from datetime import timedelta

from cryptography.fernet import Fernet, MultiFernet
from django.core.validators import RegexValidator
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django.utils import timezone


def _fernet() -> MultiFernet:
    keys = getattr(settings, "FIELD_ENCRYPTION_KEYS", None)
    if not keys:
        single = getattr(settings, "FIELD_ENCRYPTION_KEY", None)
        if not single:
            raise ImproperlyConfigured(
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

    def to_python(self, value):
        if value is None or value == "":
            return value
        return str(value)

class BitrixAccount(models.Model):
    company_name = models.CharField(max_length=255)
    domain = models.CharField(max_length=255, 
                              unique=True,
                              validators=[
                                    RegexValidator(
                                        regex=r"^[a-zA-Z0-9-]+\.bitrix24\.[a-z.]+$"
                                    )
                                ],
                              )  

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
            models.Index(fields=["is_active", "expires_at"]),  
        ]

    def __str__(self):
        return self.company_name
