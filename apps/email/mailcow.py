"""Thin client for the Mailcow admin API.

Used to provision a tenant's sending domain and its DKIM key, and to read back
the DKIM record we surface for the tenant to add to DNS. Mirrors the small
``requests`` + timeout style of ``apps.bitrix.auth``.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 15


class MailcowError(Exception):
    pass


class MailcowClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or settings.MAILCOW_API_BASE or "").rstrip("/")
        self.api_key = api_key or settings.MAILCOW_API_KEY
        if not self.base_url or not self.api_key:
            raise MailcowError(
                "MAILCOW_API_BASE and MAILCOW_API_KEY must be configured."
            )

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}/api/v1/{path.lstrip('/')}"
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        # Mailcow returns a list of {"type": "success"|"danger", "msg": ...}
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("type") == "danger":
                raise MailcowError(str(item.get("msg")))
        return data

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}/api/v1/{path.lstrip('/')}"
        resp = requests.get(url, headers=self._headers(), timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    # --- domains ---------------------------------------------------------

    def add_domain(self, domain: str, description: str = "") -> dict:
        return self._post("add/domain", {
            "domain": domain,
            "description": description or domain,
            "active": "1",
            "rl_value": "100",
            "rl_frame": "h",
            "backupmx": "0",
        })

    def generate_dkim(self, domain: str, selector: str = "dkim", key_size: int = 2048) -> dict:
        return self._post("add/dkim", {
            "domains": domain,
            "dkim_selector": selector,
            "key_size": key_size,
        })

    def get_dkim(self, domain: str) -> dict:
        """Return Mailcow's DKIM info for a domain (incl. the TXT record value)."""
        return self._get(f"get/dkim/{domain}")

    def provision_sending_domain(self, domain: str, selector: str = "dkim") -> dict:
        """Add the domain + DKIM and return the DKIM record value for DNS.

        Returns ``{"dkim_txt": ..., "selector": ...}``.
        """
        self.add_domain(domain)
        self.generate_dkim(domain, selector=selector)
        dkim = self.get_dkim(domain) or {}
        return {
            "dkim_txt": dkim.get("dkim_txt", ""),
            "selector": dkim.get("dkim_selector") or selector,
        }
