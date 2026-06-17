import json
import logging
import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings

from apps.accounts.utils import get_current_account
from .models import Plan, Subscription, UsageSummary
from .flutterwave import FlutterwaveError, get_fw_client

logger = logging.getLogger(__name__)


@login_required
def pricing_page(request):
    is_admin = request.user.is_superuser
    account = get_current_account(request)
    if account is None and not is_admin:
        return redirect("/dashboard/")

    # Admins see every package (incl. inactive) to manage; tenants see only active.
    plan_qs = Plan.objects.all() if is_admin else Plan.objects.filter(is_active=True)
    plans = list(plan_qs.order_by("price_monthly"))
    subscription = getattr(account, "subscription", None) if account else None
    current_plan_slug = subscription.plan.slug if subscription else None

    return render(request, "billing/plans.html", {
        "plans": plans,
        "account": account,
        "is_admin": is_admin,
        "subscription": subscription,
        "current_plan_slug": current_plan_slug,
        "conversations_used": UsageSummary.get_current_usage(account) if account else 0,
        "emails_used": UsageSummary.get_current_email_usage(account) if account else 0,
    })


# --- Admin: package (Plan) management -----------------------------------------

def _plan_form_fields(post):
    """Pull + coerce Plan fields from POST (shared by create/edit)."""
    def _int(name, default=0):
        try:
            return int(post.get(name, default) or default)
        except ValueError:
            return default
    from decimal import Decimal, InvalidOperation
    try:
        price = Decimal(post.get("price_monthly") or "0")
    except InvalidOperation:
        price = Decimal("0")
    return {
        "name": (post.get("name") or "").strip(),
        "price_monthly": price,
        "max_conversations_per_month": _int("max_conversations_per_month"),
        "max_emails_per_month": _int("max_emails_per_month"),
        "max_mailboxes": _int("max_mailboxes"),
        "mailbox_storage_gb": _int("mailbox_storage_gb"),
        "max_forwarding_rules": _int("max_forwarding_rules"),
        "max_aliases": _int("max_aliases"),
        "max_automation_rules": _int("max_automation_rules"),
        "max_whatsapp_numbers": _int("max_whatsapp_numbers"),
        "trial_days": _int("trial_days"),
        "log_retention_days": _int("log_retention_days"),
        "flutterwave_plan_id": (post.get("flutterwave_plan_id") or "").strip() or None,
        "has_priority_support": "has_priority_support" in post,
        "email_apis": "email_apis" in post,
        "inbound_email": "inbound_email" in post,
        "tracking_webhooks": "tracking_webhooks" in post,
        "detailed_analytics": "detailed_analytics" in post,
        "is_active": "is_active" in post,
    }


@login_required
@require_POST
def plan_create(request):
    if not request.user.is_superuser:
        return redirect("/billing/plans/")
    from django.utils.text import slugify
    fields = _plan_form_fields(request.POST)
    slug = slugify(request.POST.get("slug") or fields["name"])
    if not slug or not fields["name"]:
        messages.error(request, "Package name (and slug) are required.")
        return redirect("billing:plans")
    if Plan.objects.filter(slug=slug).exists():
        messages.error(request, f"A package with slug '{slug}' already exists.")
        return redirect("billing:plans")
    Plan.objects.create(slug=slug, **fields)
    messages.success(request, f"Package '{fields['name']}' created.")
    return redirect("billing:plans")


@login_required
@require_POST
def plan_edit(request, pk):
    if not request.user.is_superuser:
        return redirect("/billing/plans/")
    plan = get_object_or_404(Plan, pk=pk)
    fields = _plan_form_fields(request.POST)
    if not fields["name"]:
        messages.error(request, "Package name is required.")
        return redirect("billing:plans")
    for k, v in fields.items():
        setattr(plan, k, v)
    plan.save()
    messages.success(request, f"Package '{plan.name}' updated.")
    return redirect("billing:plans")


@login_required
@require_POST
def plan_toggle(request, pk):
    if not request.user.is_superuser:
        return redirect("/billing/plans/")
    plan = get_object_or_404(Plan, pk=pk)
    plan.is_active = not plan.is_active
    plan.save(update_fields=["is_active"])
    messages.success(
        request, f"Package '{plan.name}' {'activated' if plan.is_active else 'deactivated'}."
    )
    return redirect("billing:plans")


@login_required
@require_POST
def plan_delete(request, pk):
    if not request.user.is_superuser:
        return redirect("/billing/plans/")
    plan = get_object_or_404(Plan, pk=pk)
    if plan.subscriptions.exists():
        messages.error(
            request,
            f"Can't delete '{plan.name}' — customers are subscribed. Deactivate it instead.",
        )
        return redirect("billing:plans")
    name = plan.name
    plan.delete()
    messages.success(request, f"Package '{name}' deleted.")
    return redirect("billing:plans")


@login_required
def checkout(request):
    account = get_current_account(request)
    if account is None:
        return redirect("/dashboard/")

    plan_slug = request.GET.get("plan")
    plan = get_object_or_404(Plan, slug=plan_slug, is_active=True)
    if plan.slug == Plan.TRIAL:
        messages.error(request, "Trial plan cannot be purchased.")
        return redirect("/billing/plans/")

    tx_ref = f"sub_{account.pk}_{plan.slug}_{uuid.uuid4().hex[:8]}"
    currency = getattr(settings, "FLUTTERWAVE_CURRENCY", "USD")
    redirect_url = request.build_absolute_uri("/billing/callback/")

    try:
        fw = get_fw_client()
        link = fw.initialize_payment(
            tx_ref=tx_ref,
            amount=plan.price_monthly,
            currency=currency,
            customer_email=request.user.email or f"admin+{account.pk}@automator.local",
            customer_name=account.company_name or request.user.username,
            redirect_url=redirect_url,
            payment_plan_id=plan.flutterwave_plan_id,
            meta={"account_id": account.pk, "plan_slug": plan.slug},
        )
    except FlutterwaveError as exc:
        logger.error("checkout: FW error for account=%s plan=%s: %s", account.pk, plan.slug, exc)
        messages.error(request, f"Payment initialization failed: {exc}")
        return redirect("/billing/plans/")

    request.session["pending_tx_ref"] = tx_ref
    request.session["pending_account_id"] = account.pk
    request.session["pending_plan_slug"] = plan.slug

    return redirect(link)


def callback(request):
    status = request.GET.get("status")
    transaction_id = request.GET.get("transaction_id")

    if status != "successful":
        messages.error(request, "Payment was not completed successfully.")
        return redirect("/billing/plans/")

    account_id = request.session.pop("pending_account_id", None)
    plan_slug = request.session.pop("pending_plan_slug", None)
    request.session.pop("pending_tx_ref", None)

    if not account_id or not plan_slug:
        messages.error(request, "Session expired. Please try again.")
        return redirect("/billing/plans/")

    try:
        fw = get_fw_client()
        transaction = fw.verify_transaction(transaction_id)
    except FlutterwaveError as exc:
        logger.error("callback: verification failed: %s", exc)
        messages.error(request, "Payment verification failed. Contact support if charged.")
        return redirect("/billing/plans/")

    if transaction.get("status") != "successful":
        messages.error(request, "Payment could not be verified.")
        return redirect("/billing/plans/")

    try:
        from apps.accounts.models import Account
        account = Account.objects.get(pk=account_id)
        plan = Plan.objects.get(slug=plan_slug)
    except Exception as exc:
        logger.error("callback: account/plan lookup failed: %s", exc)
        messages.error(request, "Subscription activation failed. Contact support.")
        return redirect("/dashboard/")

    now = timezone.now()
    Subscription.objects.update_or_create(
        account=account,
        defaults={
            "plan": plan,
            "status": Subscription.ACTIVE,
            "current_period_start": now,
            "current_period_end": now + timedelta(days=30),
            "fw_customer_email": (transaction.get("customer") or {}).get("email"),
            "trial_ends_at": None,
            "cancelled_at": None,
        },
    )

    logger.info(
        "callback: activated %s subscription for account=%s tx=%s",
        plan.name, account.pk, transaction_id,
    )
    messages.success(request, f"Successfully subscribed to {plan.name}!")
    return redirect("/dashboard/")


@csrf_exempt
@require_POST
def webhook(request):
    verif_hash = request.headers.get("verif-hash")
    expected = getattr(settings, "FLUTTERWAVE_WEBHOOK_HASH", None)

    if not expected or verif_hash != expected:
        logger.warning("webhook: invalid verif-hash")
        return HttpResponse(status=401)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    event = payload.get("event")

    if event == "charge.completed":
        _handle_charge_completed(payload)
    elif event == "subscription.cancelled":
        _handle_subscription_cancelled(payload)
    else:
        logger.debug("webhook: unhandled event type=%s", event)

    return HttpResponse(status=200)


def _handle_charge_completed(payload: dict):
    data = payload.get("data", {})
    if data.get("status") != "successful":
        return

    meta = data.get("meta", {}) or {}
    account_id = meta.get("account_id")
    plan_slug = meta.get("plan_slug")

    if not account_id:
        logger.warning("_handle_charge_completed: no account_id in meta")
        return

    try:
        sub = Subscription.objects.select_related("plan").get(
            account_id=account_id
        )
    except Subscription.DoesNotExist:
        logger.warning("_handle_charge_completed: no subscription for account=%s", account_id)
        return

    now = timezone.now()
    sub.status = Subscription.ACTIVE
    sub.current_period_start = now
    sub.current_period_end = now + timedelta(days=30)
    sub.fw_customer_email = (data.get("customer") or {}).get("email") or sub.fw_customer_email
    sub.save(update_fields=["status", "current_period_start", "current_period_end", "fw_customer_email", "updated_at"])

    logger.info("_handle_charge_completed: renewed subscription for account=%s", account_id)


def _handle_subscription_cancelled(payload: dict):
    data = payload.get("data", {})
    meta = (data.get("meta") or {})
    account_id = meta.get("account_id")

    if not account_id:
        return

    now = timezone.now()
    updated = Subscription.objects.filter(account_id=account_id).update(
        status=Subscription.CANCELLED,
        cancelled_at=now,
    )
    if updated:
        logger.info("_handle_subscription_cancelled: cancelled subscription for account=%s", account_id)
