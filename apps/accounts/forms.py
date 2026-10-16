from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils import timezone

from .models import CustomerProfile

User = get_user_model()


class SignUpForm(UserCreationForm):
    first_name = forms.CharField(label="Your first name", max_length=60)
    last_name = forms.CharField(label="Your last name", max_length=60, required=False)
    phone = forms.CharField(label="Phone number (optional)", max_length=30, required=False,
                            help_text="Our planning team may call or message you about your plan.")
    accept_terms = forms.BooleanField(
        label="I understand that my wedding details are shared with the planning team, and I have read the privacy notice.",
        error_messages={"required": "Please confirm to continue."},
    )

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists. Try logging in instead.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = user.email.lower()
        user.role = User.Role.CUSTOMER
        if commit:
            user.save()
            CustomerProfile.objects.create(
                user=user, phone=self.cleaned_data.get("phone", ""), consent_terms_at=timezone.now()
            )
        return user


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))
