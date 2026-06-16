import logging

from celery import shared_task
from django.utils import timezone

from apps.whatsapp.models import WebhookEventLog
from apps.bitrix.models import BitrixConnection
from apps.bitrix.sdk import token_for_connection

logger = logging.getLogger(__name__)

_MAX_EVENT_ATTEMPTS = 3


@shared_task
def refresh_tokens():
    stale = BitrixConnection.objects.filter(
        is_active=True,
        expires_at__lte=timezone.now() + BitrixConnection.REFRESH_MARGIN,
    )
    refreshed = failed = 0
    for connection in stale:
        try:
            token = token_for_connection(connection)
            token.refresh_and_set_oauth_token()
            oauth_token = token.oauth_token
            connection.access_token = oauth_token.access_token
            if oauth_token.refresh_token:
                connection.refresh_token = oauth_token.refresh_token
            if oauth_token.expires:
                connection.expires_at = oauth_token.expires
            connection.save(
                update_fields=["access_token", "refresh_token", "expires_at"]
            )
            refreshed += 1
        except Exception as exc:
            logger.error(
                "refresh_tokens: failed for connection pk=%s: %s", connection.pk, exc
            )
            failed += 1
    if refreshed or failed:
        logger.info("refresh_tokens: refreshed=%s failed=%s", refreshed, failed)


@shared_task(bind=True, max_retries=_MAX_EVENT_ATTEMPTS, default_retry_delay=60)
def process_bitrix_webhook(self, event_id: int):
    try:
        event = WebhookEventLog.objects.get(
            pk=event_id, source=WebhookEventLog.Source.BITRIX
        )
    except WebhookEventLog.DoesNotExist:
        logger.error("process_bitrix_webhook: event %s not found", event_id)
        return

    if event.processed:
        return

    try:
        # TODO: bitrix_client.handle_event(event.event_type, event.payload)
        event.mark_processed()
    except Exception as exc:
        event.mark_failed(str(exc))
        logger.exception(
            "process_bitrix_webhook: unhandled error for event %s", event_id
        )
        raise self.retry(exc=exc)
