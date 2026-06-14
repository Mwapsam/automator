import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def expire_trials():
    from apps.billing.models import Subscription

    count = Subscription.objects.filter(
        status=Subscription.TRIALING,
        trial_ends_at__lte=timezone.now(),
    ).update(status=Subscription.EXPIRED)

    if count:
        logger.info("expire_trials: expired %s trial subscriptions", count)


@shared_task
def reset_monthly_usage():
    """
    UsageSummary rows are created on demand with period_start=first_of_month,
    so no reset is needed — new month = new row. This task is a heartbeat.
    """
    logger.info("reset_monthly_usage: heartbeat at %s", timezone.now().date())
