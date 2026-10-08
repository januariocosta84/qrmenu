from decimal import Decimal, InvalidOperation

from django import forms
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .models import Quotation

MAX_LINES = 200


class QuotationForm(forms.ModelForm):
    class Meta:
        model = Quotation
        fields = [
            "client_name", "client_organization", "client_contact", "client_address", "reference",
            "title", "event_date", "guests", "issue_date", "valid_until", "language", "status",
            "discount", "vat_percent", "terms",
        ]
        widgets = {
            "client_address": forms.Textarea(attrs={"rows": 2}),
            "terms": forms.Textarea(attrs={"rows": 5}),
            "event_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "issue_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "valid_until": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "discount": forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal"}),
            "vat_percent": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "50", "inputmode": "decimal"}),
        }
        labels = {
            "client_name": gettext_lazy("Client contact person"), "client_organization": gettext_lazy("Organisation"),
            "client_contact": gettext_lazy("Phone / email"), "client_address": gettext_lazy("Address"),
            "reference": gettext_lazy("Tender / procurement reference"), "title": gettext_lazy("Subject"),
            "event_date": gettext_lazy("Event / delivery date"), "guests": gettext_lazy("Number of guests"),
            "issue_date": gettext_lazy("Quotation date"), "valid_until": gettext_lazy("Valid until"),
            "language": gettext_lazy("Document language"), "status": gettext_lazy("Status"),
            "discount": gettext_lazy("Discount (amount)"), "vat_percent": gettext_lazy("VAT (%)"),
            "terms": gettext_lazy("Terms, payment & bank details"),
        }
        help_texts = {
            "title": gettext_lazy("e.g. Lunch catering for training workshop"),
            "vat_percent": gettext_lazy("0 = no VAT."),
        }

    def clean(self):
        cleaned = super().clean()
        issue, valid = cleaned.get("issue_date"), cleaned.get("valid_until")
        if issue and valid and valid < issue:
            self.add_error("valid_until", _("Must be on or after the quotation date."))
        return cleaned


def parse_lines(post) -> tuple[list[dict], list[str]]:
    """
    Read the item rows posted by the editor (parallel lists line_description[], line_unit[], …).
    Blank rows are skipped. Returns (lines, errors).
    """
    descs = post.getlist("line_description")
    units = post.getlist("line_unit")
    qtys = post.getlist("line_quantity")
    prices = post.getlist("line_price")
    menu_ids = post.getlist("line_menu_item")
    lines, errors = [], []
    for i, desc in enumerate(descs[:MAX_LINES]):
        desc = desc.strip()
        qty_raw = (qtys[i] if i < len(qtys) else "").strip().replace(",", ".")
        price_raw = (prices[i] if i < len(prices) else "").strip().replace(",", ".")
        if not desc and not qty_raw and not price_raw:
            continue
        try:
            qty, price = Decimal(qty_raw or "0"), Decimal(price_raw or "0")
        except InvalidOperation:
            errors.append(_("Line %(n)s: enter numbers for quantity and price.") % {"n": i + 1})
            continue
        if not desc:
            errors.append(_("Line %(n)s: enter a description.") % {"n": i + 1})
            continue
        if qty < 0 or price < 0 or qty >= Decimal("100000000") or price >= Decimal("10000000000"):
            errors.append(_("Line %(n)s: quantity and price must be valid positive numbers.") % {"n": i + 1})
            continue
        menu_id = menu_ids[i] if i < len(menu_ids) else ""
        lines.append({
            "description": desc[:300], "unit": (units[i] if i < len(units) else "").strip()[:20],
            "quantity": qty.quantize(Decimal("0.01")), "unit_price": price.quantize(Decimal("0.01")),
            "menu_item_id": int(menu_id) if menu_id.isdigit() else None,
        })
    if not lines and not errors:
        errors.append(_("Add at least one line."))
    return lines, errors
