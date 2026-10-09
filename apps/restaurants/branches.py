"""
Restaurants with several branches.

Each branch is its own Restaurant (menu, tables, staff, orders); the main branch's
subscription covers its sub-branches and the owner's login owns every branch.
A main branch with a verified business profile requests a sub-branch (copying the
menu and settings); it stays Pending until a platform admin approves it.
"""
import logging
import re
import unicodedata
from datetime import timedelta

from django.core.mail import mail_admins, send_mail
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.accounts.signup import unique_slug
from apps.core.permissions import Role
from apps.menu.models import MenuCategory, MenuItem, MenuItemOption

from .models import Restaurant, RestaurantStaff

logger = logging.getLogger(__name__)

# Settings a new branch takes over from the branch it is copied from. Device- and
# address-specific fields (printer IP, cash drawer) are left for each branch to set.
COPIED_SETTINGS = (
    "description", "logo", "cover_image", "email", "opening_hours", "translations",
    "currency", "currency_symbol", "service_charge_percent", "default_language", "default_prep_minutes",
    "receipt_prompt", "receipt_width", "receipt_footer",
    "vat_enabled", "vat_percent", "vat_inclusive", "vat_label", "vat_number",
)


def branch_family(restaurant) -> list[Restaurant]:
    """The main branch first, then its sub-branches (open ones first, then by name)."""
    main = restaurant.main_branch
    subs = list(main.sub_branches.order_by(F("branch_closed_at").asc(nulls_first=True), "name"))
    return [main, *subs]


def owned_restaurants(user):
    """Every restaurant (branch) this login owns, live or not."""
    return Restaurant.objects.filter(staff__user=user, staff__role=Role.OWNER, staff__is_active=True).distinct().order_by("name")


def copy_menu(source: Restaurant, target: Restaurant) -> int:
    """Copy categories, dishes and add-ons (with photos and translations). Returns the number of dishes."""
    count = 0
    for cat in MenuCategory.objects.filter(restaurant=source).prefetch_related("items__options"):
        new_cat = MenuCategory.objects.create(
            restaurant=target, name=cat.name, position=cat.position, is_active=cat.is_active,
            translations=cat.translations,
        )
        for item in cat.items.all():
            new_item = MenuItem.objects.create(
                restaurant=target, category=new_cat, name=item.name, description=item.description,
                price=item.price, image=item.image.name if item.image else "", is_available=item.is_available,
                prep_minutes=item.prep_minutes, position=item.position, translations=item.translations,
            )
            MenuItemOption.objects.bulk_create([
                MenuItemOption(menu_item=new_item, name=o.name, price=o.price, is_available=o.is_available,
                               position=o.position, translations=o.translations)
                for o in item.options.all()
            ])
            count += 1
    return count


def create_branch(owner, *, main: Restaurant, name: str, address: str = "", phone: str = "",
                  source: Restaurant | None = None, copy_menu_items: bool = True, copy_settings: bool = True,
                  status: str = Restaurant.BRANCH_PENDING, **branch_fields) -> Restaurant:
    """
    New sub-branch of `main`, owned by `owner`. It starts Pending (hidden, can't record revenue) until a
    platform admin approves it; pass status=ACTIVE to create it approved (platform admin tools, tests).
    It has no subscription of its own: the main branch's plan covers it (see billing.branch_slots).
    """
    if main is None:
        raise ValueError("A sub-branch needs a main branch.")
    main = main.main_branch  # one level: sub-branches always hang off the main branch

    fields = {}
    if source is not None and copy_settings:
        fields = {f: getattr(source, f) for f in COPIED_SETTINGS}
    with transaction.atomic():
        branch = Restaurant.objects.create(
            name=name.strip(), slug=unique_slug(name), address=address.strip(), phone=phone.strip(),
            is_active=status == Restaurant.BRANCH_ACTIVE, parent=main, branch_status=status,
            **{**fields, **branch_fields},
        )
        RestaurantStaff.objects.create(restaurant=branch, user=owner, role=Role.OWNER)
        if source is not None and copy_menu_items:
            copy_menu(source, branch)
    return branch


def request_branch(owner, *, main: Restaurant, storefront_photo, registration_number: str, owner_name: str,
                   **kwargs) -> Restaurant:
    """A verified main branch asks for a new sub-branch: Pending, checked for abuse, platform admins emailed."""
    branch = create_branch(owner, main=main, storefront_photo=storefront_photo,
                           branch_registration_number=registration_number.strip(),
                           branch_owner_name=owner_name.strip(), **kwargs)
    branch.branch_flags = abuse_flags(branch)
    branch.save(update_fields=["branch_flags"])
    flags = "".join(f"\n  ! {f}" for f in branch.branch_flags)
    try:
        mail_admins(f"Branch request: {branch.name}",
                    f"{main.name} asked for a new branch: {branch.name}\n{branch.address} · {branch.phone}\n"
                    f"Owner: {owner.email or owner.username}"
                    + (f"\n\nFlagged for review:{flags}" if flags else "")
                    + "\n\nReview it in the platform console → Verification.")
    except Exception:
        logger.exception("Could not email platform admins about a branch request")
    return branch


# ---------------------------------------------------------------- abuse detection

# Municipalities and main towns of Timor-Leste, to recognise a local address.
TL_PLACES = (
    "timor", "dili", "atauro", "aileu", "ainaro", "maubisse", "baucau", "bobonaro", "maliana", "balibo", "covalima",
    "suai", "ermera", "gleno", "lautem", "lospalos", "liquica", "maubara", "manatuto", "laclubar", "manufahi",
    "same", "oecusse", "oe-cusse", "oecussi", "pante macassar", "viqueque", "comoro", "becora", "bidau", "taibesi",
    "bairro pite", "fatuhada", "hera", "metinaro", "tibar", "railaco", "letefoho", "hatulia", "atsabe", "venilale",
    "vemasse", "laga", "baguia", "quelicai", "uatolari", "ossu", "lacluta", "alas", "turiscai", "hatu-builico",
)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().casefold()
    return " ".join(re.sub(r"[^a-z0-9+-]+", " ", text).split())


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def brand_ok(main: Restaurant, name: str) -> bool:
    """Branch names must use the main brand name, e.g. "Cafe Atsabe – Dili" for "Cafe Atsabe"."""
    return _norm(main.name) in _norm(name)


def abuse_flags(branch: Restaurant) -> list[str]:
    """Reasons a sub-branch request needs a closer look by a platform admin (empty = nothing unusual)."""
    from apps.billing.models import BillingSettings

    main = branch.main_branch
    flags = []
    limit = BillingSettings.load().branch_flag_limit
    recent = main.sub_branches.filter(created_at__gte=timezone.now() - timedelta(days=30)).count()
    if recent > limit:
        flags.append(f"{recent} branch requests in the last 30 days (limit {limit}).")
    if not brand_ok(main, branch.name):
        flags.append(f"Branch name “{branch.name}” doesn't use the brand name “{main.name}”.")
    profile = getattr(main, "business_profile", None)
    if profile is not None:
        if branch.branch_registration_number and _norm(branch.branch_registration_number) != _norm(profile.registration_number):
            flags.append(f"Different registration number: {branch.branch_registration_number} "
                         f"(main branch: {profile.registration_number}).")
        if branch.branch_owner_name and _norm(branch.branch_owner_name) != _norm(profile.owner_name):
            flags.append(f"Different owner: {branch.branch_owner_name} (main branch: {profile.owner_name}).")
    address = _norm(branch.address)
    if address and not any(re.search(rf"\b{re.escape(p)}\b", address) for p in TL_PLACES):
        flags.append(f"Address “{branch.address}” isn't recognisable as a place in Timor-Leste.")
    digits = _digits(branch.phone)
    if branch.phone.strip().startswith(("+", "00")) and not digits.lstrip("0").startswith("670"):
        flags.append(f"Phone {branch.phone} is from another country.")
    # The same phone or address as a restaurant of a different account suggests a separate business.
    family = [main.pk, *main.sub_branches.values_list("pk", flat=True)]
    others = Restaurant.objects.exclude(pk__in=family).exclude(parent_id__in=family)
    for other in others.only("name", "phone", "address"):
        if len(digits) >= 7 and len(_digits(other.phone)) >= 7 and _digits(other.phone)[-7:] == digits[-7:]:
            flags.append(f"Same phone number as {other.name} (another account).")
            break
    for other in others.only("name", "address"):
        if len(address) >= 8 and _norm(other.address) == address:
            flags.append(f"Same address as {other.name} (another account).")
            break
    return flags


# ---------------------------------------------------------------- platform review

class NoBranchSlot(Exception):
    pass


def _tell_owner(branch: Restaurant, subject: str, body: str) -> None:
    emails = list(branch.main_branch.staff.filter(role=Role.OWNER, is_active=True, user__email__gt="")
                  .values_list("user__email", flat=True))
    if not emails:
        return
    try:
        send_mail(subject, body, None, emails)
    except Exception:
        logger.exception("Could not email the owner of %s", branch.name)


def approve_branch(branch: Restaurant) -> Restaurant:
    """Pending/rejected/suspended → Active. Needs a free branch slot on the main branch's plan."""
    from apps.billing.services import branch_slots

    if branch.branch_status != Restaurant.BRANCH_ACTIVE and branch_slots(branch.main_branch)["free"] < 1:
        raise NoBranchSlot(f"{branch.main_branch.name} has no free branch slot: its plan's branches are in use. "
                           "The owner needs to pay for an extra branch first.")
    branch.branch_status, branch.is_active, branch.branch_review_note = Restaurant.BRANCH_ACTIVE, True, ""
    branch.save(update_fields=["branch_status", "is_active", "branch_review_note", "updated_at"])
    _tell_owner(branch, f"Branch approved: {branch.name}",
                f"Good news: {branch.name} is approved and live. Its staff can now take orders and record revenue.")
    return branch


def reject_branch(branch: Restaurant, note: str = "") -> Restaurant:
    branch.branch_status, branch.is_active, branch.branch_review_note = Restaurant.BRANCH_REJECTED, False, note[:300]
    branch.save(update_fields=["branch_status", "is_active", "branch_review_note", "updated_at"])
    _tell_owner(branch, f"Branch request not approved: {branch.name}",
                f"Your request for {branch.name} was not approved." + (f"\n\nReason: {note}" if note else "")
                + "\n\nYou can correct the details and send it again from Branches in your dashboard.")
    return branch


def suspend_branch(branch: Restaurant, note: str = "") -> Restaurant:
    branch.branch_status, branch.is_active, branch.branch_review_note = Restaurant.BRANCH_SUSPENDED, False, note[:300]
    branch.save(update_fields=["branch_status", "is_active", "branch_review_note", "updated_at"])
    return branch


@transaction.atomic
def convert_to_separate_account(branch: Restaurant) -> Restaurant:
    """
    An invalid branch (really a different business) becomes its own account: no longer a branch,
    with its own subscription on the same plan, invoiced straight away (no new trial).
    """
    from apps.billing.models import Subscription
    from apps.billing.services import create_invoice, get_subscription

    main = branch.main_branch
    plan = get_subscription(main).plan if get_subscription(main) else None
    branch.parent, branch.branch_status, branch.branch_closed_at = None, "", None
    branch.save(update_fields=["parent", "branch_status", "branch_closed_at", "updated_at"])
    if plan is None:
        return branch
    today = timezone.localdate()
    sub, _ = Subscription.objects.get_or_create(restaurant=branch, defaults={"plan": plan})
    sub.plan, sub.trial_ends_on, sub.paid_until, sub.cancelled_at, sub.extra_branches = plan, today, None, None, 0
    sub.save()
    if sub.is_billable:
        create_invoice(sub, notes="Separate account (was a branch of %s)" % main.name)
    return branch
