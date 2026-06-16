import requests


from urllib.parse import urlencode
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def get_authorization_url():
    portal = settings.BITRIX_PORTAL_DOMAIN
    if not portal:
        raise ImproperlyConfigured(
            "BITRIX_PORTAL_DOMAIN is not set. Set it to your portal host, "
            "e.g. mycompany.bitrix24.com (no scheme)."
        )

    params = {
        "client_id": settings.BITRIX_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.BITRIX24_OAUTH_REDIRECT_URL,
    }

    # Authorization must happen on the portal itself; oauth.bitrix.info has no
    # authorize endpoint (it returns 404) and only serves token exchange.
    return f"https://{portal}/oauth/authorize/?" + urlencode(params)

def exchange_code(code):

    url = "https://oauth.bitrix.info/oauth/token/"

    payload = {
        "grant_type": "authorization_code",
        "client_id": settings.BITRIX_CLIENT_ID,
        "client_secret": settings.BITRIX_CLIENT_SECRET,
        "redirect_uri": settings.BITRIX24_OAUTH_REDIRECT_URL,
        "code": code,
    }

    response = requests.post(
        url,
        data=payload,
        timeout=10,
    )

    response.raise_for_status()

    return response.json()