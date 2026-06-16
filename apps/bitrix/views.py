import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from b24pysdk.credentials import OAuthEventData, OAuthPlacementData
from b24pysdk.errors import BitrixSDKException

from apps.accounts.utils import get_current_account
from apps.bitrix.models import BitrixConnection
from .auth import exchange_code, get_authorization_url
from .client import BitrixClient

logger = logging.getLogger(__name__)

_INSTALL_FINISHED_HTML = (
    "<head><script src='//api.bitrix24.com/api/v1/'></script>"
    "<script>BX24.init(function(){BX24.installFinish();});</script></head>"
    "<body>installation has been finished</body>"
)


def _expires_at(oauth_token):
    """Resolve an absolute expiry datetime from an SDK OAuth token."""
    if oauth_token.expires:
        return oauth_token.expires
    return timezone.now() + timedelta(seconds=oauth_token.expires_in or 3600)


def _label_for(domain, access_token):
    """Best-effort connected-user label; never blocks the save."""
    try:
        profile = BitrixClient(domain, access_token).call("profile")
        if profile:
            logger.info("Bitrix connected: %s (%s)", domain, profile.get("EMAIL"))
    except BitrixSDKException:
        logger.exception("Bitrix profile lookup failed for %s", domain)


def _save_connection(account, domain, access_token, refresh_token, expires_at):
    """Persist (or refresh) the BitrixConnection for ``account``."""
    _label_for(domain, access_token)
    connection, _ = BitrixConnection.objects.update_or_create(
        account=account,
        defaults={
            "domain": domain,
            "client_id": settings.BITRIX_CLIENT_ID,
            "client_secret": settings.BITRIX_CLIENT_SECRET,
            "access_token": access_token,
            "refresh_token": refresh_token or "",
            "expires_at": expires_at,
        },
    )
    return connection


def _update_connection_by_domain(domain, access_token, refresh_token, expires_at):
    """Server-to-server path: refresh tokens for an already-connected portal."""
    try:
        connection = BitrixConnection.objects.get(domain=domain)
    except BitrixConnection.DoesNotExist:
        logger.warning(
            "Bitrix install event for unknown domain %s — no account to attach to. "
            "Connect the portal from the dashboard first.", domain,
        )
        return None
    connection.access_token = access_token
    connection.refresh_token = refresh_token or connection.refresh_token
    connection.expires_at = expires_at
    connection.save(update_fields=["access_token", "refresh_token", "expires_at"])
    return connection


@login_required
def connect(request):
    """Kick off the OAuth authorization-code flow (portal authorize page)."""
    return redirect(get_authorization_url())


@login_required
def callback(request):
    """OAuth authorization-code redirect handler — binds the portal to the account."""
    account = get_current_account(request)
    if account is None:
        return JsonResponse({"error": "No account for current user"}, status=400)

    code = request.GET.get("code")
    if not code:
        return JsonResponse({"error": "Missing authorization code"}, status=400)

    try:
        data = exchange_code(code)
    except Exception:
        logger.exception("Bitrix token exchange failed")
        return JsonResponse({"error": "Token exchange failed"}, status=502)

    domain = data.get("domain") or data.get("client_endpoint", "").split("/")[2]
    expires_at = timezone.now() + timedelta(seconds=int(data["expires_in"]))
    _save_connection(
        account,
        domain,
        data["access_token"],
        data.get("refresh_token", ""),
        expires_at,
    )
    return redirect("/dashboard/")


@csrf_exempt
def install(request):
    """Single Bitrix handler URL for install + events (server-to-server).

    Tokens are refreshed for the portal's existing BitrixConnection (matched by
    domain). Initial connection must be made interactively from the dashboard so
    the portal can be attached to the right Account.
    """
    params = request.POST.dict() or request.GET.dict()

    if (params.get("event") or "").upper() == "ONAPPINSTALL":
        try:
            event_data = OAuthEventData.from_dict(params)
        except OAuthEventData.ValidationError:
            logger.exception("Invalid ONAPPINSTALL payload")
            return JsonResponse({"error": "Invalid install event"}, status=401)

        auth = event_data.auth
        if auth and auth.oauth_token:
            _update_connection_by_domain(
                auth.domain,
                auth.oauth_token.access_token,
                auth.oauth_token.refresh_token,
                _expires_at(auth.oauth_token),
            )
        return JsonResponse({"success": True})

    if not ("PLACEMENT" in params or "AUTH_ID" in params):
        logger.info(
            "Bitrix install handler probed without payload (%s)", request.method
        )
        return HttpResponse("Bitrix handler ready")

    try:
        placement = OAuthPlacementData.from_dict(params)
    except OAuthPlacementData.ValidationError:
        logger.error("Invalid Bitrix placement payload: %s", list(params))
        return JsonResponse({"error": "Invalid install request"}, status=400)

    oauth_token = placement.oauth_token
    _update_connection_by_domain(
        placement.domain,
        oauth_token.access_token,
        oauth_token.refresh_token,
        _expires_at(oauth_token),
    )
    return HttpResponse(_INSTALL_FINISHED_HTML)
