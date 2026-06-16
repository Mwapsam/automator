"""Backwards-compatible facade over b24pysdk's BitrixToken.

The CRM services (contacts/deals/activities) and the automation workflows call
``BitrixClient(...).call(method, params)`` / ``.list(method, params)``. Rather
than rewrite every call site, this keeps that small interface but routes every
request through the official SDK, which gives us argument validation, retries,
unified error handling and automatic OAuth token refresh.
"""

import logging

from b24pysdk import BitrixToken

# Re-exported so existing ``except BitrixAPIError`` call sites keep working.
from b24pysdk.errors import BitrixAPIError  # noqa: F401

from .sdk import get_bitrix_app, token_for_connection

logger = logging.getLogger(__name__)

__all__ = ["BitrixClient", "BitrixAPIError"]


class BitrixClient:
    """Low-level REST client for the Bitrix24 REST API (b24pysdk facade)."""

    def __init__(
        self,
        domain: str,
        access_token: str | None = None,
        *,
        refresh_token: str | None = None,
        expires=None,
        connection=None,
    ):
        if connection is not None:
            # Refreshes are persisted back to the connection automatically.
            self._token = token_for_connection(connection)
        else:
            self._token = BitrixToken(
                domain=domain,
                auth_token=access_token,
                refresh_token=refresh_token,
                expires=expires,
                bitrix_app=get_bitrix_app(),
            )

    @classmethod
    def from_connection(cls, connection) -> "BitrixClient":
        """Build a client whose token refreshes persist to ``connection``."""
        return cls(connection.domain, connection.access_token, connection=connection)

    def call(self, method: str, params: dict | None = None):
        """Call a single REST method and return its ``result`` payload."""
        response = self._token.call_method(method, params or {})
        return response.get("result")

    def list(self, method: str, params: dict | None = None) -> list:
        """Paginate through a Bitrix list method and return all items."""
        params = dict(params or {})
        params.setdefault("start", 0)
        items: list = []
        while True:
            response = self._token.call_method(method, params)
            data = response.get("result") or []
            items.extend(data if isinstance(data, list) else [])
            next_start = response.get("next")
            if next_start is None:
                break
            params["start"] = next_start
        return items
