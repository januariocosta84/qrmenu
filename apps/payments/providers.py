"""
Payment provider abstraction.

To add an online provider later (bank transfer, Visa/Mastercard gateway,
QR payment):

1. Subclass `PaymentProvider`, implement `start()` (create a payment with the
   provider and return a redirect URL / QR payload) and `handle_webhook()`
   (verify the signature, then mark the Payment succeeded/failed).
2. Register it in `PROVIDERS` under its PaymentMethod code.
3. Add the method code to `PaymentMethod.CUSTOMER_CHOICES` to offer it at checkout.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.orders.models import Order, PaymentMethod, PaymentStatus

from .models import Payment


@dataclass
class PaymentStart:
    payment: Payment
    redirect_url: str = ""
    extra: dict = field(default_factory=dict)


class PaymentProvider:
    code = "base"
    online = False

    def start(self, order: Order, amount: Decimal) -> PaymentStart:
        raise NotImplementedError

    def handle_webhook(self, request) -> Payment:
        raise NotImplementedError


class ManualProvider(PaymentProvider):
    """Cash / pay at counter / manual: staff record the money they received."""

    code = "manual"

    @transaction.atomic
    def record(
        self, order: Order, amount: Decimal, method: str, user=None, reference: str = "",
        cash_tendered: Decimal | None = None, change_given: Decimal = Decimal("0"),
    ) -> Payment:
        payment = Payment.objects.create(
            restaurant=order.restaurant,
            order=order,
            method=method,
            status=Payment.SUCCEEDED,
            amount=amount,
            currency=order.currency,
            provider=self.code,
            provider_reference=reference,
            cash_tendered=cash_tendered,
            change_given=change_given,
            received_by=user,
            paid_at=timezone.now(),
        )
        paid = sum(
            (p.amount for p in order.payments.filter(status=Payment.SUCCEEDED)), Decimal("0")
        )
        if paid >= order.total and order.payment_status != PaymentStatus.PAID:
            order.payment_status = PaymentStatus.PAID
            order.save(update_fields=["payment_status", "updated_at"])
        return payment


PROVIDERS: dict[str, PaymentProvider] = {
    PaymentMethod.PAY_AT_COUNTER: ManualProvider(),
    PaymentMethod.CASH: ManualProvider(),
    PaymentMethod.MANUAL: ManualProvider(),
}

STAFF_RECORDABLE_METHODS = [
    (PaymentMethod.CASH, "Cash"),
    (PaymentMethod.MANUAL, "Manual / other"),
    (PaymentMethod.BANK_TRANSFER, "Bank transfer (manual check)"),
    (PaymentMethod.CARD, "Card (external terminal)"),
    (PaymentMethod.QR_PAYMENT, "QR payment (manual check)"),
]


def get_provider(method: str) -> PaymentProvider:
    return PROVIDERS.get(method) or ManualProvider()
