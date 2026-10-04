from django import forms

from apps.billing.models import BillingSettings, Invoice, Plan


class PlanForm(forms.ModelForm):
    class Meta:
        model = Plan
        fields = ["name", "description", "price_monthly", "currency", "max_tables", "max_menu_items", "max_staff",
                  "is_active", "is_public", "position"]
        widgets = {"price_monthly": forms.NumberInput(attrs={"step": "0.01", "min": "0"})}
        labels = {"price_monthly": "Price per month", "max_menu_items": "Max dishes"}


class BillingSettingsForm(forms.ModelForm):
    class Meta:
        model = BillingSettings
        fields = ["default_plan", "trial_days", "grace_days", "invoice_days_before", "payment_instructions"]
        widgets = {"payment_instructions": forms.Textarea(attrs={
            "rows": 5, "placeholder": "Bank: BNCTL · Account name: … · Account no.: …\nMobile money: …",
        })}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["default_plan"].queryset = Plan.objects.filter(is_active=True)


class MarkPaidForm(forms.Form):
    method = forms.ChoiceField(choices=Invoice.METHOD_CHOICES)
    reference = forms.CharField(max_length=120, required=False, help_text="Transfer / receipt reference")


class NewInvoiceForm(forms.Form):
    months = forms.IntegerField(min_value=1, max_value=24, initial=1)
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False,
                                help_text="Leave blank for plan price × months.")
    notes = forms.CharField(max_length=255, required=False)
