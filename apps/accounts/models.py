from django.contrib.auth.models import User
from django.db import models
from django.utils.text import slugify


class Account(models.Model):
    """A tenant on the platform.

    This replaces the old Bitrix-centric ``BitrixAccount`` as the central tenant.
    It is provider-agnostic: WhatsApp numbers, email sending domains, billing and
    the (optional) Bitrix24 connection all hang off an ``Account``.
    """

    company_name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["company_name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug(self.company_name or "account")
        super().save(*args, **kwargs)

    @staticmethod
    def _unique_slug(base: str) -> str:
        root = slugify(base) or "account"
        slug = root
        i = 2
        while Account.objects.filter(slug=slug).exists():
            slug = f"{root}-{i}"
            i += 1
        return slug

    @property
    def owner(self):
        membership = self.memberships.filter(role=Membership.Role.OWNER).first()
        return membership.user if membership else None

    def __str__(self):
        return self.company_name


class Membership(models.Model):
    """Links a Django ``User`` to an ``Account`` with a role.

    Allows a tenant to have multiple users and lets us resolve the "current
    account" for a logged-in user.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="memberships"
    )
    account = models.ForeignKey(
        Account, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(
        max_length=20, choices=Role.choices, default=Role.OWNER
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "account")
        indexes = [
            models.Index(fields=["user", "account"]),
        ]

    def __str__(self):
        return f"{self.user} -> {self.account} ({self.role})"
