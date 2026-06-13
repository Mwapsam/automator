import json
import logging
from functools import wraps

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.conf import settings


def _admin_api(view_func):
    """Require is_staff for JSON API endpoints; return 403 instead of redirecting."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            return JsonResponse({"error": "Forbidden"}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper

from apps.whatsapp.models import BitrixAccount
from apps.whatsapp.models.tenant import WhatsAppBusinessNumber

logger = logging.getLogger(__name__)


@staff_member_required(login_url="/auth/login/")
def tenants_page(request):
    return render(request, "bitrix/tenants.html")


def _account_json(account):
    return {
        "id": account.pk,
        "company_name": account.company_name,
        "domain": account.domain,
        "is_active": account.is_active,
        "token_ok": not account.token_needs_refresh,
        "created_at": account.created_at.date().isoformat(),
    }


def _number_json(num):
    return {
        "id": num.pk,
        "phone_number_id": num.phone_number_id,
        "waba_id": num.waba_id or "",
        "display_number": num.display_number or "",
        "is_active": num.is_active,
    }


def _parse(request):
    return json.loads(request.body)


@_admin_api
@require_http_methods(["GET", "POST"])
def tenants_api(request):
    if request.method == "GET":
        accounts = BitrixAccount.objects.order_by("-created_at")
        return JsonResponse([_account_json(a) for a in accounts], safe=False)

    body = _parse(request)
    account = BitrixAccount.objects.create(
        company_name=body.get("company_name", ""),
        domain=body.get("domain", ""),
        client_id=getattr(settings, "BITRIX_CLIENT_ID", ""),
        client_secret=getattr(settings, "BITRIX_CLIENT_SECRET", ""),
        access_token="",
        refresh_token="",
        expires_at=timezone.now(),
    )
    return JsonResponse(_account_json(account), status=201)


@_admin_api
@require_http_methods(["GET", "PATCH", "DELETE"])
def tenant_detail_api(request, pk):
    try:
        account = BitrixAccount.objects.get(pk=pk)
    except BitrixAccount.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    if request.method == "GET":
        return JsonResponse(_account_json(account))

    if request.method == "PATCH":
        body = _parse(request)
        fields = []
        if "company_name" in body:
            account.company_name = body["company_name"]
            fields.append("company_name")
        if "is_active" in body:
            account.is_active = body["is_active"]
            fields.append("is_active")
        if fields:
            account.save(update_fields=fields)
        return JsonResponse(_account_json(account))

    account.delete()
    return JsonResponse({}, status=204)


@_admin_api
@require_http_methods(["GET", "POST"])
def tenant_numbers_api(request, pk):
    try:
        account = BitrixAccount.objects.get(pk=pk)
    except BitrixAccount.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    if request.method == "GET":
        numbers = account.whatsapp_numbers.order_by("phone_number_id")
        return JsonResponse([_number_json(n) for n in numbers], safe=False)

    body = _parse(request)
    phone_number_id = body.get("phone_number_id", "").strip()
    if not phone_number_id:
        return JsonResponse({"error": "phone_number_id is required"}, status=400)

    number, created = WhatsAppBusinessNumber.objects.get_or_create(
        bitrix_account=account,
        phone_number_id=phone_number_id,
        defaults={
            "waba_id": body.get("waba_id") or None,
            "display_number": body.get("display_number") or None,
        },
    )
    if not created:
        return JsonResponse({"error": "This phone_number_id is already registered"}, status=409)
    return JsonResponse(_number_json(number), status=201)


@_admin_api
@require_http_methods(["PATCH", "DELETE"])
def tenant_number_detail_api(request, pk, num_pk):
    try:
        number = WhatsAppBusinessNumber.objects.get(pk=num_pk, bitrix_account_id=pk)
    except WhatsAppBusinessNumber.DoesNotExist:
        return JsonResponse({"error": "Not found"}, status=404)

    if request.method == "PATCH":
        body = _parse(request)
        if "is_active" in body:
            number.is_active = body["is_active"]
            number.save(update_fields=["is_active"])
        return JsonResponse(_number_json(number))

    number.delete()
    return JsonResponse({}, status=204)
