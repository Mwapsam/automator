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

from apps.accounts.models import Account
from apps.accounts.utils import get_current_account
from apps.email.iredmail import IRedMailClient, IRedMailError
from apps.email.progstack import ProgstackClient, ProgstackError
from apps.email.models import (
    EmailAlias,
    EmailApiKey,
    EmailDomain,
    EmailMessage,
    Mailbox,
)
from apps.email.tasks import provision_mailbox, send_email

logger = logging.getLogger(__name__)


def _is_admin(request) -> bool:
    """Superusers manage every tenant's email resources without restriction.

    (Proper role-based access control will replace this blanket check later.)
    """
    return bool(getattr(request.user, "is_superuser", False))


def _scoped(manager, request, account):
    """Limit a queryset to ``account``, or return all rows for an admin."""
    qs = manager.all()
    if not _is_admin(request):
        qs = qs.filter(account=account)
    return qs


# --- AJAX helpers -------------------------------------------------------------
# Views below are progressively enhanced: an XHR (from static/js/app.js) gets a
# rendered partial + an `X-Toast` header; a normal POST keeps the redirect flow,
# so everything still works with JavaScript disabled.

def is_ajax(request) -> bool:
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _toast(response, kind: str, message: str):
    # URL-encode the message so the header stays ASCII (avoids RFC 2047
    # encoding of non-ASCII chars); app.js decodeURIComponent()s it back.
    from urllib.parse import quote
    response["X-Toast"] = f"{kind}|{quote(message)}"
    return response


def _ajax_error(message: str, status: int = 400):
    return JsonResponse({"error": message}, status=status)


def _domain_card(request, record):
    from django.conf import settings
    return render(request, "email/_domain_card.html", {
        "d": record,
        "is_admin": _is_admin(request),
        "mail_host": settings.EMAIL_HOST or "YOUR_MAIL_HOST",
    })


def _mailbox_row(request, mb):
    return render(request, "email/_mailbox_row.html", {
        "mb": mb, "is_admin": _is_admin(request),
    })


# --- Dashboard (server-rendered, account-scoped) ------------------------------

@login_required
def domains_list(request):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    domains = _scoped(EmailDomain.objects, request, account).select_related("account")
    recent = _scoped(EmailMessage.objects, request, account)[:25]
    api_key = (
        EmailApiKey.objects.filter(account=account, is_active=True).first()
        if account
        else None
    )
    from django.conf import settings
    from apps.billing.limits import LimitChecker

    # The API + SMTP relay is a plan capability; admins always see it.
    email_apis_enabled = admin or (account and LimitChecker(account).has_feature("email_apis"))

    return render(request, "email/domains.html", {
        "account": account,
        "is_admin": admin,
        "domains": domains,
        "api_key": api_key,
        "recent": recent,
        "email_apis_enabled": email_apis_enabled,
        "mail_host": settings.EMAIL_HOST or "YOUR_MAIL_HOST",
        # Admins choose which tenant a new domain belongs to.
        "accounts": Account.objects.order_by("company_name") if admin else None,
    })


@login_required
@require_POST
def domain_create(request):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    ajax = is_ajax(request)
    domain = (request.POST.get("domain") or "").strip().lower()
    if not domain:
        if ajax:
            return _ajax_error("Domain is required.")
        messages.error(request, "Domain is required.")
        return redirect("email-domains")

    if EmailDomain.objects.filter(domain=domain).exists():
        msg = "That domain is already registered."
        if ajax:
            return _ajax_error(msg)
        messages.error(request, msg)
        return redirect("email-domains")

    # An admin without their own workspace picks which tenant owns the domain.
    if account is None:
        account = Account.objects.filter(pk=request.POST.get("account_id")).first()
        if account is None:
            msg = "Select an account to attach this domain to."
            if ajax:
                return _ajax_error(msg)
            messages.error(request, msg)
            return redirect("email-domains")

    record = EmailDomain.objects.create(account=account, domain=domain)
    kind, message = "success", f"{domain} provisioned — add the DNS records, then verify."
    try:
        client = IRedMailClient()
        result = client.provision_sending_domain(domain, selector=record.dkim_selector)
        record.dkim_public_key = result.get("dkim_txt", "")
        record.dkim_selector = result.get("selector", record.dkim_selector)
        record.save(update_fields=["dkim_public_key", "dkim_selector"])
    except IRedMailError as exc:
        record.status = EmailDomain.Status.FAILED
        record.save(update_fields=["status"])
        logger.error("domain_create: mail server error for %s: %s", domain, exc)
        kind, message = "danger", f"Provisioning failed: {exc}"

    # Fetch the Progstack ownership-verification TXT record so the customer can
    # add it alongside DKIM/SPF/DMARC. Best-effort: a missing token or API error
    # shouldn't fail provisioning — they can set the token and re-verify later.
    if kind != "danger":
        ok, note = _generate_verify_record(record)
        if not ok and note:
            message = f"{message} {note}"

    if ajax:
        return _toast(_domain_card(request, record), kind, message)
    messages.add_message(
        request, messages.SUCCESS if kind == "success" else messages.ERROR, message
    )
    return redirect("email-domains")


@login_required
@require_POST
def domain_verify(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    record = get_object_or_404(_scoped(EmailDomain.objects, request, account), pk=pk)
    ajax = is_ajax(request)

    # No verification record yet (e.g. token was added after provisioning) —
    # generate it now so the customer has a TXT to add, then ask them to retry.
    if not record.verify_record_value:
        ok, note = _generate_verify_record(record)
        kind = "warning" if ok else "danger"
        message = note or "Add the verification TXT record, then verify."
        if ajax:
            return _toast(_domain_card(request, record), kind, message)
        messages.add_message(
            request, messages.SUCCESS if kind == "success" else messages.ERROR, message
        )
        return redirect("email-domains")

    kind, message = "success", f"{record.domain} verified."
    try:
        result = ProgstackClient(record.account.progstack_token).check(record.domain)
        if result["verified"]:
            record.status = EmailDomain.Status.VERIFIED
            record.verified_at = timezone.now()
            record.save(update_fields=["status", "verified_at"])
        else:
            kind = "warning"
            message = result["message"] or "Verification TXT record not found in DNS yet."
    except ProgstackError as exc:
        kind, message = "danger", f"Verification failed: {exc}"

    if ajax:
        return _toast(_domain_card(request, record), kind, message)
    messages.add_message(
        request, messages.SUCCESS if kind == "success" else messages.ERROR, message
    )
    return redirect("email-domains")


def _generate_verify_record(record) -> tuple[bool, str]:
    """Fetch and store the Progstack ownership TXT record for ``record``.

    Returns ``(ok, note)``: ``ok`` is False when no token is configured or the
    API errors. Never raises — callers fold the note into their toast/message.
    """
    if not record.account.progstack_token:
        return False, "Set this account's Progstack API token to verify domains."
    try:
        rec = ProgstackClient(record.account.progstack_token).generate(record.domain)
    except ProgstackError as exc:
        logger.error("generate verify record for %s: %s", record.domain, exc)
        return False, f"Could not generate verification record: {exc}"
    record.verify_record_name = rec.get("name", "")
    record.verify_record_value = rec.get("value", "")
    record.save(update_fields=["verify_record_name", "verify_record_value"])
    return True, ""


@login_required
@require_POST
def progstack_token_set(request):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")
    account.progstack_token = (request.POST.get("token") or "").strip()
    account.save(update_fields=["progstack_token"])
    if account.progstack_token:
        messages.success(request, "Progstack API token saved.")
    else:
        messages.success(request, "Progstack API token cleared.")
    return redirect("email-domains")


@login_required
@require_POST
def domain_toggle(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    record = get_object_or_404(_scoped(EmailDomain.objects, request, account), pk=pk)
    ajax = is_ajax(request)
    new_active = not record.is_active
    try:
        IRedMailClient().set_domain_status(record.domain, new_active)
        record.is_active = new_active
        record.save(update_fields=["is_active"])
        kind = "success"
        message = f"{record.domain} {'enabled' if new_active else 'disabled'}."
    except IRedMailError as exc:
        kind, message = "danger", f"Could not update {record.domain}: {exc}"

    if ajax:
        return _toast(_domain_card(request, record), kind, message)
    messages.add_message(
        request, messages.SUCCESS if kind == "success" else messages.ERROR, message
    )
    return redirect("email-domains")


@login_required
@require_POST
def domain_delete(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    record = get_object_or_404(_scoped(EmailDomain.objects, request, account), pk=pk)
    ajax = is_ajax(request)
    try:
        IRedMailClient().delete_domain(record.domain)
    except IRedMailError as exc:
        msg = f"Could not delete {record.domain}: {exc}"
        if ajax:
            return _ajax_error(msg)
        messages.error(request, msg)
        return redirect("email-domains")
    domain_name = record.domain
    record.delete()
    if ajax:
        from django.http import HttpResponse
        return _toast(HttpResponse(""), "success", f"{domain_name} deleted.")
    messages.success(request, f"{domain_name} deleted.")
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


# --- Insights: delivery logs + open/click analytics ---------------------------

@login_required
def insights(request):
    from apps.billing.limits import LimitChecker

    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    has_analytics = admin or (account and LimitChecker(account).has_feature("detailed_analytics"))
    domains = list(
        _scoped(EmailDomain.objects, request, account).filter(
            status=EmailDomain.Status.VERIFIED
        )
    )
    domain_names = [d.domain for d in domains]
    selected = request.GET.get("domain") or (domain_names[0] if domain_names else "")

    logs, stats, error = [], [], None
    if has_analytics and selected and selected in domain_names:
        try:
            client = IRedMailClient()
            logs = client.domain_logs(selected, limit=100) or []
            stats = client.domain_stats(selected) or []
        except IRedMailError as exc:
            error = str(exc)

    return render(request, "email/insights.html", {
        "account": account,
        "is_admin": admin,
        "has_analytics": has_analytics,
        "domains": domains,
        "selected": selected,
        "logs": logs,
        "stats": stats,
        "error": error,
    })


# --- Mailboxes & aliases ------------------------------------------------------

@login_required
def mailbox_list(request):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    domains = _scoped(EmailDomain.objects, request, account).filter(
        status=EmailDomain.Status.VERIFIED
    )
    mailboxes = _scoped(Mailbox.objects, request, account).select_related("domain")
    aliases = _scoped(EmailAlias.objects, request, account).select_related("domain")
    return render(request, "email/mailboxes.html", {
        "account": account,
        "is_admin": admin,
        "domains": domains,
        "mailboxes": mailboxes,
        "aliases": aliases,
    })


@login_required
@require_POST
def mailbox_create(request):
    admin = _is_admin(request)
    current = get_current_account(request)
    if current is None and not admin:
        return redirect("dashboard")

    ajax = is_ajax(request)
    email = (request.POST.get("email") or "").strip().lower()
    password = request.POST.get("password") or ""
    name = (request.POST.get("name") or "").strip()
    try:
        quota_mb = int(request.POST.get("quota_mb") or 1024)
    except ValueError:
        quota_mb = 1024

    def fail(msg):
        if ajax:
            return _ajax_error(msg)
        messages.error(request, msg)
        return redirect("email-mailboxes")

    if not email or not password:
        return fail("Email and password are required.")

    domain_part = email.rsplit("@", 1)[-1]
    domain = _scoped(EmailDomain.objects, request, current).filter(
        domain=domain_part, status=EmailDomain.Status.VERIFIED
    ).first()
    if domain is None:
        return fail(f"'{domain_part}' is not a verified sending domain.")

    # The mailbox belongs to whichever tenant owns the domain.
    account = domain.account

    if Mailbox.objects.filter(email=email).exists():
        return fail("That mailbox already exists.")

    note = ""
    if not admin:
        from apps.billing.limits import LimitChecker, PlanLimitExceeded
        lc = LimitChecker(account)
        try:
            lc.check_mailbox()
        except PlanLimitExceeded as exc:
            return fail(str(exc))
        cap = lc.mailbox_storage_cap_mb()
        if cap and quota_mb > cap:
            quota_mb = cap
            note = f" (storage capped at {cap} MB by your plan)"

    mb = Mailbox.objects.create(
        account=account, domain=domain, email=email, name=name, quota_mb=quota_mb
    )
    transaction.on_commit(lambda: provision_mailbox.delay(mb.id, password))
    msg = f"Provisioning {email}{note}…"
    if ajax:
        return _toast(_mailbox_row(request, mb), "success", msg)
    messages.success(request, msg)
    return redirect("email-mailboxes")


@login_required
@require_POST
def mailbox_delete(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    mb = get_object_or_404(_scoped(Mailbox.objects, request, account), pk=pk)
    ajax = is_ajax(request)
    email = mb.email
    try:
        IRedMailClient().delete_mailbox(mb.email)
        mb.delete()
    except IRedMailError as exc:
        if ajax:
            return _ajax_error(f"Delete failed: {exc}")
        messages.error(request, f"Delete failed: {exc}")
        return redirect("email-mailboxes")
    if ajax:
        from django.http import HttpResponse
        return _toast(HttpResponse(""), "success", f"{email} deleted.")
    messages.success(request, "Mailbox deleted.")
    return redirect("email-mailboxes")


@login_required
@require_POST
def mailbox_password(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    mb = get_object_or_404(_scoped(Mailbox.objects, request, account), pk=pk)
    ajax = is_ajax(request)
    password = request.POST.get("password") or ""
    if not password:
        if ajax:
            return _ajax_error("Password is required.")
        messages.error(request, "Password is required.")
        return redirect("email-mailboxes")
    try:
        IRedMailClient().change_password(mb.email, password)
    except IRedMailError as exc:
        if ajax:
            return _ajax_error(f"Password change failed: {exc}")
        messages.error(request, f"Password change failed: {exc}")
        return redirect("email-mailboxes")
    if ajax:
        from django.http import HttpResponse
        return _toast(HttpResponse(""), "success", f"Password updated for {mb.email}.")
    messages.success(request, f"Password updated for {mb.email}.")
    return redirect("email-mailboxes")


@login_required
@require_POST
def mailbox_quota(request, pk):
    admin = _is_admin(request)
    account = get_current_account(request)
    if account is None and not admin:
        return redirect("dashboard")

    mb = get_object_or_404(_scoped(Mailbox.objects, request, account), pk=pk)
    ajax = is_ajax(request)
    try:
        quota_mb = int(request.POST.get("quota_mb") or mb.quota_mb)
    except ValueError:
        if ajax:
            return _ajax_error("Quota must be a number (MB).")
        messages.error(request, "Quota must be a number (MB).")
        return redirect("email-mailboxes")
    # Tenants are capped by their plan's per-mailbox storage; admins are not.
    note = ""
    if not admin:
        from apps.billing.limits import LimitChecker
        cap = LimitChecker(mb.account).mailbox_storage_cap_mb()
        if cap and quota_mb > cap:
            quota_mb = cap
            note = f" (capped at {cap} MB by plan)"
    try:
        IRedMailClient().update_quota(mb.email, quota_mb)
        mb.quota_mb = quota_mb
        mb.save(update_fields=["quota_mb"])
    except IRedMailError as exc:
        if ajax:
            return _ajax_error(f"Quota update failed: {exc}")
        messages.error(request, f"Quota update failed: {exc}")
        return redirect("email-mailboxes")
    if ajax:
        return _toast(_mailbox_row(request, mb), "success", f"Quota updated for {mb.email}{note}.")
    messages.success(request, f"Quota updated for {mb.email}{note}.")
    return redirect("email-mailboxes")


@login_required
@require_POST
def alias_create(request):
    admin = _is_admin(request)
    current = get_current_account(request)
    if current is None and not admin:
        return redirect("dashboard")

    ajax = is_ajax(request)
    address = (request.POST.get("address") or "").strip().lower()
    goto = (request.POST.get("goto") or "").strip().lower()

    def fail(msg):
        if ajax:
            return _ajax_error(msg)
        messages.error(request, msg)
        return redirect("email-mailboxes")

    if not address or not goto:
        return fail("Both the alias and forwarding address are required.")

    domain_part = address.rsplit("@", 1)[-1]
    domain = _scoped(EmailDomain.objects, request, current).filter(
        domain=domain_part, status=EmailDomain.Status.VERIFIED
    ).first()
    if domain is None:
        return fail(f"'{domain_part}' is not a verified sending domain.")

    if not admin:
        from apps.billing.limits import LimitChecker, PlanLimitExceeded
        try:
            LimitChecker(domain.account).check_alias()
        except PlanLimitExceeded as exc:
            return fail(str(exc))

    try:
        IRedMailClient().add_alias(address, goto)
        alias = EmailAlias.objects.create(
            account=domain.account, domain=domain, address=address, goto=goto
        )
    except IRedMailError as exc:
        return fail(f"Alias creation failed: {exc}")

    if ajax:
        resp = render(request, "email/_alias_row.html", {"a": alias, "is_admin": admin})
        return _toast(resp, "success", f"Alias {address} → {goto} created.")
    messages.success(request, f"Alias {address} → {goto} created.")
    return redirect("email-mailboxes")


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
    lc = LimitChecker(account)
    # Gate the API + SMTP relay capability by plan.
    if not lc.has_feature("email_apis"):
        return JsonResponse(
            {"error": "Your plan does not include the email API & SMTP relay. Upgrade to send."},
            status=403,
        )
    try:
        lc.check_email()
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
