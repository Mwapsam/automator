import phonenumbers
from django.core.exceptions import ValidationError
from django.db import models, transaction

from .account import BitrixAccount


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
