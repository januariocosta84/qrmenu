from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Expense


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ["date", "category", "description", "amount", "paid_with"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal"}),
            "description": forms.TextInput(attrs={"placeholder": _("e.g. Rice 25 kg, gas refill, market vegetables")}),
        }
        labels = {"date": _("Date"), "category": _("Category"), "description": _("What was bought"),
                  "amount": _("Amount"), "paid_with": _("Paid with")}


class RevenueEntryForm(forms.ModelForm):
    class Meta:
        from .models import RevenueEntry

        model = RevenueEntry
        fields = ["date", "amount", "method", "note"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01", "inputmode": "decimal"}),
            "note": forms.TextInput(attrs={"placeholder": _("e.g. Walk-in sales, catering for Ministry")}),
        }
        labels = {"date": _("Date"), "amount": _("Amount (USD)"), "method": _("Payment method"), "note": _("Note (optional)")}
