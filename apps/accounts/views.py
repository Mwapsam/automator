import logging

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from apps.accounts.forms import SignupForm
from apps.accounts.models import Account, Membership
from apps.accounts.utils import get_current_account, set_current_account

logger = logging.getLogger(__name__)


def signup(request):
    """Self-service signup: create a User, an Account, and an owner Membership.

    The trial Subscription is created by the billing post_save signal on Account.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save(commit=False)
                user.email = form.cleaned_data["email"]
                user.save()

                account = Account.objects.create(
                    company_name=form.cleaned_data["company_name"],
                )
                Membership.objects.create(
                    user=user, account=account, role=Membership.Role.OWNER
                )

            login(request, user)
            set_current_account(request, account)
            logger.info("signup: created account %s for user %s", account.pk, user.pk)
            return redirect("dashboard")
    else:
        form = SignupForm()

    return render(request, "accounts/signup.html", {"form": form})


@login_required
def dashboard(request):
    account = get_current_account(request)
    if account is None:
        # Authenticated user with no tenant (e.g. a staff-only admin user).
        return render(request, "accounts/dashboard.html", {"account": None})

    from apps.whatsapp.models.tenant import WhatsAppBusinessNumber

    numbers = WhatsAppBusinessNumber.objects.filter(account=account).order_by(
        "phone_number_id"
    )

    email_domains = []
    try:
        from apps.email.models import EmailDomain

        email_domains = list(
            EmailDomain.objects.filter(account=account).order_by("domain")
        )
    except Exception:
        pass

    subscription = getattr(account, "subscription", None)
    bitrix_connection = getattr(account, "bitrix_connection", None)

    return render(
        request,
        "accounts/dashboard.html",
        {
            "account": account,
            "numbers": numbers,
            "email_domains": email_domains,
            "subscription": subscription,
            "bitrix_connection": bitrix_connection,
        },
    )
