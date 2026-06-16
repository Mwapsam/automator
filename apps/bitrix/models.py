"""Bitrix24 is now an optional addon connected to an Account.

The OAuth credentials that used to live on the central tenant model
(``BitrixAccount``) live here, on a per-Account ``BitrixConnection``.
"""

from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.accounts.fields import EncryptedTextField


class BitrixConnection(models.Model):
    """A tenant's connection to a single Bitrix24 portal."""

    REFRESH_MARGIN = timedelta(minutes=10)

    account = models.OneToOneField(
        "accounts.Account",
        on_delete=models.CASCADE,
        related_name="bitrix_connection",
    )

    domain = models.CharField(max_length=255, unique=True)  # company.bitrix24.com

    client_id = models.CharField(max_length=255)
    client_secret = EncryptedTextField()

    access_token = EncryptedTextField()
    refresh_token = EncryptedTextField()
    expires_at = models.DateTimeField()

    webhook_url = models.URLField(blank=True, null=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["is_active", "expires_at"]),  # refresh worker
        ]

    @property
    def token_needs_refresh(self) -> bool:
        return self.expires_at <= timezone.now() + self.REFRESH_MARGIN

    def __str__(self):
        return f"{self.account.company_name} -> {self.domain}"
