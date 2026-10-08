from datetime import timedelta

from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.dashboard.decorators import plan_feature, staff_view
from apps.menu.models import MenuCategory, MenuItem

from .forms import QuotationForm, parse_lines
from .models import Quotation, QuotationItem
from .words import amount_in_words


def _get(request, pk):
    return get_object_or_404(Quotation.objects.prefetch_related("items"), restaurant=request.restaurant, pk=pk)


@staff_view("quotations")
@plan_feature("quotations")
def quotation_list(request):
    qs = request.restaurant.quotations.prefetch_related("items")
    status = request.GET.get("status", "")
    if status in dict(Quotation.STATUS_CHOICES):
        qs = qs.filter(status=status)
    return render(request, "dashboard/quotations/list.html", {"quotations": qs[:200], "status": status})


def _defaults(restaurant) -> dict:
    """New quotation: VAT from the restaurant setting, terms copied from the last quotation."""
    last = restaurant.quotations.exclude(terms="").order_by("-id").first()
    return {
        "issue_date": timezone.localdate(),
        "valid_until": timezone.localdate() + timedelta(days=30),
        "vat_percent": restaurant.vat_percent if restaurant.vat_enabled else 0,
        "language": translation.get_language() if translation.get_language() in ("en", "pt", "tet", "id") else "en",
        "terms": last.terms if last else "",
    }


def _menu_choices(restaurant):
    """Menu for the "Add from menu" picker: categories with their dishes."""
    return MenuCategory.objects.filter(restaurant=restaurant).prefetch_related(
        Prefetch("items", queryset=MenuItem.objects.filter(restaurant=restaurant).order_by("position", "name"))
    )


def _save_lines(quotation, lines):
    valid_menu = set(MenuItem.objects.filter(
        restaurant=quotation.restaurant, id__in=[l["menu_item_id"] for l in lines if l["menu_item_id"]]
    ).values_list("id", flat=True))
    quotation.items.all().delete()
    QuotationItem.objects.bulk_create([
        QuotationItem(quotation=quotation, position=i, description=l["description"], unit=l["unit"],
                      quantity=l["quantity"], unit_price=l["unit_price"],
                      menu_item_id=l["menu_item_id"] if l["menu_item_id"] in valid_menu else None)
        for i, l in enumerate(lines)
    ])


def _editor(request, quotation=None):
    r = request.restaurant
    form = QuotationForm(request.POST or None, instance=quotation, initial=None if quotation else _defaults(r))
    lines = [
        {"description": i.description, "unit": i.unit, "quantity": i.quantity, "unit_price": i.unit_price,
         "menu_item_id": i.menu_item_id}
        for i in quotation.items.all()
    ] if quotation else []
    errors = []
    if request.method == "POST":
        lines, errors = parse_lines(request.POST)
        if form.is_valid() and not errors:
            with transaction.atomic():
                if quotation is None:
                    data = {k: v for k, v in form.cleaned_data.items()}
                    quotation = Quotation.create_for(r, request.user, **data)
                else:
                    quotation = form.save()
                _save_lines(quotation, lines)
            messages.success(request, _("Quotation %(number)s saved.") % {"number": quotation.number})
            return redirect("dashboard:quotation_edit", r.slug, quotation.pk)
    return render(request, "dashboard/quotations/edit.html", {
        "form": form, "quotation": quotation, "lines": lines, "line_errors": errors,
        "categories": _menu_choices(r),
    })


@staff_view("quotations")
@plan_feature("quotations")
def quotation_create(request):
    return _editor(request)


@staff_view("quotations")
@plan_feature("quotations")
def quotation_edit(request, pk):
    return _editor(request, _get(request, pk))


@require_POST
@staff_view("quotations")
@plan_feature("quotations")
def quotation_duplicate(request, pk):
    src = _get(request, pk)
    with transaction.atomic():
        copy = Quotation.create_for(
            request.restaurant, request.user,
            **{f: getattr(src, f) for f in ("language", "client_name", "client_organization", "client_address",
                                            "client_contact", "reference", "title", "event_date", "guests",
                                            "discount", "vat_percent", "terms")},
        )
        QuotationItem.objects.bulk_create([
            QuotationItem(quotation=copy, position=i.position, menu_item_id=i.menu_item_id, description=i.description,
                          unit=i.unit, quantity=i.quantity, unit_price=i.unit_price)
            for i in src.items.all()
        ])
    messages.success(request, _("Copied as %(number)s. Update the dates and prices if needed.") % {"number": copy.number})
    return redirect("dashboard:quotation_edit", request.restaurant.slug, copy.pk)


@require_POST
@staff_view("quotations")
@plan_feature("quotations")
def quotation_delete(request, pk):
    q = _get(request, pk)
    number = q.number
    q.delete()
    messages.success(request, _("Quotation %(number)s deleted.") % {"number": number})
    return redirect("dashboard:quotations", request.restaurant.slug)


@xframe_options_sameorigin
@staff_view("quotations")
@plan_feature("quotations")
def quotation_print(request, pk):
    """A4 quotation in the document's own language (not the staff member's)."""
    q = _get(request, pk)
    with translation.override(q.language):
        return render(request, "dashboard/quotations/print.html", {
            "q": q, "r": request.restaurant, "doc_lang": q.language,
            "words": amount_in_words(q.total, q.language, request.restaurant.currency),
        })
