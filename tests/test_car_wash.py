"""Café + car wash from the same table QR: order splitting, bills (combined/separate), paid items, receipt layout."""
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase

from apps.menu import service_types
from apps.menu.models import MenuCategory, MenuItem
from apps.orders.models import Order, OrderStatus, PaymentStatus
from apps.orders.services import OrderError, place_order
from apps.payments.bills import COMBINED, SEPARATE, billable, build_bills, create_receipts, receipt_lines
from apps.payments.services import receive_cash
from apps.payments.tickets import ticket_lines

from .test_ordering import make_restaurant


def car_wash_menu(r):
    r.car_wash_enabled, r.car_wash_name = True, "Timor Car Wash"
    r.save()
    cat = MenuCategory.objects.create(restaurant=r, name="Car wash", service_type=service_types.CAR_WASH)
    basic = MenuItem.objects.create(restaurant=r, category=cat, name="Basic Wash", price=Decimal("5.00"),
                                    vehicle_prices={"motorbike": "2.00", "pickup": "7.00"})
    full = MenuItem.objects.create(restaurant=r, category=cat, name="Full Wash + Interior", price=Decimal("12.00"))
    return basic, full


class SplitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.basic, self.full = car_wash_menu(self.r)

    def order(self, lines, table=True):
        return place_order(restaurant=self.r, table=self.table if table else None, lines=lines, customer_ref="abc")

    def test_one_cart_two_orders(self):
        o = self.order([{"menu_item": self.tea.id, "quantity": 2},
                        {"menu_item": self.basic.id, "quantity": 1, "plate_number": " 12-345 tl ", "vehicle_type": "pickup"}])
        cafe, wash = o.group_orders
        self.assertEqual((cafe.service_type, wash.service_type), ("cafe", "car_wash"))
        self.assertEqual(o, cafe)  # the café order is the main one
        self.assertEqual(cafe.order_group, wash.order_group)
        self.assertIsNotNone(cafe.order_group)
        self.assertEqual(cafe.table_session, wash.table_session)  # same table bill
        self.assertEqual((cafe.total, wash.total), (Decimal("3.00"), Decimal("7.00")))  # pickup price
        item = wash.items.get()
        self.assertEqual((item.plate_number, item.vehicle_type, item.unit_price), ("12-345 TL", "pickup", Decimal("7.00")))
        self.assertEqual(wash.plate_number, "12-345 TL")
        self.assertIsNone(wash.estimated_minutes)

    def test_cafe_only_unchanged(self):
        o = self.order([{"menu_item": self.tea.id, "quantity": 1}])
        self.assertEqual((o.service_type, o.order_group, len(o.group_orders)), ("cafe", None, 1))

    def test_plate_required_and_vehicle_checked(self):
        with self.assertRaises(OrderError) as ctx:
            self.order([{"menu_item": self.basic.id, "quantity": 1}])
        self.assertEqual(ctx.exception.code, "plate_required")
        with self.assertRaises(OrderError) as ctx:
            self.order([{"menu_item": self.basic.id, "quantity": 1, "plate_number": "A1", "vehicle_type": "boat"}])
        self.assertEqual(ctx.exception.code, "invalid_vehicle")
        motor = self.order([{"menu_item": self.basic.id, "quantity": 1, "plate_number": "A1", "vehicle_type": "motorbike"}])
        self.assertEqual(motor.total, Decimal("2.00"))
        car = self.order([{"menu_item": self.full.id, "quantity": 1, "plate_number": "A2"}])  # no type: car price
        self.assertEqual((car.total, car.items.get().vehicle_type), (Decimal("12.00"), "car"))

    def test_disabled_car_wash_is_unavailable(self):
        self.r.car_wash_enabled = False
        self.r.save()
        with self.assertRaises(OrderError) as ctx:
            self.order([{"menu_item": self.basic.id, "quantity": 1, "plate_number": "A1"}])
        self.assertEqual(ctx.exception.code, "unavailable")

    def test_car_wash_completes_when_paid(self):
        o = self.order([{"menu_item": self.tea.id, "quantity": 1},
                        {"menu_item": self.full.id, "quantity": 1, "plate_number": "B2"}])
        cafe, wash = o.group_orders
        receive_cash([wash])
        wash.refresh_from_db()
        cafe.refresh_from_db()
        self.assertEqual((wash.payment_status, wash.status), (PaymentStatus.PAID, OrderStatus.COMPLETED))
        self.assertEqual((cafe.payment_status, cafe.status), (PaymentStatus.UNPAID, OrderStatus.NEW))  # kitchen flow as before
        self.assertEqual(billable([cafe, wash]), [cafe])  # only unpaid items print


class BillTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant(service="10")
        self.basic, self.full = car_wash_menu(self.r)
        o = place_order(restaurant=self.r, table=self.table, lines=[
            {"menu_item": self.tea.id, "quantity": 2},
            {"menu_item": self.basic.id, "quantity": 1, "plate_number": "12-345 TL"}])
        self.cafe, self.wash = o.group_orders

    def test_combined_bill(self):
        [bill] = build_bills(self.r, [self.cafe, self.wash], COMBINED)
        self.assertEqual(bill["business"], self.r.name)
        self.assertEqual([s["title"] for s in bill["sections"]], ["CAFÉ", "TIMOR CAR WASH (Plate: 12-345 TL)"])
        self.assertEqual([s["subtotal"] for s in bill["sections"]], ["3.30", "5.50"])  # incl. 10% service each
        self.assertEqual(bill["total"], "8.80")
        self.assertFalse(bill["paid"])
        [rec] = create_receipts(self.r, [self.cafe, self.wash], mode=COMBINED)
        self.assertEqual(rec.number, "R-0001")
        self.assertEqual(set(rec.orders.all()), {self.cafe, self.wash})

    def test_separate_bills_have_own_name_and_numbering(self):
        bills = build_bills(self.r, [self.cafe, self.wash], SEPARATE)
        self.assertEqual([b["business"] for b in bills], [self.r.name, "Timor Car Wash"])
        self.assertEqual([b["total"] for b in bills], ["3.30", "5.50"])
        first = create_receipts(self.r, [self.cafe, self.wash], mode=SEPARATE)
        again = create_receipts(self.r, [self.cafe, self.wash], mode=SEPARATE)
        self.assertEqual([r.number for r in first + again], ["C-0001", "W-0001", "C-0002", "W-0002"])
        self.assertEqual(list(first[1].orders.all()), [self.wash])
        # Separate mode with a single service is just one bill.
        self.assertEqual(len(build_bills(self.r, [self.wash], SEPARATE)), 1)

    def test_pay_only_car_wash(self):
        receive_cash([self.wash])
        self.cafe.refresh_from_db()
        self.wash.refresh_from_db()
        left = billable([self.cafe, self.wash])
        self.assertEqual(left, [self.cafe])
        [bill] = build_bills(self.r, left)
        self.assertEqual([s["service"] for s in bill["sections"]], ["cafe"])
        [paid] = build_bills(self.r, [self.wash])
        self.assertTrue(paid["paid"])
        self.assertEqual(paid["title"], "RECEIPT")

    def test_receipt_layout_fits_58_and_80mm(self):
        self.r.name = "Kafe Atsabe Cristo Rei – Restaurante no Kafé"
        self.r.address = "Rua de Cristo Rei, Bairro Pite, Dili, Timor-Leste (near the big statue)"
        self.r.save(update_fields=["name", "address"])
        long = MenuItem.objects.create(restaurant=self.r, category=self.rice.category,
                                       name="Extra large special fried rice with chicken, beef and prawns", price=Decimal("9.99"))
        o = place_order(restaurant=self.r, table=self.table, lines=[
            {"menu_item": long.id, "quantity": 12, "note": "no chili please, and extra sauce on the side"}])
        for width in (32, 48):
            for copy in (False, True):
                lines = receipt_lines(build_bills(self.r, [o, self.cafe, self.wash])[0], width, copy=copy)
                for text, style in lines:
                    limit = width // 2 if style == "big" else width
                    self.assertLessEqual(len(text), limit, (width, text))
                texts = [t for t, _s in lines]
                joined = " ".join(" ".join(texts).split())
                # Wrapped onto the next line(s), never cut off; the price stays on the first line.
                self.assertIn("Extra large special fried rice with chicken, beef and prawns", joined.replace(" $119.88", ""))
                self.assertTrue(any(t.startswith("12 x Extra") and t.endswith("$119.88") for t in texts))
                self.assertIn("NOT PAID", joined)
                self.assertEqual("COPY" in joined, copy)

    def test_ticket(self):
        lines = [t for t, _s in ticket_lines(self.wash, 32)]
        self.assertIn("TIMOR CAR WASH", " ".join(lines))
        self.assertIn("Table 12", " ".join(lines))
        self.assertIn("Plate: 12-345 TL", lines)
        self.assertIn("Package: Basic Wash (Car)", lines)
        self.assertTrue(all(len(t) <= 32 for t in lines))
        walk_in = place_order(restaurant=self.r, table=None,
                              lines=[{"menu_item": self.full.id, "quantity": 1, "plate_number": "77-111 TL"}])
        self.assertIn("Walk-in", " ".join(t for t, _s in ticket_lines(walk_in, 48)))


# ---------------------------------------------------------------- screens & APIs

import json  # noqa: E402

from django.test import Client  # noqa: E402
from django.urls import reverse  # noqa: E402

from apps.core.permissions import Role  # noqa: E402
from apps.payments.models import Receipt  # noqa: E402

from .test_ordering import staff  # noqa: E402


class FlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.basic, self.full = car_wash_menu(self.r)
        self.customer = Client()
        self.customer.get(self.table.active_qr().get_path())
        self.cashier = Client()
        self.cashier.force_login(staff(self.r, "cashier", Role.OWNER))

    def post_order(self, items, client=None, **extra):
        return (client or self.customer).post(reverse("storefront:api_place_order", args=["alpha"]),
                                              data=json.dumps({"items": items, **extra}), content_type="application/json")

    def test_customer_orders_cafe_and_car_wash_together(self):
        page = self.customer.get(reverse("storefront:table", args=["alpha", "12"]))
        self.assertEqual([code for code, _l in page.context["tabs"]], ["cafe", "car_wash"])
        self.assertContains(page, "Timor Car Wash")
        resp = self.post_order([{"menu_item": self.tea.id, "quantity": 1},
                                {"menu_item": self.basic.id, "quantity": 1, "plate_number": "12-345 tl",
                                 "vehicle_type": "motorbike"}])
        self.assertEqual(resp.status_code, 201)
        cafe, wash = Order.objects.order_by("number")
        self.assertEqual((cafe.service_type, wash.service_type, wash.total), ("cafe", "car_wash", Decimal("2.00")))
        self.assertEqual(resp.json()["token"], str(cafe.public_token))
        status = self.customer.get(resp.json()["status_url"])
        self.assertContains(status, "Car wash ordered")  # the car wash part: no live tracking
        self.assertContains(status, "12-345 TL")
        wash_page = self.customer.get(reverse("storefront:order_status", args=[wash.public_token]))
        self.assertFalse(wash_page.context["tracked"])
        self.assertNotContains(wash_page, "order_status.js")
        # Paying only the café doesn't end ordering: the car wash is still on this phone's bill.
        receive_cash([cafe])
        self.assertTrue(self.customer.get(reverse("storefront:api_access", args=["alpha"])).json()["can_order"])
        # More coffee later: same table bill.
        self.post_order([{"menu_item": self.tea.id, "quantity": 2}])
        self.assertEqual(Order.objects.filter(table_session=cafe.table_session).count(), 3)

    def test_plate_required_message(self):
        resp = self.post_order([{"menu_item": self.basic.id, "quantity": 1}])
        self.assertEqual((resp.status_code, resp.json()["error"]), (409, "plate_required"))

    def test_menu_without_car_wash_has_no_tabs(self):
        self.r.car_wash_enabled = False
        self.r.save(update_fields=["car_wash_enabled"])
        page = self.customer.get(reverse("storefront:table", args=["alpha", "12"]))
        self.assertEqual(page.context["tabs"], [])
        self.assertNotContains(page, "Basic Wash")

    def test_walk_in_qr(self):
        self.cashier.get(reverse("dashboard:car_wash", args=["alpha"]))  # makes the walk-in QR token
        self.r.refresh_from_db()
        url = reverse("storefront:car_wash", args=["alpha"])
        walker = Client()
        self.assertFalse(walker.get(url).context["can_order"])  # without the QR token: menu only
        walker.get(f"{url}?k={self.r.car_wash_qr_token}")
        page = walker.get(url)
        self.assertTrue(page.context["can_order"])
        self.assertNotContains(page, "Iced Tea")  # car wash tab only
        bad = self.post_order([{"menu_item": self.tea.id, "quantity": 1}], client=walker, walk_in=True)
        self.assertEqual(bad.status_code, 403)
        ok = self.post_order([{"menu_item": self.full.id, "quantity": 1, "plate_number": "77-111 TL"}],
                             client=walker, walk_in=True)
        self.assertEqual(ok.status_code, 201)
        o = Order.objects.get(plate_number="77-111 TL")
        self.assertIsNone(o.table)
        self.assertEqual(Client().post(reverse("storefront:api_place_order", args=["alpha"]), data=json.dumps(
            {"items": [{"menu_item": self.full.id, "quantity": 1, "plate_number": "X"}], "walk_in": True}),
            content_type="application/json").status_code, 403)

    def test_kitchen_only_gets_cafe(self):
        self.post_order([{"menu_item": self.tea.id, "quantity": 1},
                         {"menu_item": self.basic.id, "quantity": 1, "plate_number": "A1"}])
        data = self.cashier.get(reverse("dashboard:api_orders", args=["alpha"]) + "?active=1&kitchen=1").json()
        self.assertEqual([o["service_type"] for o in data], ["cafe"])
        self.assertEqual(self.cashier.get(reverse("dashboard:kitchen", args=["alpha"])).context["kds_config"]
                         ["kitchenServices"], ["cafe"])
        page = self.cashier.get(reverse("dashboard:car_wash", args=["alpha"]))
        self.assertContains(page, "A1")

    def test_print_bill_dialog_flow(self):
        self.post_order([{"menu_item": self.tea.id, "quantity": 2},
                         {"menu_item": self.full.id, "quantity": 1, "plate_number": "12-345 TL"}])
        session = Order.objects.first().table_session
        opts = self.cashier.get(reverse("dashboard:bill_options", args=["alpha"]) + f"?session={session.pk}").json()
        self.assertEqual([(s["code"], s["total"]) for s in opts["services"]], [("cafe", "3.00"), ("car_wash", "12.00")])
        self.assertEqual(opts["mode"], "combined")
        wash_ids = opts["services"][1]["orders"]
        # Pay only the car wash now, separate bill on 58 mm.
        resp = self.cashier.post(reverse("dashboard:bill_print", args=["alpha"]), data=json.dumps(
            {"orders": wash_ids, "mode": "separate", "width": 32, "copies": 2, "pay": True, "tendered": "20"}),
            content_type="application/json").json()
        self.assertEqual([x["number"] for x in resp["receipts"]], ["W-0001"])
        self.assertEqual(resp["change"]["change"], "8.00")
        printed = self.cashier.get(resp["printUrl"])
        self.assertEqual(len(printed.context["pages"]), 2)  # 2 copies
        self.assertEqual(printed.context["width"], 32)
        text = " ".join(t for page in printed.context["pages"] for t, _s in page)
        self.assertIn("RECEIPT", text)
        self.assertIn("Timor Car Wash".upper(), text.upper())
        self.assertNotIn("COPY", text)
        # Only unpaid items remain: the café.
        opts = self.cashier.get(reverse("dashboard:bill_options", args=["alpha"]) + f"?session={session.pk}").json()
        self.assertEqual([s["code"] for s in opts["services"]], ["cafe"])
        again = self.cashier.post(reverse("dashboard:bill_print", args=["alpha"]), data=json.dumps(
            {"orders": wash_ids}), content_type="application/json")
        self.assertEqual(again.status_code, 409)  # already paid: not billed twice
        # Reprint from history: COPY.
        receipt = Receipt.objects.get(number="W-0001")
        copy = self.cashier.get(reverse("dashboard:bill_receipts", args=["alpha"]) + f"?ids={receipt.pk}&reprint=1")
        self.assertIn("COPY", " ".join(t for page in copy.context["pages"] for t, _s in page))
        receipt.refresh_from_db()
        self.assertEqual(receipt.reprints, 1)
        table_page = self.cashier.get(reverse("dashboard:table_detail", args=["alpha", self.table.pk]))
        self.assertContains(table_page, "W-0001")
        # Preview shows the café part, without numbering anything.
        preview = self.cashier.get(reverse("dashboard:bill_preview", args=["alpha"]) + f"?session={session.pk}")
        self.assertIn("PREVIEW", " ".join(t for page in preview.context["pages"] for t, _s in page))
        self.assertEqual(Receipt.objects.count(), 1)

    def test_drop_off_then_link_to_table(self):
        self.cashier.post(reverse("dashboard:car_wash", args=["alpha"]),
                          {"plate": "55-001 tl", "package": self.basic.id, "vehicle_type": "pickup"})
        o = Order.objects.get()
        self.assertEqual((o.plate_number, o.table, o.total, o.source), ("55-001 TL", None, Decimal("7.00"), "staff"))
        opts = self.cashier.get(reverse("dashboard:bill_options", args=["alpha"]) + "?plate=55-001 TL").json()
        self.assertEqual(opts["services"][0]["total"], "7.00")
        self.cashier.post(reverse("dashboard:car_wash_link", args=["alpha", o.pk]), {"table": self.table.pk})
        o.refresh_from_db()
        self.assertEqual((o.table, o.table_number), (self.table, "12"))
        self.assertEqual(o.table_session, self.table.current_session())
        ticket = self.cashier.get(reverse("dashboard:car_wash_ticket", args=["alpha", o.pk]))
        self.assertIn("Plate: 55-001 TL", [t for t, _s in ticket.context["pages"][0]])

    def test_reports_split_by_service(self):
        self.post_order([{"menu_item": self.tea.id, "quantity": 1},
                         {"menu_item": self.full.id, "quantity": 1, "plate_number": "B1"}])
        for o in Order.objects.all():
            receive_cash([o])
        from apps.orders.services import change_status
        cafe = Order.objects.get(service_type="cafe")
        for s in (OrderStatus.PREPARING, OrderStatus.READY, OrderStatus.COMPLETED):
            change_status(cafe, s)
        page = self.cashier.get(reverse("dashboard:reports", args=["alpha"]))
        rows = {s["code"]: s["revenue"] for s in page.context["services"]}
        self.assertEqual(rows, {"cafe": Decimal("1.50"), "car_wash": Decimal("12.00")})
        self.assertEqual(page.context["services"][1]["top_items"][0]["name"], "Full Wash + Interior")
        csv = self.cashier.get(reverse("dashboard:reports", args=["alpha"]) + "?format=csv").content.decode()
        self.assertIn("Service type", csv)
        self.assertIn("Timor Car Wash,1,12.00", csv)

    def test_waiter_order_cafe_and_car_wash_one_bill(self):
        page = self.cashier.get(reverse("dashboard:new_order", args=["alpha"]))
        self.assertIn("car_wash", [c["service"] for c in page.context["pos_config"]["menu"]])
        url = reverse("dashboard:api_order_create", args=["alpha"])
        lines = [{"menu_item": self.tea.id, "quantity": 2},
                 {"menu_item": self.basic.id, "quantity": 1, "plate_number": "88-123 tl", "vehicle_type": "pickup"}]
        at_table = self.cashier.post(url, data=json.dumps({"table": self.table.pk, "items": lines}),
                                     content_type="application/json").json()
        self.assertEqual([g["service_type"] for g in at_table["group"]], ["cafe", "car_wash"])
        cafe, wash = (Order.objects.get(pk=g["id"]) for g in at_table["group"])
        self.assertEqual(cafe.table_session, wash.table_session)  # one table bill
        self.assertEqual((wash.plate_number, wash.total), ("88-123 TL", Decimal("7.00")))
        opts = self.cashier.get(reverse("dashboard:bill_options", args=["alpha"]) + f"?session={at_table['table_session']}").json()
        self.assertEqual([x["code"] for x in opts["services"]], ["cafe", "car_wash"])
        # Counter (no table): the parts are printed together as one combined bill.
        counter = self.cashier.post(url, data=json.dumps({"items": lines}), content_type="application/json").json()
        ids = [g["id"] for g in counter["group"]]
        resp = self.cashier.post(reverse("dashboard:bill_print", args=["alpha"]), data=json.dumps(
            {"orders": ids, "mode": "combined"}), content_type="application/json").json()
        self.assertEqual(len(resp["receipts"]), 1)
        rec = Receipt.objects.get(pk=resp["receipts"][0]["id"])
        self.assertEqual(rec.data["total"], "10.00")
        self.assertEqual([x["service"] for x in rec.data["sections"]], ["cafe", "car_wash"])
        missing = self.cashier.post(url, data=json.dumps({"items": [{"menu_item": self.basic.id, "quantity": 1}]}),
                                    content_type="application/json")
        self.assertEqual(missing.json()["error"], "plate_required")

    def test_no_washing_or_ready_buttons(self):
        self.post_order([{"menu_item": self.full.id, "quantity": 1, "plate_number": "C3"}])
        o = Order.objects.get()
        page = self.cashier.get(reverse("dashboard:order_detail", args=["alpha", o.pk]))
        self.assertEqual([c for c, _l in page.context["transitions"]], ["cancelled"])
        self.cashier.post(reverse("dashboard:order_detail", args=["alpha", o.pk]), {"action": "status", "status": "preparing"})
        o.refresh_from_db()
        self.assertEqual(o.status, "new")
        api = self.cashier.post(reverse("dashboard:api_order_status", args=["alpha", o.pk]),
                                data=json.dumps({"status": "preparing"}), content_type="application/json")
        self.assertEqual(api.status_code, 409)


class PayTogetherTests(TestCase):
    """One QR order with café + car wash: one payment for the sum, change given once."""

    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.basic, self.full = car_wash_menu(self.r)
        o = place_order(restaurant=self.r, table=self.table, customer_ref="g1", lines=[
            {"menu_item": self.tea.id, "quantity": 2},
            {"menu_item": self.full.id, "quantity": 1, "plate_number": "12-345 TL"}])
        self.cafe, self.wash = o.group_orders
        self.cashier = Client()
        self.cashier.force_login(staff(self.r, "cashier", Role.OWNER))

    def assert_both_paid(self):
        for o in (self.cafe, self.wash):
            o.refresh_from_db()
            self.assertEqual(o.payment_status, PaymentStatus.PAID)

    def test_mark_paid_pays_the_sum_once(self):
        resp = self.cashier.post(reverse("dashboard:order_mark_paid", args=["alpha", self.cafe.pk]), {"tendered": "20"})
        self.assertEqual(resp.status_code, 302)
        self.assert_both_paid()
        payments = [p for o in (self.cafe, self.wash) for p in o.payments.all()]
        self.assertEqual(sum(p.change_given for p in payments), Decimal("5.00"))  # 20 − 15: change once
        self.assertEqual(len([p for p in payments if p.change_given]), 1)

    def test_kitchen_card_shows_and_pays_group_total(self):
        data = self.cashier.get(reverse("dashboard:api_order", args=["alpha", self.cafe.pk])).json()
        self.assertEqual(data["pay_group"]["due"], "15.00")
        self.assertEqual(data["pay_group"]["numbers"], f"#{self.cafe.number} + #{self.wash.number}")
        resp = self.cashier.post(reverse("dashboard:api_order_payment", args=["alpha", self.cafe.pk]),
                                 data=json.dumps({"tendered": "50"}), content_type="application/json").json()
        self.assertEqual(resp["change"], "35.00")
        self.assertEqual(sorted(resp["receipt"]["orders"]), sorted([self.cafe.pk, self.wash.pk]))
        self.assert_both_paid()
        self.wash.refresh_from_db()
        self.assertEqual(self.wash.status, OrderStatus.COMPLETED)

    def test_order_page_and_lists_show_the_sum(self):
        page = self.cashier.get(reverse("dashboard:order_detail", args=["alpha", self.wash.pk]))
        self.assertEqual(page.context["pay_group_due"], Decimal("15.00"))
        self.assertContains(page, "paid as one sum")
        table = self.cashier.get(reverse("dashboard:table_detail", args=["alpha", self.table.pk]))
        self.assertContains(table, 'data-cash-due="15.00"')
        # Already paid part drops out of the group.
        receive_cash([self.wash])
        page = self.cashier.get(reverse("dashboard:order_detail", args=["alpha", self.cafe.pk]))
        self.assertIsNone(page.context["pay_group"])

    def test_one_pay_button_per_group(self):
        orders = self.cashier.get(reverse("dashboard:orders", args=["alpha"]))
        html = orders.content.decode()
        self.assertEqual(html.count("dashboard/alpha/orders/" + str(self.cafe.pk) + "/paid/") +
                         html.count("dashboard/alpha/orders/" + str(self.wash.pk) + "/paid/"), 1)
        self.assertContains(orders, f"Paid with #{self.cafe.number}")
        self.assertContains(orders, f"#{self.cafe.number} + #{self.wash.number}")

    def test_walk_in_bill_includes_cafe_ordered_together(self):
        o = place_order(restaurant=self.r, table=None, customer_ref="w1", placed_by=None, lines=[
            {"menu_item": self.tea.id, "quantity": 1},
            {"menu_item": self.basic.id, "quantity": 1, "plate_number": "77-777 TL"}])
        self.r.car_wash_enabled = True
        page = self.cashier.get(reverse("dashboard:car_wash", args=["alpha"]))
        walk = page.context["walk_ins"][0]
        self.assertEqual((walk["plate"], walk["total"]), ("77-777 TL", Decimal("6.50")))
        opts = self.cashier.get(reverse("dashboard:bill_options", args=["alpha"]) + "?plate=77-777 TL").json()
        self.assertEqual([x["code"] for x in opts["services"]], ["cafe", "car_wash"])
