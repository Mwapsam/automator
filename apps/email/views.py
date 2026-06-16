import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.accounts.utils import get_current_account
from apps.email.mailcow import MailcowClient, MailcowError
from apps.email.models import EmailApiKey, EmailDomain, EmailMessage
from apps.email.tasks import send_email

logger = logging.getLogger(__name__)


# --- Dashboard (server-rendered, account-scoped) ------------------------------

@login_required
def domains_list(request):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")

    domains = EmailDomain.objects.filter(account=account)
    api_key = EmailApiKey.objects.filter(account=account, is_active=True).first()
    recent = EmailMessage.objects.filter(account=account)[:25]
    return render(request, "email/domains.html", {
        "account": account,
        "domains": domains,
        "api_key": api_key,
        "recent": recent,
    })


@login_required
@require_POST
def domain_create(request):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")

    domain = (request.POST.get("domain") or "").strip().lower()
    if not domain:
        messages.error(request, "Domain is required.")
        return redirect("email-domains")

    if EmailDomain.objects.filter(domain=domain).exists():
        messages.error(request, "That domain is already registered.")
        return redirect("email-domains")

    record = EmailDomain.objects.create(account=account, domain=domain)
    try:
        client = MailcowClient()
        result = client.provision_sending_domain(domain, selector=record.dkim_selector)
        record.dkim_public_key = result.get("dkim_txt", "")
        record.dkim_selector = result.get("selector", record.dkim_selector)
        record.save(update_fields=["dkim_public_key", "dkim_selector"])
        messages.success(
            request,
            f"{domain} provisioned. Add the DNS records below, then verify.",
        )
    except MailcowError as exc:
        record.status = EmailDomain.Status.FAILED
        record.save(update_fields=["status"])
        logger.error("domain_create: Mailcow error for %s: %s", domain, exc)
        messages.error(request, f"Mailcow provisioning failed: {exc}")
    return redirect("email-domains")


@login_required
@require_POST
def domain_verify(request, pk):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")

    record = get_object_or_404(EmailDomain, pk=pk, account=account)
    try:
        dkim = MailcowClient().get_dkim(record.domain) or {}
        if dkim.get("dkim_txt"):
            record.dkim_public_key = dkim["dkim_txt"]
            record.dkim_ok = True
            record.status = EmailDomain.Status.VERIFIED
            record.verified_at = timezone.now()
            record.save(update_fields=[
                "dkim_public_key", "dkim_ok", "status", "verified_at",
            ])
            messages.success(request, f"{record.domain} verified.")
        else:
            messages.error(request, "DKIM not found on Mailcow yet.")
    except MailcowError as exc:
        messages.error(request, f"Verification failed: {exc}")
    return redirect("email-domains")


@login_required
@require_POST
def key_create(request):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")
    EmailApiKey.objects.filter(account=account).update(is_active=False)
    EmailApiKey.objects.create(account=account, name="default")
    messages.success(request, "New API key generated.")
    return redirect("email-domains")


# --- Transactional send API ---------------------------------------------------

def _authenticate(request):
    """Resolve the EmailApiKey from the request, or return None."""
    header = request.headers.get("X-Api-Key") or ""
    if not header:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            header = auth.removeprefix("Bearer ").strip()
    if not header:
        return None
    return EmailApiKey.objects.filter(key=header, is_active=True).select_related(
        "account"
    ).first()


@csrf_exempt
@require_POST
def api_send(request):
    api_key = _authenticate(request)
    if api_key is None:
        return JsonResponse({"error": "Invalid or missing API key"}, status=401)

    account = api_key.account

    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    from_email = (body.get("from") or "").strip()
    to_email = (body.get("to") or "").strip()
    subject = body.get("subject") or ""
    text_body = body.get("text") or ""
    html_body = body.get("html") or ""

    if not from_email or not to_email:
        return JsonResponse({"error": "'from' and 'to' are required"}, status=400)

    from_domain = from_email.rsplit("@", 1)[-1].lower()
    domain = EmailDomain.objects.filter(
        account=account, domain=from_domain, status=EmailDomain.Status.VERIFIED
    ).first()
    if domain is None:
        return JsonResponse(
            {"error": f"'{from_domain}' is not a verified sending domain for this account"},
            status=403,
        )

    from apps.billing.limits import LimitChecker, PlanLimitExceeded
    try:
        LimitChecker(account).check_email()
    except PlanLimitExceeded as exc:
        return JsonResponse({"error": str(exc)}, status=403)

    msg = EmailMessage.objects.create(
        account=account,
        domain=domain,
        from_email=from_email,
        to_email=to_email,
        subject=subject,
    )
    api_key.touch()
    transaction.on_commit(
        lambda: send_email.delay(msg.id, text_body=text_body, html_body=html_body)
    )
    return JsonResponse({"id": msg.id, "status": msg.status}, status=202)
