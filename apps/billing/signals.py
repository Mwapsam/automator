import logging
from datetime import timedelta

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)


@receiver(post_save, sender="accounts.Account")
def auto_create_trial(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from apps.billing.models import Plan, Subscription
        from apps.core.models import SiteSettings

        site = SiteSettings.load()
        plan = site.default_plan or Plan.objects.filter(slug=Plan.TRIAL).first()
        if plan is None:
            return
        trial_days = plan.trial_days or site.default_trial_days
        now = timezone.now()
        Subscription.objects.get_or_create(
            account=instance,
            defaults={
                "plan": plan,
                "status": Subscription.TRIALING if trial_days else Subscription.ACTIVE,
                "trial_ends_at": now + timedelta(days=trial_days) if trial_days else None,
                "current_period_start": now,
            },
        )
    except Exception:
        logger.exception(
            "auto_create_trial: failed to create trial subscription for account %s",
            instance.pk,
        )
