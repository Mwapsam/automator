from datetime import timedelta

import phonenumbers
from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone


def _fernet() -> MultiFernet:
    keys = getattr(settings, "FIELD_ENCRYPTION_KEYS", None)
    if not keys:
        single = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
        if not single:
            raise ValidationError(
                "Set FIELD_ENCRYPTION_KEYS (list, newest first) in settings."
            )
        keys = [single]
    return MultiFernet([Fernet(k) for k in keys])


class EncryptedTextField(models.TextField):

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        return _fernet().encrypt(str(value).encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except Exception:
            raise ValidationError(
                "Could not decrypt field value - check FIELD_ENCRYPTION_KEYS "
                "(is the original key still present in the list?)."
            )



def normalize_phone(raw: str, default_region: str = "ZM") -> str:
    if not raw:
        raise ValidationError("Empty phone number.")
    raw = raw.strip()
    # WhatsApp Cloud API sends wa_id without '+', e.g. '260971234567'
    if not raw.startswith("+") and len(raw) > 9 and not raw.startswith("0"):
        raw = "+" + raw
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException as exc:
        raise ValidationError(f"Unparseable phone number: {raw}") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValidationError(f"Invalid phone number: {raw}")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)



class BitrixAccount(models.Model):
    company_name = models.CharField(max_length=255)
    domain = models.CharField(max_length=255, unique=True)  # company.bitrix24.com

    client_id = models.CharField(max_length=255)
    client_secret = EncryptedTextField()

    access_token = EncryptedTextField()
    refresh_token = EncryptedTextField()
    expires_at = models.DateTimeField()

    webhook_url = models.URLField(blank=True, null=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    REFRESH_MARGIN = timedelta(minutes=10)

    @property
    def token_needs_refresh(self) -> bool:
        return self.expires_at <= timezone.now() + self.REFRESH_MARGIN

    class Meta:
        indexes = [
            models.Index(fields=["is_active", "expires_at"]),  # refresh worker
        ]

    def __str__(self):
        return self.company_name


class WhatsAppContact(models.Model):
    bitrix_account = models.ForeignKey(
        BitrixAccount, on_delete=models.CASCADE, related_name="contacts"
    )

    phone_number = models.CharField(max_length=20, db_index=True)

    display_name = models.CharField(max_length=255, blank=True, null=True)

    last_message_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("bitrix_account", "phone_number")

    def save(self, *args, **kwargs):
        self.phone_number = normalize_phone(self.phone_number)
        super().save(*args, **kwargs)

    # -- CRM binding helpers (see CrmBinding below) --------------------

    @property
    def primary_binding(self):
        return self.crm_bindings.filter(is_primary=True).first()

    def binding_for(self, entity_type: str):
        return self.crm_bindings.filter(entity_type=entity_type).first()

    def __str__(self):
        return f"{self.phone_number} ({self.bitrix_account.company_name})"


class CrmBinding(models.Model):
    class EntityType(models.TextChoices):
        LEAD = "lead", "Lead"
        CONTACT = "contact", "Contact"
        DEAL = "deal", "Deal"

    bitrix_account = models.ForeignKey(BitrixAccount, on_delete=models.CASCADE)
    contact = models.ForeignKey(
        WhatsAppContact, on_delete=models.CASCADE, related_name="crm_bindings"
    )

    entity_type = models.CharField(max_length=20, choices=EntityType.choices)
    entity_id = models.CharField(max_length=50)

    is_primary = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["bitrix_account", "entity_type", "entity_id", "contact"],
                name="unique_binding_per_entity",
            ),
            models.UniqueConstraint(
                fields=["contact"],
                condition=models.Q(is_primary=True),
                name="one_primary_binding_per_contact",
            ),
        ]
        indexes = [
            models.Index(fields=["bitrix_account", "entity_type", "entity_id"]),
        ]

    @transaction.atomic
    def make_primary(self):
        """Promote this binding (e.g. after Lead -> Deal conversion)."""
        CrmBinding.objects.filter(
            contact=self.contact, is_primary=True
        ).exclude(pk=self.pk).update(is_primary=False)
        self.is_primary = True
        self.save(update_fields=["is_primary"])

    def __str__(self):
        flag = " *" if self.is_primary else ""
        return f"{self.contact.phone_number} -> {self.entity_type}:{self.entity_id}{flag}"



class Conversation(models.Model):
    SESSION_GAP = timedelta(hours=24)          # WhatsApp send window
    SESSION_CLOSE_GRACE = timedelta(hours=24)  # extra time before worker closes

    bitrix_account = models.ForeignKey(BitrixAccount, on_delete=models.CASCADE)
    contact = models.ForeignKey(
        WhatsAppContact, on_delete=models.CASCADE, related_name="conversations"
    )

    crm_binding = models.ForeignKey(
        CrmBinding, on_delete=models.SET_NULL, blank=True, null=True
    )

    is_open = models.BooleanField(default=True)
    closed_at = models.DateTimeField(blank=True, null=True)

    # Refreshed on every INBOUND message.
    window_expires_at = models.DateTimeField(blank=True, null=True)

    last_message_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["contact", "is_open"]),
            models.Index(fields=["is_open", "window_expires_at"]),  # closer worker
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["bitrix_account", "contact"],
                condition=models.Q(is_open=True),
                name="one_open_conversation_per_contact",
            ),
        ]

    @property
    def window_is_open(self) -> bool:
        """True if we may still send freeform (non-template) messages."""
        return bool(
            self.window_expires_at and self.window_expires_at > timezone.now()
        )

    def register_inbound(self, at):
        """Call on every inbound message: refreshes the 24h send window."""
        self.last_message_at = at
        self.window_expires_at = at + self.SESSION_GAP
        self.save(update_fields=["last_message_at", "window_expires_at"])

    def close(self):
        self.is_open = False
        self.closed_at = timezone.now()
        self.save(update_fields=["is_open", "closed_at"])

    @classmethod
    def get_or_open(cls, contact: "WhatsAppContact") -> "Conversation":
        with transaction.atomic():
            convo = (
                cls.objects.select_for_update()
                .filter(contact=contact, is_open=True)
                .order_by("-created_at")
                .first()
            )
            if convo:
                return convo
            try:
                with transaction.atomic():  # savepoint for the create
                    return cls.objects.create(
                        bitrix_account=contact.bitrix_account,
                        contact=contact,
                        crm_binding=contact.primary_binding,
                    )
            except IntegrityError:
                # Lost the race - another worker just created it.
                return cls.objects.get(contact=contact, is_open=True)


class MessageLog(models.Model):

    class Direction(models.TextChoices):
        INBOUND = "in", "Inbound"
        OUTBOUND = "out", "Outbound"

    class MessageType(models.TextChoices):
        TEXT = "text", "Text"
        IMAGE = "image", "Image"
        AUDIO = "audio", "Audio"          # voice notes arrive as audio
        VIDEO = "video", "Video"
        DOCUMENT = "document", "Document"
        STICKER = "sticker", "Sticker"
        LOCATION = "location", "Location"
        CONTACTS = "contacts", "Contacts"
        TEMPLATE = "template", "Template"  # outbound template sends
        UNKNOWN = "unknown", "Unknown"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"

    _STATUS_RANK = {
        Status.QUEUED: 0,
        Status.SENT: 1,
        Status.DELIVERED: 2,
        Status.READ: 3,
        Status.FAILED: 99,
    }

    bitrix_account = models.ForeignKey(BitrixAccount, on_delete=models.CASCADE)
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    contact = models.ForeignKey(WhatsAppContact, on_delete=models.CASCADE)

    direction = models.CharField(max_length=10, choices=Direction.choices)

    message_id = models.CharField(max_length=255, blank=True, null=True)

    message_type = models.CharField(
        max_length=20, choices=MessageType.choices, default=MessageType.TEXT
    )

    content = models.TextField(blank=True, default="")

    media_id = models.CharField(max_length=255, blank=True, null=True)
    media_url = models.URLField(max_length=1000, blank=True, null=True)
    media_mime_type = models.CharField(max_length=100, blank=True, null=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED
    )

    timestamp = models.DateTimeField()  

    bitrix_activity_id = models.CharField(max_length=50, blank=True, null=True)

    raw_payload = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["timestamp"]),
            models.Index(fields=["conversation", "timestamp"]),
            models.Index(fields=["contact", "timestamp", "direction"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["bitrix_account", "message_id"],
                condition=models.Q(message_id__isnull=False),
                name="unique_message_per_account",
            ),
        ]

    def apply_status_update(self, new_status: str) -> bool:
        current = self._STATUS_RANK.get(self.status, 0)
        incoming = self._STATUS_RANK.get(new_status)
        if incoming is None or incoming <= current:
            return False
        self.status = new_status
        self.save(update_fields=["status"])
        return True

    def __str__(self):
        return f"[{self.direction}] {self.contact.phone_number} @ {self.timestamp:%Y-%m-%d %H:%M}"


class OutboundMessage(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    MAX_ATTEMPTS = 5

    bitrix_account = models.ForeignKey(BitrixAccount, on_delete=models.CASCADE)
    contact = models.ForeignKey(WhatsAppContact, on_delete=models.CASCADE)

    template = models.ForeignKey(
        "MessageTemplate", blank=True, null=True, on_delete=models.SET_NULL
    )

    payload = models.JSONField()

    idempotency_key = models.CharField(max_length=255, blank=True, null=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUEUED
    )

    scheduled_at = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True, null=True)

    message_log = models.OneToOneField(
        MessageLog, blank=True, null=True, on_delete=models.SET_NULL,
        related_name="outbound_source",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "scheduled_at"]),
            models.Index(fields=["bitrix_account", "status"]),  # per-tenant rate limit
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["bitrix_account", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="unique_outbound_idempotency",
            ),
        ]

    def mark_failed(self, error: str):
        self.attempts += 1
        self.last_error = error[:5000]
        if self.attempts >= self.MAX_ATTEMPTS:
            self.status = self.Status.FAILED
            self.next_attempt_at = None
        else:
            self.status = self.Status.QUEUED
            self.next_attempt_at = timezone.now() + timedelta(
                minutes=2 ** (self.attempts - 1)
            )
        self.save(update_fields=[
            "attempts", "last_error", "status", "next_attempt_at",
        ])

    def __str__(self):
        return f"-> {self.contact.phone_number} [{self.status}]"


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


class MessageTemplate(models.Model):
    class ApprovalStatus(models.TextChoices):
        DRAFT = "draft", "Draft (local only)"
        PENDING = "pending", "Pending Meta approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        PAUSED = "paused", "Paused by Meta"

    class Category(models.TextChoices):
        MARKETING = "marketing", "Marketing"
        UTILITY = "utility", "Utility"
        AUTHENTICATION = "authentication", "Authentication"

    bitrix_account = models.ForeignKey(BitrixAccount, on_delete=models.CASCADE)

    name = models.CharField(max_length=255)  # internal label

    whatsapp_template_name = models.CharField(max_length=255, blank=True, null=True)
    language_code = models.CharField(max_length=10, default="en")

    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.UTILITY
    )
    approval_status = models.CharField(
        max_length=20, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT
    )

    content = models.TextField()
    variables = models.JSONField(default=list)  # ["name", "company"]

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("bitrix_account", "whatsapp_template_name", "language_code")

    @property
    def sendable_outside_window(self) -> bool:
        return self.approval_status == self.ApprovalStatus.APPROVED

    def __str__(self):
        return f"{self.name} [{self.approval_status}]"


class WebhookEventLog(models.Model):

    class Source(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        BITRIX = "bitrix", "Bitrix24"

    source = models.CharField(max_length=50, choices=Source.choices)
    event_type = models.CharField(max_length=100)

    payload = models.JSONField()

    processed = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    error_message = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [
            # Exactly what the retry/reprocessing worker queries.
            models.Index(fields=["processed", "created_at"]),
            models.Index(fields=["source", "event_type"]),
        ]

    def mark_processed(self):
        self.processed = True
        self.processed_at = timezone.now()
        self.save(update_fields=["processed", "processed_at"])

    def mark_failed(self, error: str):
        self.attempts += 1
        self.error_message = error[:5000]
        self.save(update_fields=["attempts", "error_message"])

    def __str__(self):
        return f"{self.source}:{self.event_type} ({'ok' if self.processed else 'pending'})"