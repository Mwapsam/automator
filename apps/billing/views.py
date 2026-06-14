import json
import logging
import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings

from .models import Plan, Subscription, UsageSummary
from .flutterwave import FlutterwaveError, get_fw_client

logger = logging.getLogger(__name__)


@staff_member_required(login_url="/auth/login/")
def pricing_page(request):
    import json
    from apps.whatsapp.models import BitrixAccount

    plans = list(Plan.objects.filter(is_active=True).order_by("price_monthly"))
    accounts = list(BitrixAccount.objects.select_related("subscription__plan").order_by("company_name"))

    preselected_id = request.GET.get("account")
    try:
        preselected_id = int(preselected_id) if preselected_id else None
    except (ValueError, TypeError):
        preselected_id = None

    accounts_json = json.dumps([
        {"id": a.pk, "label": a.company_name or a.domain}
        for a in accounts
    ])

    return render(request, "billing/plans.html", {
        "plans": plans,
        "accounts": accounts,
        "accounts_json": accounts_json,
        "preselected_id": preselected_id or (accounts[0].pk if len(accounts) == 1 else None),
    })


@staff_member_required(login_url="/auth/login/")
def checkout(request):
    plan_slug = request.GET.get("plan")
    account_id = request.GET.get("account")

    plan = get_object_or_404(Plan, slug=plan_slug, is_active=True)
    if plan.slug == Plan.TRIAL:
        messages.error(request, "Trial plan cannot be purchased.")
        return redirect("/billing/plans/")

    from apps.whatsapp.models import BitrixAccount
    account = get_object_or_404(BitrixAccount, pk=account_id)

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
        from apps.whatsapp.models import BitrixAccount
        account = BitrixAccount.objects.get(pk=account_id)
        plan = Plan.objects.get(slug=plan_slug)
    except Exception as exc:
        logger.error("callback: account/plan lookup failed: %s", exc)
        messages.error(request, "Subscription activation failed. Contact support.")
        return redirect("/tenants/")

    now = timezone.now()
    Subscription.objects.update_or_create(
        bitrix_account=account,
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
    return redirect("/tenants/")


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
            bitrix_account_id=account_id
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
    updated = Subscription.objects.filter(bitrix_account_id=account_id).update(
        status=Subscription.CANCELLED,
        cancelled_at=now,
    )
    if updated:
        logger.info("_handle_subscription_cancelled: cancelled subscription for account=%s", account_id)
