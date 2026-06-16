from django.urls import path

from .views import connect, callback, install


urlpatterns = [
    path(
        "connect/",
        connect,
        name="bitrix-connect",
    ),
    path(
        "callback/",
        callback,
        name="bitrix-callback",
    ),
    path(
        "install/",
        install,
        name="bitrix-install",
    ),
]