from django import forms

from apps.billing.models import BillingSettings, Invoice, Plan


class PlanForm(forms.ModelForm):
    class Meta:
        model = Plan
        fields = ["name", "description", "price_monthly", "currency", "max_tables", "max_menu_items", "max_staff",
                  "feature_analytics", "feature_cash_register", "feature_quotations", "feature_expenses", "feature_branches",
                  "feature_api", "included_branches", "extra_branch_price", "is_active", "is_public", "position"]
        widgets = {"price_monthly": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
                   "extra_branch_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"})}
        labels = {"price_monthly": "Price per month", "max_menu_items": "Max dishes",
                  "feature_analytics": "Includes Analytics", "feature_cash_register": "Includes Cash register",
                  "feature_quotations": "Includes Quotations", "feature_expenses": "Includes Expenses & profit",
                  "feature_branches": "Includes Branches", "feature_api": "Includes Ordering API", "included_branches": "Branches included",
                  "extra_branch_price": "Price per extra branch (monthly)"}


class BillingSettingsForm(forms.ModelForm):
    class Meta:
        model = BillingSettings
        fields = ["default_plan", "trial_days", "grace_days", "invoice_days_before", "branch_flag_limit",
                  "payment_instructions"]
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
