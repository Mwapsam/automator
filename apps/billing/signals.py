import logging
from datetime import timedelta

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

logger = logging.getLogger(__name__)


@receiver(post_save, sender="whatsapp.BitrixAccount")
def auto_create_trial(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from apps.billing.models import Plan, Subscription

        trial_plan = Plan.objects.get(slug=Plan.TRIAL)
        now = timezone.now()
        Subscription.objects.get_or_create(
            bitrix_account=instance,
            defaults={
                "plan": trial_plan,
                "status": Subscription.TRIALING,
                "trial_ends_at": now + timedelta(days=trial_plan.trial_days),
                "current_period_start": now,
            },
        )
    except Exception:
        logger.exception(
            "auto_create_trial: failed to create trial subscription for account %s",
            instance.pk,
        )
