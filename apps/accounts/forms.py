from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

User = get_user_model()


class SignupForm(forms.Form):
    restaurant_name = forms.CharField(max_length=120, label="Restaurant name")
    full_name = forms.CharField(max_length=150, label="Your name")
    email = forms.EmailField(label="Email", help_text="You'll sign in with this email. We'll send a confirmation link.")
    phone = forms.CharField(max_length=30, required=False, label="Phone (optional)")
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}), label="Password")
    accept = forms.BooleanField(label="I confirm I own or manage this restaurant.")
    # Honeypot: hidden from people, filled in by bots.
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username__iexact=email).exists():
            raise ValidationError("An account with this email already exists. Try logging in or resetting your password.")
        return email

    def clean_restaurant_name(self):
        name = " ".join(self.cleaned_data["restaurant_name"].split())
        if len(name) < 2:
            raise ValidationError("Enter the restaurant's name.")
        return name

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            raise ValidationError("Sign-up failed. Please try again.")
        if cleaned.get("password"):
            probe = User(username=cleaned.get("email", ""), email=cleaned.get("email", ""),
                         first_name=cleaned.get("full_name", ""))
            try:
                validate_password(cleaned["password"], probe)
            except ValidationError as exc:
                self.add_error("password", exc)
        return cleaned
