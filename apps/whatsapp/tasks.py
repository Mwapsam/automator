import logging

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from apps.whatsapp.models import (
    Conversation,
    MessageLog,
    OutboundMessage,
    WebhookEventLog,
    WhatsAppContact,
)
from apps.whatsapp.models.tenant import TenantResolutionError, get_account_for_webhook

logger = logging.getLogger(__name__)

_OUTBOUND_BATCH = 50
_MEDIA_BATCH = 20
_MAX_EVENT_ATTEMPTS = 3


@shared_task(bind=True, max_retries=_MAX_EVENT_ATTEMPTS, default_retry_delay=60)
def process_whatsapp_event(self, event_id: int):
    try:
        event = WebhookEventLog.objects.get(pk=event_id)
    except WebhookEventLog.DoesNotExist:
        logger.error("process_whatsapp_event: event %s not found", event_id)
        return

    if event.processed:
        return

    try:
        if event.event_type == "message":
            _handle_inbound_message(event)
        elif event.event_type == "status":
            _handle_status_update(event)
        else:
            logger.debug(
                "process_whatsapp_event: no handler for event_type=%s", event.event_type
            )
        event.mark_processed()
    except TenantResolutionError as exc:
        logger.warning(
            "process_whatsapp_event: tenant not found for event %s: %s", event_id, exc
        )
        event.mark_failed(str(exc))
    except Exception as exc:
        event.mark_failed(str(exc))
        logger.exception("process_whatsapp_event: unhandled error for event %s", event_id)
        raise self.retry(exc=exc)


def _handle_inbound_message(event: WebhookEventLog) -> None:
    value = event.payload["entry"][0]["changes"][0]["value"]
    message = value["messages"][0]
    phone_number_id = value["metadata"]["phone_number_id"]

    account = get_account_for_webhook(phone_number_id)

    wa_id = message["from"]
    profile_name = (value.get("contacts") or [{}])[0].get("profile", {}).get("name")

    contact, _ = WhatsAppContact.objects.get_or_create(
        bitrix_account=account,
        phone_number=wa_id,
        defaults={"display_name": profile_name},
    )
    if profile_name and contact.display_name != profile_name:
        contact.display_name = profile_name
        contact.save(update_fields=["display_name"])

    msg_ts = timezone.datetime.fromtimestamp(int(message["timestamp"]), tz=timezone.utc)
    conversation = Conversation.get_or_open(contact)
    conversation.register_inbound(msg_ts)

    msg_type = message.get("type", "unknown")
    content = ""
    media_id = media_mime_type = None

    if msg_type == "text":
        content = message.get("text", {}).get("body", "")
    elif msg_type in ("image", "audio", "video", "document", "sticker"):
        block = message.get(msg_type, {})
        media_id = block.get("id")
        media_mime_type = block.get("mime_type")
        content = block.get("caption", "")
    elif msg_type == "location":
        loc = message.get("location", {})
        content = f"{loc.get('latitude')},{loc.get('longitude')}"

    valid_types = {c[0] for c in MessageLog.MessageType.choices}
    MessageLog.objects.get_or_create(
        bitrix_account=account,
        message_id=message.get("id"),
        defaults={
            "conversation": conversation,
            "contact": contact,
            "direction": MessageLog.Direction.INBOUND,
            "message_type": msg_type if msg_type in valid_types else MessageLog.MessageType.UNKNOWN,
            "content": content,
            "media_id": media_id,
            "media_mime_type": media_mime_type,
            "status": MessageLog.Status.DELIVERED,
            "timestamp": msg_ts,
            "raw_payload": event.payload,
        },
    )

    contact.last_message_at = msg_ts
    contact.save(update_fields=["last_message_at"])


def _handle_status_update(event: WebhookEventLog) -> None:
    value = event.payload["entry"][0]["changes"][0]["value"]
    status_obj = value["statuses"][0]

    message_id = status_obj.get("id")
    new_status = status_obj.get("status")  # "sent" | "delivered" | "read" | "failed"
    if not message_id or not new_status:
        return

    try:
        log = MessageLog.objects.get(
            message_id=message_id,
            direction=MessageLog.Direction.OUTBOUND,
        )
        log.apply_status_update(new_status)
    except MessageLog.DoesNotExist:
        logger.debug(
            "_handle_status_update: no outbound log for message_id=%s", message_id
        )


@shared_task
def drain_outbound_queue():
    now = timezone.now()
    due = (
        OutboundMessage.objects.filter(
            status=OutboundMessage.Status.QUEUED,
            scheduled_at__lte=now,
        )
        .filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        .select_related("bitrix_account", "contact")[:_OUTBOUND_BATCH]
    )

    sent = failed = 0
    for msg in due:
        try:
            # TODO: whatsapp_client.send(msg.bitrix_account, msg.contact, msg.payload)
            msg.status = OutboundMessage.Status.SENT
            msg.sent_at = timezone.now()
            msg.save(update_fields=["status", "sent_at"])
            sent += 1
        except Exception as exc:
            msg.mark_failed(str(exc))
            failed += 1

    if sent or failed:
        logger.info("drain_outbound_queue: sent=%s failed=%s", sent, failed)


@shared_task
def close_expired_conversations():
    expired = Conversation.objects.filter(
        is_open=True,
        window_expires_at__isnull=False,
        window_expires_at__lte=timezone.now(),
    )
    count = 0
    for convo in expired.iterator():
        convo.close()
        count += 1
    if count:
        logger.info("close_expired_conversations: closed %s conversations", count)


@shared_task
def download_media():
    pending = (
        MessageLog.objects.filter(
            direction=MessageLog.Direction.INBOUND,
            media_id__isnull=False,
            media_url__isnull=True,
        )
        .select_related("bitrix_account")[:_MEDIA_BATCH]
    )
    count = 0
    for log in pending:
        try:
            # TODO: url = whatsapp_client.get_media_url(log.bitrix_account, log.media_id)
            # log.media_url = url
            # log.save(update_fields=["media_url"])
            count += 1
        except Exception as exc:
            logger.warning(
                "download_media: failed for MessageLog pk=%s: %s", log.pk, exc
            )
    if count:
        logger.info("download_media: fetched %s media URLs", count)
