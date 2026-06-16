"""Wiring between the project's BitrixConnection records and b24pysdk.

Centralises construction of the OAuth app/token objects so every call site
gets automatic token refresh, and so refreshed tokens (and portal domain
changes) are persisted back to the database.
"""

import logging
from functools import lru_cache

from django.conf import settings

from b24pysdk import BitrixApp, BitrixToken

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_bitrix_app() -> BitrixApp:
    """The mass-market application credentials (cached for app.info reuse)."""
    return BitrixApp(
        client_id=settings.BITRIX_CLIENT_ID,
        client_secret=settings.BITRIX_CLIENT_SECRET,
    )


def _bind_persistence(token: BitrixToken, connection) -> None:
    """Persist token refreshes / domain changes emitted by the SDK back to DB."""

    def _on_token_renewed(event):
        oauth_token = event.renewed_oauth_token.oauth_token
        connection.access_token = oauth_token.access_token
        if oauth_token.refresh_token:
            connection.refresh_token = oauth_token.refresh_token
        if oauth_token.expires:
            connection.expires_at = oauth_token.expires
        connection.save(
            update_fields=["access_token", "refresh_token", "expires_at"]
        )
        logger.info("Bitrix token refreshed for %s", connection.domain)

    def _on_domain_changed(event):
        connection.domain = event.new_domain
        connection.save(update_fields=["domain"])
        logger.info(
            "Bitrix portal domain changed: %s -> %s",
            event.old_domain,
            event.new_domain,
        )

    token.oauth_token_renewed_signal.connect(_on_token_renewed)
    token.portal_domain_changed_signal.connect(_on_domain_changed)


def token_for_connection(connection) -> BitrixToken:
    """Build a BitrixToken for a BitrixConnection, wired to persist refreshes."""
    token = BitrixToken(
        domain=connection.domain,
        auth_token=connection.access_token,
        refresh_token=connection.refresh_token,
        expires=connection.expires_at,
        bitrix_app=get_bitrix_app(),
    )
    _bind_persistence(token, connection)
    return token
