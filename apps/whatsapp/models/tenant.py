from django.db import models

from apps.whatsapp.models import BitrixAccount


class WhatsAppBusinessNumber(models.Model):
    """
    Maps a WhatsApp number (phone_number_id from Meta) to a BitrixAccount.
    A tenant may have multiple WhatsApp numbers for different regions,
    departments, etc.
    """

    bitrix_account = models.ForeignKey(
        BitrixAccount,
        on_delete=models.CASCADE,
        related_name="whatsapp_numbers",
    )

    phone_number_id = models.CharField(max_length=50, unique=True, db_index=True)
    waba_id = models.CharField(max_length=50, blank=True, null=True)
    display_number = models.CharField(max_length=20, blank=True, null=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("bitrix_account", "phone_number_id")
        indexes = [
            models.Index(fields=["phone_number_id"]),
            models.Index(fields=["is_active", "phone_number_id"]),
        ]

    def __str__(self):
        disp = f" ({self.display_number})" if self.display_number else ""
        return f"{self.bitrix_account.company_name}: {self.phone_number_id}{disp}"


class TenantResolutionError(Exception):
    """Raised when a webhook event cannot be mapped to a tenant."""
    pass


def get_account_for_webhook(phone_number_id: str) -> BitrixAccount:
    """
    Resolve a WhatsApp phone_number_id (from the webhook) to its tenant.

    Args:
        phone_number_id: Meta's phone number ID (from webhook.changes[0].value.metadata.phone_number_id)

    Returns:
        BitrixAccount instance.

    Raises:
        TenantResolutionError: if the number is not registered or is inactive.

    Usage:
        def process_whatsapp_event(event_id):
            event = WebhookEventLog.objects.get(id=event_id)
            phone_number_id = event.payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]
            try:
                account = get_account_for_webhook(phone_number_id)
            except TenantResolutionError as e:
                # log and mark event as failed - webhook came from an unconfigured number
                event.mark_failed(str(e))
                return
    """
    try:
        whatsapp_num = WhatsAppBusinessNumber.objects.get(
            phone_number_id=phone_number_id, is_active=True
        )
        return whatsapp_num.bitrix_account
    except WhatsAppBusinessNumber.DoesNotExist:
        raise TenantResolutionError(
            f"No active WhatsAppBusinessNumber found for phone_number_id={phone_number_id}. "
            "Check that the number has been registered in the dashboard."
        )