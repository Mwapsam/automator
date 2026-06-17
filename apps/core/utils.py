from django.contrib.auth.decorators import user_passes_test


def _is_superuser(user):
    return user.is_authenticated and user.is_superuser


# Gate platform-admin views. Non-admins are redirected to LOGIN_URL/?next=…;
# authenticated non-superusers effectively bounce to the dashboard after login.
admin_required = user_passes_test(_is_superuser)
