import logging

from celery import shared_task

from apps.email.models import EmailMessage
from apps.email.services import smtp_send

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3


@shared_task(bind=True, max_retries=_MAX_ATTEMPTS, default_retry_delay=60)
def send_email(self, email_message_id: int, text_body: str = "", html_body: str = ""):
    """Send a queued EmailMessage and record the outcome.

    The body is passed as task args (and thus preserved across retries) rather
    than persisted on the log row.
    """
    try:
        msg = EmailMessage.objects.select_related("account").get(pk=email_message_id)
    except EmailMessage.DoesNotExist:
        logger.error("send_email: EmailMessage %s not found", email_message_id)
        return

    if msg.status == EmailMessage.Status.SENT:
        return

    try:
        message_id = smtp_send(
            from_email=msg.from_email,
            to_email=msg.to_email,
            subject=msg.subject,
            text_body=text_body,
            html_body=html_body,
        )
        msg.mark_sent(message_id)

        # Count successful sends against the monthly quota.
        try:
            from apps.billing.models import UsageSummary
            UsageSummary.increment_emails(msg.account)
        except Exception as exc:
            logger.debug("send_email: usage increment skipped: %s", exc)
    except Exception as exc:
        msg.mark_failed(str(exc))
        logger.exception("send_email: failed for EmailMessage %s", email_message_id)
        raise self.retry(exc=exc)
