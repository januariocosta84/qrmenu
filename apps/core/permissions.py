"""
Role-based permissions for restaurant staff.

Every staff action is checked against a capability. Capabilities map to the
roles allowed to perform them; a user only ever holds a role *within* a
specific restaurant, which is what keeps restaurants isolated from each other.
"""
from django.utils.translation import gettext_lazy as _


class Role:
    OWNER = "owner"
    MANAGER = "manager"
    KITCHEN = "kitchen"
    WAITER = "waiter"

    CHOICES = [
        (OWNER, _("Owner")),
        (MANAGER, _("Manager")),
        (KITCHEN, _("Kitchen")),
        (WAITER, _("Waiter / Cashier")),
    ]


ALL_ROLES = {Role.OWNER, Role.MANAGER, Role.KITCHEN, Role.WAITER}

CAPABILITIES = {
    "view_dashboard": ALL_ROLES,
    "view_orders": ALL_ROLES,
    "kitchen": ALL_ROLES,  # KDS + order status changes
    "cancel_orders": {Role.OWNER, Role.MANAGER, Role.WAITER},
    "take_orders": {Role.OWNER, Role.MANAGER, Role.WAITER},  # enter orders for guests without a phone
    "toggle_availability": ALL_ROLES,  # mark items sold out
    "payments": {Role.OWNER, Role.MANAGER, Role.WAITER},
    "table_sessions": {Role.OWNER, Role.MANAGER, Role.WAITER},
    "manage_menu": {Role.OWNER, Role.MANAGER},
    "manage_tables": {Role.OWNER, Role.MANAGER},
    "manage_restaurant": {Role.OWNER, Role.MANAGER},
    "reports": {Role.OWNER, Role.MANAGER},
    "quotations": {Role.OWNER, Role.MANAGER},  # price offers for catering / procurement bids
    "expenses": {Role.OWNER, Role.MANAGER},  # daily expenses and profit
    "record_revenue": {Role.OWNER, Role.MANAGER, Role.WAITER},  # manual sales entries for their own branch
    "manage_staff": {Role.OWNER},
    "manage_billing": {Role.OWNER},
}


def role_can(role: str | None, capability: str) -> bool:
    return bool(role) and role in CAPABILITIES.get(capability, set())
