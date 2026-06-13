from django.db import models

from .account import BitrixAccount


class AutomationRule(models.Model):
    class TriggerEvent(models.TextChoices):
        MESSAGE_RECEIVED = "message_received", "Message received"
        MESSAGE_SENT = "message_sent", "Message sent"
        LEAD_CREATED = "lead_created", "Lead created"
        DEAL_STAGE_CHANGED = "deal_stage_changed", "Deal stage changed"

    bitrix_account = models.ForeignKey(BitrixAccount, on_delete=models.CASCADE)

    name = models.CharField(max_length=255)
    trigger_event = models.CharField(max_length=50, choices=TriggerEvent.choices)

    conditions = models.JSONField(default=dict)
    action = models.JSONField(default=dict)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["bitrix_account", "trigger_event", "is_active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.trigger_event})"
