"""
Customer-facing language support.

UI strings live in a plain dictionary so new languages can be added without
gettext tooling. Menu content (restaurant, categories, items, options) is
translated per-record via the `translations` JSON field on each model; see
`TranslatableMixin`.

NOTE: Tetum strings should be reviewed by a native speaker before launch.
"""

DEFAULT_LANGUAGE = "en"

LANGUAGES = [
    ("en", "English"),
    ("tet", "Tetum"),
    ("id", "Bahasa Indonesia"),
]
LANGUAGE_CODES = {code for code, _ in LANGUAGES}
TRANSLATED_LANGUAGES = [(c, n) for c, n in LANGUAGES if c != DEFAULT_LANGUAGE]

UI_STRINGS = {
    "en": {
        "menu": "Menu",
        "table": "Table",
        "add": "Add",
        "add_to_cart": "Add to Cart",
        "sold_out": "Sold out",
        "unavailable": "Unavailable",
        "quantity": "Quantity",
        "special_instructions": "Special instructions",
        "instructions_placeholder": "e.g. No spicy, no onions",
        "add_ons": "Add-ons",
        "cart": "Your order",
        "view_cart": "View order",
        "items": "items",
        "subtotal": "Subtotal",
        "service_charge": "Service charge",
        "total": "Total",
        "place_order": "Place Order",
        "placing_order": "Placing order…",
        "empty_cart": "Your cart is empty.",
        "remove": "Remove",
        "your_name": "Your name (optional)",
        "phone": "Phone number (optional)",
        "order_note": "Note for the kitchen (optional)",
        "payment": "Payment",
        "pay_at_counter": "Pay at the counter",
        "cash": "Cash",
        "order": "Order",
        "order_received_msg": "Your order has been received.",
        "status": "Status",
        "status_new": "Order Received",
        "status_accepted": "Order Received",
        "status_preparing": "Preparing",
        "status_ready": "Ready",
        "status_completed": "Completed",
        "status_cancelled": "Cancelled",
        "estimated_time": "Estimated preparation time",
        "minutes": "min",
        "back_to_menu": "Back to menu",
        "order_more": "Order more",
        "your_orders": "Your orders",
        "scan_to_order": "To order, please scan the QR code on your table.",
        "not_accepting": "This restaurant is not accepting orders right now.",
        "live_updates": "This page updates automatically.",
        "ready_msg": "Your food is ready!",
        "cancelled_msg": "This order was cancelled. Please ask the staff for help.",
        "network_error": "Connection problem. Please try again.",
        "language": "Language",
        "close": "Close",
        "search": "Search the menu",
        "no_results": "No dishes found.",
        "opening_hours": "Opening hours",
        "unpaid": "Pay at the restaurant",
        "paid": "Paid",
        "item_unavailable_error": "Some items are no longer available. Please review your order.",
        "paid_scan_again": "Your bill is paid. Thank you! To order again, please scan the QR code on your table.",
    },
    "tet": {
        "menu": "Menu",
        "table": "Meza",
        "add": "Aumenta",
        "add_to_cart": "Tau ba Karreta",
        "sold_out": "Hotu ona",
        "unavailable": "La disponivel",
        "quantity": "Kuantidade",
        "special_instructions": "Instrusaun espesiál",
        "instructions_placeholder": "ez. La bele ai-manas, la iha liis",
        "add_ons": "Aumenta tan",
        "cart": "Ita-nia pedidu",
        "view_cart": "Haree pedidu",
        "items": "item",
        "subtotal": "Subtotál",
        "service_charge": "Taxa servisu",
        "total": "Totál",
        "place_order": "Haruka Pedidu",
        "placing_order": "Haruka hela pedidu…",
        "empty_cart": "Ita-nia karreta mamuk.",
        "remove": "Hasai",
        "your_name": "Ita-nia naran (opsionál)",
        "phone": "Númeru telefone (opsionál)",
        "order_note": "Nota ba kozinha (opsionál)",
        "payment": "Pagamentu",
        "pay_at_counter": "Selu iha balkaun",
        "cash": "Osan-mihis",
        "order": "Pedidu",
        "order_received_msg": "Ami simu ona Ita-nia pedidu.",
        "status": "Estadu",
        "status_new": "Pedidu Simu Ona",
        "status_accepted": "Pedidu Simu Ona",
        "status_preparing": "Prepara hela",
        "status_ready": "Prontu",
        "status_completed": "Remata ona",
        "status_cancelled": "Kansela ona",
        "estimated_time": "Tempu preparasaun estimadu",
        "minutes": "min",
        "back_to_menu": "Fila ba menu",
        "order_more": "Pedidu tan",
        "your_orders": "Ita-nia pedidu sira",
        "scan_to_order": "Atu halo pedidu, favór scan kódigu QR iha Ita-nia meza.",
        "not_accepting": "Restaurante ne'e la simu pedidu agora.",
        "live_updates": "Pájina ne'e atualiza automatikamente.",
        "ready_msg": "Ita-nia hahán prontu ona!",
        "cancelled_msg": "Pedidu ne'e kansela ona. Favór husu ajuda ba funsionáriu.",
        "network_error": "Problema koneksaun. Favór koko fali.",
        "language": "Lian",
        "close": "Taka",
        "search": "Buka iha menu",
        "no_results": "La hetan hahán.",
        "opening_hours": "Oras loke",
        "unpaid": "Selu iha restaurante",
        "paid": "Selu ona",
        "item_unavailable_error": "Item balun la disponivel ona. Favór haree fali Ita-nia pedidu.",
        "paid_scan_again": "Ita-nia konta selu ona. Obrigadu! Atu halo pedidu tan, favór scan fali kódigu QR iha Ita-nia meza.",
    },
    "id": {
        "menu": "Menu",
        "table": "Meja",
        "add": "Tambah",
        "add_to_cart": "Tambah ke Keranjang",
        "sold_out": "Habis",
        "unavailable": "Tidak tersedia",
        "quantity": "Jumlah",
        "special_instructions": "Instruksi khusus",
        "instructions_placeholder": "mis. Tidak pedas, tanpa bawang",
        "add_ons": "Tambahan",
        "cart": "Pesanan Anda",
        "view_cart": "Lihat pesanan",
        "items": "item",
        "subtotal": "Subtotal",
        "service_charge": "Biaya layanan",
        "total": "Total",
        "place_order": "Pesan Sekarang",
        "placing_order": "Mengirim pesanan…",
        "empty_cart": "Keranjang Anda kosong.",
        "remove": "Hapus",
        "your_name": "Nama Anda (opsional)",
        "phone": "Nomor telepon (opsional)",
        "order_note": "Catatan untuk dapur (opsional)",
        "payment": "Pembayaran",
        "pay_at_counter": "Bayar di kasir",
        "cash": "Tunai",
        "order": "Pesanan",
        "order_received_msg": "Pesanan Anda telah diterima.",
        "status": "Status",
        "status_new": "Pesanan Diterima",
        "status_accepted": "Pesanan Diterima",
        "status_preparing": "Sedang Disiapkan",
        "status_ready": "Siap",
        "status_completed": "Selesai",
        "status_cancelled": "Dibatalkan",
        "estimated_time": "Perkiraan waktu penyiapan",
        "minutes": "mnt",
        "back_to_menu": "Kembali ke menu",
        "order_more": "Pesan lagi",
        "your_orders": "Pesanan Anda",
        "scan_to_order": "Untuk memesan, silakan pindai kode QR di meja Anda.",
        "not_accepting": "Restoran ini sedang tidak menerima pesanan.",
        "live_updates": "Halaman ini diperbarui secara otomatis.",
        "ready_msg": "Makanan Anda sudah siap!",
        "cancelled_msg": "Pesanan ini dibatalkan. Silakan hubungi staf.",
        "network_error": "Masalah koneksi. Silakan coba lagi.",
        "language": "Bahasa",
        "close": "Tutup",
        "search": "Cari menu",
        "no_results": "Tidak ada hidangan.",
        "opening_hours": "Jam buka",
        "unpaid": "Bayar di restoran",
        "paid": "Lunas",
        "item_unavailable_error": "Beberapa item tidak lagi tersedia. Silakan periksa pesanan Anda.",
        "paid_scan_again": "Tagihan Anda sudah dibayar. Terima kasih! Untuk memesan lagi, silakan pindai kode QR di meja Anda.",
    },
}


def normalize_language(code: str | None) -> str | None:
    if not code:
        return None
    code = code.lower().strip()
    if code in LANGUAGE_CODES:
        return code
    if code.startswith("in"):  # legacy Java/Android code for Indonesian
        return "id"
    base = code.split("-")[0]
    return base if base in LANGUAGE_CODES else None


def ui_strings(lang: str) -> dict:
    strings = dict(UI_STRINGS[DEFAULT_LANGUAGE])
    strings.update(UI_STRINGS.get(lang, {}))
    return strings


def ui_text(key: str, lang: str) -> str:
    return UI_STRINGS.get(lang, {}).get(key) or UI_STRINGS[DEFAULT_LANGUAGE].get(key, key)
