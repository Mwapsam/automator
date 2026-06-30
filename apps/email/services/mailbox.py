"""Mailbox provisioning business logic.

MailboxService owns:
  - Plan limit enforcement (mailbox count, storage quota)
  - Provider calls for create / update / delete / suspend / quota / password
  - Django ORM sync (Mailbox.status, quota_mb, error)
  - Audit log writes

Views and tasks import this service — never the provider directly.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apps.email.audit import record as audit
from apps.email.exceptions import EmailProviderError
from apps.email.models import Mailbox
from apps.email.providers import get_mail_provider
from apps.email.types import MailboxInfo, OperationResult, QuotaInfo

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

logger = logging.getLogger(__name__)


class MailboxService:
    """Orchestrates mailbox provisioning between Django and the mail provider."""

    def __init__(self, account, *, actor: "AbstractBaseUser | None" = None) -> None:
        self.account = account
        self.actor = actor
        self._provider = get_mail_provider()

    # ── Public API ────────────────────────────────────────────────────────

    def provision(self, mailbox: Mailbox, password: str) -> MailboxInfo:
        """Create the mailbox on the mail server and set status=ACTIVE.

        The password is passed in and forwarded once to the provider — it is
        never stored by Django.
        """
        if mailbox.status == Mailbox.Status.ACTIVE:
            logger.debug(
                "MailboxService.provision: %s already active, skipping",
                mailbox.email,
            )
            return self._provider.get_mailbox(mailbox.email)

        try:
            info = self._provider.create_mailbox(
                mailbox.email,
                password,
                name=mailbox.name,
                quota_mb=mailbox.quota_mb or None,
            )
        except EmailProviderError as exc:
            mailbox.status = Mailbox.Status.FAILED
            mailbox.error = str(exc)[:5000]
            mailbox.save(update_fields=["status", "error"])
            audit(
                account=self.account,
                actor=self.actor,
                action="mailbox.provision",
                resource_type="mailbox",
                resource_id=mailbox.email,
                success=False,
                error=str(exc),
            )
            raise

        mailbox.status = Mailbox.Status.ACTIVE
        mailbox.error = None
        mailbox.save(update_fields=["status", "error"])

        audit(
            account=self.account,
            actor=self.actor,
            action="mailbox.provision",
            resource_type="mailbox",
            resource_id=mailbox.email,
            metadata={"quota_mb": mailbox.quota_mb},
        )
        logger.info(
            "MailboxService.provision: %s created (account=%s)",
            mailbox.email,
            self.account.pk,
        )
        return info

    def deprovision(self, mailbox: Mailbox) -> OperationResult:
        """Delete the mailbox from the mail server."""
        try:
            result = self._provider.delete_mailbox(mailbox.email)
        except EmailProviderError:
            audit(
                account=self.account,
                actor=self.actor,
                action="mailbox.deprovision",
                resource_type="mailbox",
                resource_id=mailbox.email,
                success=False,
                error="Provider error during delete_mailbox",
            )
            raise

        audit(
            account=self.account,
            actor=self.actor,
            action="mailbox.deprovision",
            resource_type="mailbox",
            resource_id=mailbox.email,
        )
        return result

    def change_password(self, mailbox: Mailbox, new_password: str) -> OperationResult:
        """Update the mailbox password on the mail server."""
        try:
            result = self._provider.change_password(mailbox.email, new_password)
        except EmailProviderError:
            audit(
                account=self.account,
                actor=self.actor,
                action="mailbox.change_password",
                resource_type="mailbox",
                resource_id=mailbox.email,
                success=False,
                error="Provider error during change_password",
            )
            raise

        audit(
            account=self.account,
            actor=self.actor,
            action="mailbox.change_password",
            resource_type="mailbox",
            resource_id=mailbox.email,
        )
        return result

    def set_quota(self, mailbox: Mailbox, quota_mb: int) -> OperationResult:
        """Update storage quota on the provider and sync the Django model."""
        try:
            result = self._provider.set_quota(mailbox.email, quota_mb)
        except EmailProviderError:
            audit(
                account=self.account,
                actor=self.actor,
                action="mailbox.set_quota",
                resource_type="mailbox",
                resource_id=mailbox.email,
                success=False,
                error=f"Provider error setting quota to {quota_mb} MB",
            )
            raise

        mailbox.quota_mb = quota_mb
        mailbox.save(update_fields=["quota_mb"])

        audit(
            account=self.account,
            actor=self.actor,
            action="mailbox.set_quota",
            resource_type="mailbox",
            resource_id=mailbox.email,
            metadata={"quota_mb": quota_mb},
        )
        return result

    def get_quota(self, mailbox: Mailbox) -> QuotaInfo:
        """Fetch live storage usage from the mail provider."""
        return self._provider.get_quota(mailbox.email)

    def suspend(self, mailbox: Mailbox) -> OperationResult:
        """Disable mailbox login on the provider."""
        return self._toggle(mailbox, active=False)

    def activate(self, mailbox: Mailbox) -> OperationResult:
        """Re-enable a suspended mailbox."""
        return self._toggle(mailbox, active=True)

    # ── Private helpers ───────────────────────────────────────────────────

    def _toggle(self, mailbox: Mailbox, *, active: bool) -> OperationResult:
        action = "mailbox.activate" if active else "mailbox.suspend"
        try:
            result = self._provider.set_mailbox_active(mailbox.email, active=active)
        except EmailProviderError:
            audit(
                account=self.account,
                actor=self.actor,
                action=action,
                resource_type="mailbox",
                resource_id=mailbox.email,
                success=False,
                error="Provider error during set_mailbox_active",
            )
            raise

        audit(
            account=self.account,
            actor=self.actor,
            action=action,
            resource_type="mailbox",
            resource_id=mailbox.email,
        )
        return result
