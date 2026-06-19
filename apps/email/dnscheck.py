"""Live DNS verification for sending domains.

Queries the customer's published TXT records and reports, per record, whether
the expected value is present. Self-hosted (dnspython) — no external API or
per-account token. ``_resolve_txt`` is the single network seam, so tests
monkeypatch it instead of hitting real DNS.
"""
import logging
import re

import dns.resolver

logger = logging.getLogger(__name__)

# Keep checks snappy — DNS that isn't there yet should fail fast, not hang the
# request or the auto-poll.
_LIFETIME = 5.0


def _resolve_txt(name: str) -> list[str]:
    """Return the TXT values published at ``name`` (each fully concatenated)."""
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=_LIFETIME)
    except Exception as exc:  # NXDOMAIN, NoAnswer, Timeout, NoNameservers, …
        logger.debug("TXT lookup for %s failed: %s", name, exc)
        return []
    values = []
    for rdata in answers:
        # A TXT rdata is one or more byte strings; join them into the real value.
        parts = [
            p.decode("utf-8", "ignore") if isinstance(p, bytes) else str(p)
            for p in rdata.strings
        ]
        values.append("".join(parts))
    return values


def _norm(s: str) -> str:
    """Lower-case and strip all whitespace — for tolerant value comparison."""
    return re.sub(r"\s+", "", s or "").lower()


def _contains(name: str, needle: str) -> bool:
    if not needle:
        return False
    target = _norm(needle)
    return any(target in _norm(v) for v in _resolve_txt(name))


def _dkim_public_key(value: str) -> str:
    """Extract the base64 ``p=`` portion of a DKIM TXT value, if present."""
    m = re.search(r"p=([A-Za-z0-9+/=]+)", value or "")
    return m.group(1) if m else ""


def check_domain(record) -> dict:
    """Check every DNS record for ``record`` and return ``{key: found_bool}``.

    ``key`` is one of ``verify``, ``dkim``, ``spf``, ``dmarc`` (matching
    ``EmailDomain.dns_records()``).
    """
    results = {}

    # Ownership: our verification token must be published verbatim.
    results["verify"] = _contains(
        record.verify_record_name or record.domain, record.verify_record_value
    )

    # DKIM: the published record must carry our public key (the p= base64).
    dkim_key = _dkim_public_key(record.dkim_txt_value)
    results["dkim"] = bool(dkim_key) and _contains(record.dkim_record_name, dkim_key)

    # SPF: must be an spf1 record including our mail host (or at least spf1).
    from django.conf import settings
    host = settings.EMAIL_HOST or ""
    spf_records = _resolve_txt(record.domain)
    spf_present = any(_norm(v).startswith("v=spf1") for v in spf_records)
    if host:
        results["spf"] = any(
            _norm(v).startswith("v=spf1") and _norm("include:" + host) in _norm(v)
            for v in spf_records
        )
    else:
        results["spf"] = spf_present

    # DMARC: any valid DMARC1 policy at _dmarc.<domain>.
    results["dmarc"] = any(
        _norm(v).startswith("v=dmarc1") for v in _resolve_txt(record.dmarc_record_name)
    )

    return results
