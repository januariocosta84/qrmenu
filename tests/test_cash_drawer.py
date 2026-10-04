import socket
import threading
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.permissions import Role
from apps.orders.services import place_order
from apps.payments.drawer import validate_printer_host
from apps.payments.models import CashDrawerOpening

from .test_ordering import make_restaurant, staff

KICK_PIN2 = bytes([0x1B, 0x70, 0x00, 0x19, 0xFA])


class FakePrinter:
    """A TCP server on localhost that records what a receipt printer would receive."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.received = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = b""
                while chunk := conn.recv(64):
                    data += chunk
                self.received.append(data)

    def wait_for(self, n, timeout=3):
        import time
        end = time.time() + timeout
        while len(self.received) < n and time.time() < end:
            time.sleep(0.02)
        return self.received

    def close(self):
        self.sock.close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@override_settings(CASH_DRAWER_ALLOW_PUBLIC_HOSTS=True, CASH_DRAWER_NETWORK_ENABLED=True)  # fake printer on 127.0.0.1
class CashDrawerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.r, self.rice, self.egg, self.tea, self.table = make_restaurant()
        self.printer = FakePrinter()
        self.r.cash_drawer_enabled = True
        self.r.printer_host = "127.0.0.1"
        self.r.printer_port = self.printer.port
        self.r.save()
        self.waiter = staff(self.r, "waiter", Role.WAITER)
        self.client.force_login(self.waiter)
        self.order = place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.tea.id, "quantity": 2}])

    def tearDown(self):
        self.printer.close()

    def test_cash_received_opens_drawer(self):
        resp = self.client.post(reverse("dashboard:order_mark_paid", args=["alpha", self.order.pk]), follow=True)
        self.assertEqual(self.printer.wait_for(1), [KICK_PIN2])
        self.assertContains(resp, "Cash drawer opened")
        log = CashDrawerOpening.objects.get()
        self.assertEqual((log.reason, log.success, log.user, log.order), ("payment", True, self.waiter, self.order))

    def test_pin5_command(self):
        self.r.drawer_pin = 1
        self.r.save()
        self.client.post(reverse("dashboard:order_mark_paid", args=["alpha", self.order.pk]))
        self.assertEqual(self.printer.wait_for(1), [bytes([0x1B, 0x70, 0x01, 0x19, 0xFA])])

    def test_api_reports_drawer_result(self):
        api = reverse("dashboard:api_order_payment", args=["alpha", self.order.pk])
        data = self.client.post(api, {}, content_type="application/json").json()
        self.assertTrue(data["drawer"]["opened"])
        self.assertEqual(data["order"]["payment_status"], "paid")

    def test_non_cash_payment_does_not_open_drawer(self):
        api = reverse("dashboard:api_order_payment", args=["alpha", self.order.pk])
        data = self.client.post(api, {"method": "bank_transfer"}, content_type="application/json").json()
        self.assertFalse(data["drawer"]["attempted"])
        self.assertEqual(self.printer.received, [])
        self.assertFalse(CashDrawerOpening.objects.exists())

    def test_disabled_drawer_is_never_contacted(self):
        self.r.cash_drawer_enabled = False
        self.r.save()
        self.client.post(reverse("dashboard:order_mark_paid", args=["alpha", self.order.pk]))
        self.assertEqual(self.printer.received, [])
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")

    def test_printer_offline_still_saves_payment(self):
        self.r.printer_port = free_port()  # nothing listening
        self.r.save()
        resp = self.client.post(reverse("dashboard:order_mark_paid", args=["alpha", self.order.pk]), follow=True)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")
        self.assertContains(resp, "cash drawer did not open")
        self.assertFalse(CashDrawerOpening.objects.get().success)

    def test_whole_table_opens_drawer_once(self):
        place_order(restaurant=self.r, table=self.table, lines=[{"menu_item": self.rice.id, "quantity": 1}])
        self.client.post(reverse("dashboard:table_mark_paid", args=["alpha", self.table.pk]))
        self.assertEqual(self.printer.wait_for(1), [KICK_PIN2])
        self.assertEqual(CashDrawerOpening.objects.count(), 1)

    def test_no_sale_opening_is_logged_and_role_checked(self):
        resp = self.client.post(reverse("dashboard:drawer_open", args=["alpha"]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.printer.wait_for(1), [KICK_PIN2])
        self.assertEqual(CashDrawerOpening.objects.get().reason, "no_sale")
        cook = staff(self.r, "cook", Role.KITCHEN)
        self.client.force_login(cook)
        self.assertEqual(self.client.post(reverse("dashboard:drawer_open", args=["alpha"])).status_code, 403)
        self.assertEqual(self.client.post(reverse("dashboard:api_drawer_open", args=["alpha"])).status_code, 403)


@override_settings(CASH_DRAWER_NETWORK_ENABLED=True)
class PrinterHostValidationTests(TestCase):
    def test_only_local_network_printers(self):
        self.assertEqual(validate_printer_host("192.168.1.50"), "192.168.1.50")
        self.assertEqual(validate_printer_host("10.0.0.7"), "10.0.0.7")
        from django.core.exceptions import ValidationError
        for bad in ("8.8.8.8", "127.0.0.1"):
            with self.assertRaises(ValidationError):
                validate_printer_host(bad)

    def test_settings_form_requires_host_when_enabled(self):
        cache.clear()
        r, *_ = make_restaurant()
        owner = staff(r, "owner", Role.OWNER)
        self.client.force_login(owner)
        data = {
            "name": "Alpha", "currency": "USD", "currency_symbol": "$", "service_charge_percent": "0",
            "default_language": "en", "default_prep_minutes": "15", "is_accepting_orders": "on",
            "cash_drawer_enabled": "on", "printer_host": "", "printer_port": "9100", "drawer_pin": "0",
            "receipt_prompt": "ask", "receipt_printer": "browser", "receipt_width": "48", "receipt_footer": "",
        }
        resp = self.client.post(reverse("dashboard:settings", args=["alpha"]), data)
        self.assertContains(resp, "IP address")
        resp = self.client.post(reverse("dashboard:settings", args=["alpha"]), {**data, "printer_host": "8.8.8.8"})
        self.assertContains(resp, "local network")
        resp = self.client.post(reverse("dashboard:settings", args=["alpha"]), {**data, "printer_host": "192.168.1.50"})
        self.assertEqual(resp.status_code, 302)
        r.refresh_from_db()
        self.assertEqual((r.printer_host, r.cash_drawer_enabled, r.service_charge_percent), ("192.168.1.50", True, Decimal("0")))
