"""Account-scoped self-service management of WhatsApp Business numbers.

Replaces the former staff-only tenant CRUD: an account owner registers and
manages their own numbers (phone_number_id + access token) from the dashboard.
"""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.utils import get_current_account
from apps.whatsapp.models.tenant import WhatsAppBusinessNumber

logger = logging.getLogger(__name__)


@login_required
def numbers_list(request):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")

    numbers = WhatsAppBusinessNumber.objects.filter(account=account).order_by(
        "phone_number_id"
    )
    return render(
        request,
        "whatsapp/numbers.html",
        {"account": account, "numbers": numbers},
    )


@login_required
def numbers_create(request):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")

    if request.method != "POST":
        return redirect("whatsapp-numbers")

    phone_number_id = (request.POST.get("phone_number_id") or "").strip()
    access_token = (request.POST.get("access_token") or "").strip()
    if not phone_number_id:
        messages.error(request, "phone_number_id is required.")
        return redirect("whatsapp-numbers")

    from apps.billing.limits import LimitChecker, PlanLimitExceeded

    try:
        LimitChecker(account).check_whatsapp_number()
    except PlanLimitExceeded as exc:
        messages.error(request, str(exc))
        return redirect("whatsapp-numbers")

    if WhatsAppBusinessNumber.objects.filter(phone_number_id=phone_number_id).exists():
        messages.error(request, "This phone_number_id is already registered.")
        return redirect("whatsapp-numbers")

    WhatsAppBusinessNumber.objects.create(
        account=account,
        phone_number_id=phone_number_id,
        access_token=access_token or None,
        waba_id=(request.POST.get("waba_id") or "").strip() or None,
        business_id=(request.POST.get("business_id") or "").strip() or None,
        display_number=(request.POST.get("display_number") or "").strip() or None,
    )
    messages.success(request, f"WhatsApp number {phone_number_id} registered.")
    return redirect("whatsapp-numbers")


@login_required
def numbers_delete(request, pk):
    account = get_current_account(request)
    if account is None:
        return redirect("dashboard")
    if request.method != "POST":
        return redirect("whatsapp-numbers")

    number = get_object_or_404(WhatsAppBusinessNumber, pk=pk, account=account)
    number.delete()
    messages.success(request, "WhatsApp number removed.")
    return redirect("whatsapp-numbers")
