"""
Service types a restaurant can sell from the same table QR: the café menu (kitchen)
and, optionally, a car wash. Behaviour is described here, so a new type (e.g. "room",
"laundry") is one more entry plus its enable/name fields on Restaurant.

    kitchen          orders go to the Kitchen Display (else: counter screen + ticket)
    needs_vehicle    each line needs a plate number; prices may differ per vehicle type
    complete_on_paid orders become Completed once paid (no staff status buttons)
    receipt_prefix   numbering prefix for separate bills, e.g. W-0001
"""
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class ServiceType:
    code: str
    label: str
    kitchen: bool = True
    needs_vehicle: bool = False
    complete_on_paid: bool = False
    receipt_prefix: str = "C"
    enabled_field: str = ""  # Restaurant boolean that switches the service on ("" = always on)
    name_field: str = ""  # Restaurant field with the display name, e.g. "Timor Car Wash"


CAFE, CAR_WASH = "cafe", "car_wash"

SERVICE_TYPES = {
    CAFE: ServiceType(CAFE, _("Café"), receipt_prefix="C"),
    CAR_WASH: ServiceType(CAR_WASH, _("Car wash"), kitchen=False, needs_vehicle=True, complete_on_paid=True,
                          receipt_prefix="W", enabled_field="car_wash_enabled", name_field="car_wash_name"),
}
CHOICES = [(code, s.label) for code, s in SERVICE_TYPES.items()]
KITCHEN_SERVICES = [code for code, s in SERVICE_TYPES.items() if s.kitchen]

# Vehicle types for services that need a vehicle; "car" uses the item's normal price.
CAR, MOTORBIKE, PICKUP = "car", "motorbike", "pickup"
VEHICLE_TYPES = [(CAR, _("Car")), (MOTORBIKE, _("Motorbike")), (PICKUP, _("Pickup / SUV"))]
VEHICLE_CODES = [c for c, _l in VEHICLE_TYPES]
COMBINED_PREFIX = "R"  # one combined bill for several services


def get(code: str) -> ServiceType:
    return SERVICE_TYPES.get(code) or SERVICE_TYPES[CAFE]


def is_enabled(restaurant, code: str) -> bool:
    s = SERVICE_TYPES.get(code)
    return bool(s) and (not s.enabled_field or bool(getattr(restaurant, s.enabled_field, False)))


def enabled_codes(restaurant) -> list[str]:
    return [code for code in SERVICE_TYPES if is_enabled(restaurant, code)]


def display_name(restaurant, code: str) -> str:
    s = get(code)
    return (getattr(restaurant, s.name_field, "") if s.name_field else "") or str(s.label)
