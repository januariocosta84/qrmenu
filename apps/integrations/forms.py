from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.restaurants.models import Restaurant

from .models import Webhook
from .webhooks import UnsafeUrl, check_url


class ApiKeyForm(forms.Form):
    name = forms.CharField(max_length=60, label=_("Name"), help_text=_("e.g. Website, Mobile app, Delivery partner"))
    is_sandbox = forms.BooleanField(required=False, label=_("Test (sandbox) key"),
                                    help_text=_("Test orders are not sent to the kitchen and never count as revenue."))
    branches = forms.ModelMultipleChoiceField(
        queryset=Restaurant.objects.none(), required=False, widget=forms.CheckboxSelectMultiple,
        label=_("Branches"), help_text=_("Leave all unticked to allow every branch."),
    )

    def __init__(self, *args, family, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branches"].queryset = family


class ApiKeyEditForm(forms.Form):
    name = forms.CharField(max_length=60, label=_("Name"))
    branches = forms.ModelMultipleChoiceField(queryset=Restaurant.objects.none(), required=False,
                                              widget=forms.CheckboxSelectMultiple, label=_("Branches"))

    def __init__(self, *args, family, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branches"].queryset = family


class WebhookForm(forms.ModelForm):
    class Meta:
        model = Webhook
        fields = ["url", "all_orders"]
        widgets = {"url": forms.URLInput(attrs={"placeholder": "https://example.com/qrmenu/webhook"})}

    def clean_url(self):
        url = self.cleaned_data["url"].strip()
        try:
            check_url(url)
        except UnsafeUrl as exc:
            raise ValidationError(str(exc))
        return url
