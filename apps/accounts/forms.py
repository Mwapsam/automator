from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from apps.accounts.models import Invitation


class SignupForm(UserCreationForm):
    """Sign-up form: a user, their email, and the company (tenant) name."""

    email = forms.EmailField(required=True)
    company_name = forms.CharField(max_length=255, required=True, label="Company name")

    class Meta:
        model = User
        fields = ("username", "email", "company_name", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class ProfileForm(forms.ModelForm):
    """Lets a signed-in user edit their own name and email."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")
        labels = {"first_name": "First name", "last_name": "Last name", "email": "Email"}

    def clean_email(self):
        email = self.cleaned_data["email"]
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another account already uses this email.")
        return email


class InviteForm(forms.Form):
    """Invite a teammate to the current workspace by email + role."""

    email = forms.EmailField()
    role = forms.ChoiceField(choices=Invitation.INVITE_ROLES)

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class AcceptInvitationForm(UserCreationForm):
    """Create a User when accepting an invitation (email comes from the invite)."""

    class Meta:
        model = User
        fields = ("username", "password1", "password2")
