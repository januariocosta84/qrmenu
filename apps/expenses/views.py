import csv
from datetime import datetime, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.permissions import Role
from apps.dashboard.decorators import plan_feature, staff_view

from . import report
from .forms import ExpenseForm
from .models import Expense

MAX_DAYS = 3660  # ten years


def _period(request):
    today = timezone.localdate()
    preset = request.GET.get("period", "month")
    rng = report.preset_range(preset, today)
    if rng:
        return preset, *rng
    try:
        date_from = datetime.strptime(request.GET.get("from", ""), "%Y-%m-%d").date()
        date_to = datetime.strptime(request.GET.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        return ("month", *report.preset_range("month", today))
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return "custom", max(date_from, date_to - timedelta(days=MAX_DAYS)), date_to


@staff_view("expenses")
@plan_feature("expenses")
def expenses_page(request):
    r = request.restaurant
    preset, date_from, date_to = _period(request)
    form = ExpenseForm(request.POST or None, initial={"date": timezone.localdate()})
    if request.method == "POST":
        if form.is_valid():
            e = form.save(commit=False)
            e.restaurant, e.created_by = r, request.user
            e.save()
            messages.success(request, _("Expense saved: %(what)s, %(amount)s.") % {
                "what": e.description, "amount": f"{r.currency_symbol}{e.amount:,.2f}"})
            return redirect(request.get_full_path())
    qs = Expense.objects.filter(restaurant=r, date__gte=date_from, date__lte=date_to).select_related("created_by")
    if request.GET.get("format") == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="expenses-{r.slug}-{date_from}-{date_to}.csv"'
        w = csv.writer(resp)
        w.writerow(["Date", "Category", "Description", "Amount", "Paid with", "Entered by"])
        for e in qs.order_by("date", "id"):
            w.writerow([e.date, e.get_category_display(), e.description, e.amount, e.get_paid_with_display(),
                        e.created_by.username if e.created_by else ""])
        return resp
    return render(request, "dashboard/expenses.html", {
        "form": form, "preset": preset, "date_from": date_from, "date_to": date_to,
        "glance": report.at_a_glance(r, timezone.localdate()),
        "totals": report.totals(r, date_from, date_to),
        "detail": report.breakdown(r, date_from, date_to),
        "expenses": qs[:300], "expense_count": qs.count(),
    })


@staff_view("expenses")
@plan_feature("expenses")
def expense_edit(request, pk):
    e = get_object_or_404(Expense, restaurant=request.restaurant, pk=pk)
    form = ExpenseForm(request.POST or None, instance=e)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Expense updated."))
        return redirect("dashboard:expenses", request.restaurant.slug)
    return render(request, "dashboard/simple_form.html", {
        "form": form, "title": _("Edit expense"),
        "back": reverse("dashboard:expenses", args=[request.restaurant.slug]),
    })


@require_POST
@staff_view("expenses")
@plan_feature("expenses")
def expense_delete(request, pk):
    e = get_object_or_404(Expense, restaurant=request.restaurant, pk=pk)
    e.delete()
    messages.success(request, _("Expense deleted."))
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)  # back to the same period
    return redirect("dashboard:expenses", request.restaurant.slug)


# ---------------------------------------------------------------- revenue entries (each branch, its own)

def _day_range(request):
    today = timezone.localdate()
    try:
        date_from = datetime.strptime(request.GET.get("from", ""), "%Y-%m-%d").date()
        date_to = datetime.strptime(request.GET.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        return today, today
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return max(date_from, date_to - timedelta(days=MAX_DAYS)), date_to


@staff_view("record_revenue")
@plan_feature("branches")
def revenue_page(request):
    """A branch records its own sales and sees its own totals (never other branches')."""
    from .forms import RevenueEntryForm
    from .models import RevenueEntry

    r = request.restaurant
    today = timezone.localdate()
    can_record = r.branch_approved and not r.is_closed  # pending/rejected/suspended branches can't record
    form = RevenueEntryForm(request.POST if can_record else None, initial={"date": today, "method": RevenueEntry.CASH})
    if request.method == "POST" and not can_record:
        raise PermissionDenied
    if request.method == "POST" and form.is_valid():
        e = form.save(commit=False)
        e.restaurant, e.created_by = r, request.user  # always this branch, whatever was posted
        e.save()
        messages.success(request, _("Revenue recorded: %(amount)s.") % {"amount": f"{r.currency_symbol}{e.amount:,.2f}"})
        return redirect(request.get_full_path())
    date_from, date_to = _day_range(request)
    entries = RevenueEntry.objects.filter(restaurant=r, date__gte=date_from, date__lte=date_to).select_related("created_by")
    return render(request, "dashboard/revenue.html", {
        "form": form, "can_record": can_record, "today": report.totals(r, today, today),
        "period": report.totals(r, date_from, date_to), "date_from": date_from, "date_to": date_to,
        "is_today": date_from == date_to == today,
        "entries": entries[:500], "entry_count": entries.count(),
        "by_method": entries.values("method").annotate(s=Sum("amount")).order_by("-s"),
        "method_labels": dict(RevenueEntry.METHOD_CHOICES),
    })


@require_POST
@staff_view("record_revenue")
@plan_feature("branches")
def revenue_entry_delete(request, pk):
    from .models import RevenueEntry

    e = get_object_or_404(RevenueEntry, restaurant=request.restaurant, pk=pk)
    # Owners and managers can remove any entry; cashiers only their own.
    if request.role not in (Role.OWNER, Role.MANAGER) and e.created_by_id != request.user.pk:
        raise PermissionDenied
    e.delete()
    messages.success(request, _("Revenue entry deleted."))
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect("dashboard:revenue", request.restaurant.slug)
