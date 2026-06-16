from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


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
