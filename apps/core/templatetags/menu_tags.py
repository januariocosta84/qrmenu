from decimal import Decimal

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def tr(context, obj, field):
    """{% tr item "name" %} -> the field in the customer's language."""
    if obj is None:
        return ""
    return obj.tr(field, context.get("ui_lang"))


@register.filter
def money(value, symbol="$"):
    if value is None or value == "":
        return ""
    return f"{symbol}{Decimal(value):,.2f}"


@register.filter
def money_abs(value, symbol="$"):
    """Like money, without the sign (the template writes "−" or a Loss label itself)."""
    if value is None or value == "":
        return ""
    return f"{symbol}{abs(Decimal(value)):,.2f}"
