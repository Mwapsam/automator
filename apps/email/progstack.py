"""Thin client for the Progstack domain-verification API.

Progstack handles domain *ownership* verification independently of iRedMail's
DKIM provisioning: it issues a TXT token the customer adds to their DNS, then
checks for it. Auth is a per-account static token sent on the ``X-API-Token``
header (no login round-trip, unlike ``apps.email.iredmail``).

Flow:
    client = ProgstackClient(account.progstack_token)
    rec = client.generate(domain)   # -> {"name": ..., "value": ...}; show in DNS
    res = client.check(domain)      # -> {"verified": bool, "message": str}
    st  = client.status(domain)     # -> current verification status
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 15


class ProgstackError(Exception):
    pass


class ProgstackClient:
    def __init__(self, token: str | None, base_url: str | None = None):
        self.base_url = (base_url or settings.PROGSTACK_API_BASE or "").rstrip("/")
        self.token = token or ""
        if not self.base_url:
            raise ProgstackError("PROGSTACK_API_BASE must be configured.")
        if not self.token:
            raise ProgstackError("No Progstack API token set for this account.")

    def _headers(self) -> dict:
        return {"X-API-Token": self.token, "Content-Type": "application/json"}

    def _request(self, method: str, path: str) -> dict:
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), timeout=_TIMEOUT
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            detail = ""
            resp_obj = getattr(exc, "response", None)
            if resp_obj is not None:
                try:
                    detail = resp_obj.json().get("message") or resp_obj.json().get("error")
                except ValueError:
                    detail = resp_obj.text
            raise ProgstackError(f"{method} {path} failed: {detail or exc}") from exc
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    @staticmethod
    def _normalize_record(record) -> dict:
        """Coerce the ``generate`` record into ``{"name", "value"}``.

        The API may return a bare TXT string or a structured object; tolerate
        both so the page can always show a Name/Value pair.
        """
        if isinstance(record, str):
            return {"name": "", "value": record}
        if isinstance(record, dict):
            return {
                "name": record.get("name") or record.get("host") or "",
                "value": record.get("value") or record.get("txt") or record.get("record") or "",
            }
        return {"name": "", "value": ""}

    # --- verification ---------------------------------------------------

    def generate(self, domain: str) -> dict:
        """Generate the verification TXT token and return ``{"name", "value"}``."""
        data = self._request("POST", f"domain/{domain}/verify/generate")
        return self._normalize_record(data.get("record", data))

    def check(self, domain: str) -> dict:
        """Check DNS and mark verified. Returns ``{"verified": bool, "message": str}``."""
        data = self._request("POST", f"domain/{domain}/verify/check")
        return {
            "verified": bool(data.get("verified")),
            "message": data.get("message") or "",
        }

    def status(self, domain: str) -> dict:
        """Return the current verification status."""
        return self._request("GET", f"domain/{domain}/verify/status")
