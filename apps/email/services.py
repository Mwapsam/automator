"""Transactional email sending via the iRedMail SMTP relay."""

import logging
import re

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

logger = logging.getLogger(__name__)

_HREF_RE = re.compile(r'href="(https?://[^"]+)"', re.IGNORECASE)
_MAX_TRACKED_LINKS = 25


def apply_tracking(html_body: str, message_id, recipient: str, domain: str) -> str:
    """Inject an open-tracking pixel and rewrite links with click-tracking URLs.

    Uses the iredmail-api ``/api/track/generate`` endpoint. Best-effort: returns
    the original HTML unchanged on any failure so tracking never blocks delivery.
    """
    if not html_body:
        return html_body
    from apps.email.iredmail import IRedMailClient, IRedMailError

    try:
        client = IRedMailClient()
        base = client.generate_tracking(message_id, recipient, domain, f"https://{domain}")
        open_pixel = (base or {}).get("open_pixel")

        seen, count = {}, 0

        def _rewrite(match):
            nonlocal count
            url = match.group(1)
            if count >= _MAX_TRACKED_LINKS:
                return match.group(0)
            if url not in seen:
                try:
                    res = client.generate_tracking(message_id, recipient, domain, url)
                    seen[url] = (res or {}).get("click_url") or url
                    count += 1
                except IRedMailError:
                    seen[url] = url
            return f'href="{seen[url]}"'

        html = _HREF_RE.sub(_rewrite, html_body)
        if open_pixel:
            pixel = (f'<img src="{open_pixel}" width="1" height="1" alt="" '
                     f'style="display:none">')
            idx = html.lower().rfind("</body>")
            html = html[:idx] + pixel + html[idx:] if idx != -1 else html + pixel
        return html
    except IRedMailError:
        return html_body
    except Exception:
        logger.exception("apply_tracking: unexpected error; sending untracked")
        return html_body


def smtp_send(
    from_email: str,
    to_email: str,
    subject: str,
    text_body: str = "",
    html_body: str = "",
) -> str:
    """Send one email through the configured Mailcow SMTP relay.

    Returns the local Message-ID. Raises on failure (caller logs/marks failed).
    """
    connection = get_connection(
        host=settings.EMAIL_HOST,
        port=settings.EMAIL_PORT,
        username=settings.EMAIL_HOST_USER,
        password=settings.EMAIL_HOST_PASSWORD,
        use_tls=settings.EMAIL_USE_TLS,
    )
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body or " ",
        from_email=from_email,
        to=[to_email],
        connection=connection,
    )
    if html_body:
        message.attach_alternative(html_body, "text/html")

    message.send(fail_silently=False)
    return message.extra_headers.get("Message-ID", "")
