"""Transactional email sending via the Mailcow SMTP relay."""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

logger = logging.getLogger(__name__)


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
