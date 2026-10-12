import re

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.console.models import CallbackRequest


class CallbackForm(forms.ModelForm):
    """Home page: a restaurant owner asks us to call them back (no account needed)."""

    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))

    class Meta:
        model = CallbackRequest
        fields = ["name", "restaurant_name", "phone", "best_time", "setup_help"]
        labels = {
            "name": _("Your name"), "restaurant_name": _("Restaurant / café name"),
            "phone": _("WhatsApp or phone number"), "best_time": _("Best time to call"),
            "setup_help": _("Please set up my menu for me (I'll send photos of my menu on WhatsApp)"),
        }
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
            "phone": forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel", "placeholder": "+670 7xxx xxxx"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["best_time"].choices = [("any", _("Any time")), ("morning", _("Morning")),
                                            ("afternoon", _("Afternoon")), ("evening", _("Evening"))]

    def clean_phone(self):
        phone = " ".join(self.cleaned_data["phone"].split())
        if not re.fullmatch(r"[0-9+\-() ]{7,30}", phone) or len(re.sub(r"\D", "", phone)) < 7:
            raise ValidationError(_("Enter a phone number we can call, e.g. +670 7712 3456."))
        return phone

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):  # honeypot: filled in by bots only
            raise ValidationError(_("Something went wrong. Please try again."))
        return cleaned
