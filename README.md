# QR Menu: Restaurant Digital Menu & Ordering

> **No app. No registration. No complicated process. Just scan, order, and wait for the food.**

A mobile-first, multi-restaurant ordering platform. **Restaurant owners register themselves**, then build their menu, print QR codes and manage their own staff. Each table gets its own QR code. Customers scan it with their phone camera, browse the menu, add dishes to a cart and place an order. The order appears immediately in the restaurant's live **Kitchen Display System (KDS)**. As the kitchen works, the customer's status page updates by itself.

```
Customer:    Scan QR → View menu → Add to cart → Place order → Track status (live)
Restaurant:  New order (🔔) → Accept → Preparing → Ready → Completed
```

Built with Django 5.2, Django REST Framework, Django Channels (WebSockets) and PostgreSQL. The customer pages are plain HTML, CSS and vanilla JS: no build step and no framework download, so they load quickly on slow mobile networks.

---

## Contents

- [Features](#features)
- [Quick start (local)](#quick-start-local)
- [Try it on your phone](#try-it-on-your-phone)
- [Public platform: owner sign-up](#public-platform-owner-sign-up)
- [Platform console & subscriptions](#platform-console--subscriptions)
- [Onboarding a real restaurant](#onboarding-a-real-restaurant)
- [How it works](#how-it-works)
- [Roles & permissions](#roles--permissions)
- [REST API](#rest-api)
- [Configuration](#configuration)
- [Deployment (Linux VPS)](#deployment-linux-vps)
- [Cash drawer](#cash-drawer)
- [Extending](#extending)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Known limitations](#known-limitations)

---

## Features

**Customers (no login)**
- Scanning a table's QR code opens that restaurant's branded menu, with the table number already attached.
- Category tabs, search, large images (lazy-loaded WebP) and big touch targets.
- Quantity stepper, add-ons/extra ingredients and per-item special instructions ("No spicy").
- A cart with subtotal, optional service charge and total. Optional name, phone and kitchen note.
- Payment by "Pay at the counter" or "Cash"; nothing is charged online in v1.
- A live order page shows **Order Received → Preparing → Ready → Completed** and the estimated prep time. It updates over WebSockets, falls back to polling, and survives a page refresh.
- Items marked **Sold Out** grey out on every open menu straight away and can't be ordered.
- The menu is available in English, Tetum and Bahasa Indonesia, switchable from the menu.
- Several people at one table can order separately. Each gets their own order number, and all their orders belong to the same table session.

**Restaurant staff**
- A live **Kitchen queue** with New / Preparing / Ready columns, timers, notes and add-ons. It plays a sound and shows a pop-up for each new order, and has a full-screen mode for a kitchen tablet.
- **➕ New order (waiter order entry)**: for guests **without a smartphone**. The waiter picks the table (or *Counter / takeaway*) and either a new guest or an existing guest at that table, then taps dishes with add-ons, quantities and notes and sends the order to the kitchen. Server-side prices and sold-out checks apply exactly as for QR orders. These orders join that guest's bill, and the kitchen and order pages show "✍ by waiter". The screen is reachable from the sidebar, the Orders page, and the table page ("Add order" for a new guest, or "➕ Add" next to a guest).
- **Orders**: filter by status, table, date or customer. Each order has a detail page with its status history and a payment record.
- **One-tap payment**: when the waiter receives the money, they tap **💵 Mark paid** (on the orders list, the order page, the table page or a kitchen card), and the order becomes **Paid**. **Whole table paid** settles every unpaid order at a table at once and can free the table too. A pop-up asks how much cash the customer handed over, with quick buttons (Exact, $15, $20, $50, $100…), and shows the **change to give** in large print. The system refuses the payment if the cash is short, and records the amount received and the change on the payment. Partial or non-cash payments go through "Other method". The customer's page shows "✓ Paid" straight away.
- **Receipts**: after every payment a pop-up asks **"Print receipt? No / Yes, print"**, together with the change to give. *Yes* prints a receipt (restaurant, order number, table, items with add-ons, totals, cash received, change, who served, and a custom footer), either through the device's print dialog with a layout made for 80 mm thermal paper, or straight to the network receipt printer with no dialog. Restaurant settings choose **Ask** (default), **Always print** or **Never**. *Print bill* on the table page prints an unpaid bill, and the order page can reprint any receipt.
- **Cash drawer**: when a cash payment is recorded, the cash drawer opens automatically through a network receipt printer. There is also an **🗄 Open drawer** (no sale) button, a test button, and a log of every opening. See [Cash drawer](#cash-drawer).
- **Tables & QR**: create tables one at a time or in bulk. Download a table's QR code as PNG or SVG, print a single card, print all cards on A4, or **regenerate** a code so old printouts stop working.
- **Table sessions**: see every order at a table during one sitting with a running total, and close the session when the guests leave.
- **Menu**: categories, dishes, images, prices, prep times, add-ons and translations, plus a one-tap Available / Sold Out switch.
- **Restaurant profile**: name, logo, cover image, address, phone, opening hours, description, currency, service charge and default language. Ordering can be paused.
- **Staff accounts** with the roles Owner, Manager, Kitchen and Waiter/Cashier.
- **Reports**: orders, revenue, average order value, revenue per day, best sellers and CSV export.
- **Dashboard languages**: English, Português, Tetun and Bahasa Indonesia. Each staff member picks a language in the account menu (or on the login page); it's remembered on that device and doesn't change the customer menu.
- In-app notifications with an unread count.

**Platform (public, multi-tenant)**
- **Self-service sign-up**: owners register their restaurant at `/accounts/signup/`, confirm their email, and manage everything themselves.
- Each restaurant has its own menu, tables, QR codes, orders, staff and dashboard, and they are strictly isolated from each other.
- Owners sign in with their email. Password reset works by email, and staff accounts created by owners use a username.
- New restaurants stay private, with an owner-only preview, until they go live. Sign-up modes are **open**, **approval** and **closed**.
- Abuse protection: sign-up and login rate limits, a honeypot against bots, and per-restaurant limits on tables, dishes and staff.
- A platform admin (path configurable with `ADMIN_URL`) can approve or suspend restaurants and see owners and order counts.

---

## Quick start (local)

Requires **Python 3.10+**. SQLite is used automatically when `DATABASE_URL` isn't set.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      Linux/macOS:  source .venv/bin/activate
pip install -r requirements.txt

# Development settings
echo DJANGO_DEBUG=true > .env

python manage.py migrate
python manage.py seed_demo          # demo restaurant, 12 tables, full menu, staff logins
python manage.py createsuperuser    # optional: platform admin for /admin/
python manage.py runserver
```

`seed_demo` prints something like this:

```
Demo restaurant ready: /r/demo/
  Staff logins (password 'demo-pass-2024'): demo-owner, demo-kitchen, demo-waiter
  Dashboard: /dashboard/demo/
  Table 12 QR link (open on your phone): /r/demo/t/12/?k=DXBRqVCAitz8PkPh
```

- **Customer view:** open the printed *Table 12 QR link*.
- **Kitchen view:** log in at <http://127.0.0.1:8000/accounts/login/> as `demo-kitchen` and open *Kitchen queue*. Place an order from the customer view and watch it arrive.
- **Owner view:** log in as `demo-owner` to manage the menu, tables, staff and reports.

`runserver` uses Daphne (ASGI), so WebSockets work in development with no extra setup.

> The demo password is for local testing only. Never run `seed_demo` on a public server.

### Test data

```bash
python manage.py seed_testdata            # creates the data below; prints all logins
python manage.py seed_testdata --remove   # deletes exactly this test data, nothing else
```

It creates three restaurants, each with a full menu (with pictures), tables, a login for **every role** (`test-<restaurant>-owner|manager|kitchen|waiter`, password `Test-pass-2026`; change it with `--password`), 30 days of order history (cash payments with change, bank transfers, cancellations, waiter-entered orders, several guests per table), live orders on the kitchen screen, and invoices:

| Restaurant | Plan / subscription | Use it to test |
|---|---|---|
| Dili Bay Grill (TEST) | Standard, **active**, 5% service charge | Normal service, reports, paid invoices plus next month's open one |
| Kafe Atauro (TEST) | Pro, **trial ending in 5 days** | The trial banner and an open invoice |
| Warung Lospalos (TEST) | Standard, **expired** | Ordering blocked, an overdue invoice, the expired banner |

All test restaurants and users start with `test-`, which is how `--remove` finds them. On a server with `DJANGO_DEBUG=false` it refuses to run unless you add `--yes-production`.

## Try it on your phone

1. Find your computer's LAN IP address (for example `192.168.1.20`).
2. Add it to `.env`:
   ```
   DJANGO_DEBUG=true
   DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.20
   PUBLIC_BASE_URL=http://192.168.1.20:8000
   ```
3. Run `python manage.py runserver 0.0.0.0:8000`.
4. In the dashboard, open **Tables & QR → Print all QR codes** and scan one with your phone camera.

---

## Public platform: owner sign-up

When the system is deployed publicly, restaurant owners onboard themselves:

```
Landing page (/) → Register your restaurant → Dashboard (private setup) → Confirm email → LIVE
```

1. **Register** at `/accounts/signup/` with the restaurant name, your name, email and password. A unique URL is created automatically (e.g. `/r/warung-sederhana/`) and the owner is logged straight in.
2. **Set up privately.** The dashboard shows a *Get your restaurant ready* checklist (profile → categories → dishes → tables → print QR codes → staff). Until the restaurant is live, only its own staff can open the menu, which shows a "Preview" banner. Everyone else gets "not found", and no orders can be placed.
3. **Confirm the email.** The link in the confirmation email (valid for 3 days, with a "Resend email" button on the dashboard) makes the restaurant **live**.
4. **Approval (optional).** With `SIGNUP_MODE=approval`, confirmed restaurants also wait for a platform admin. The admin gets an email (`PLATFORM_ADMINS`), then goes to the platform admin → *Restaurants*, selects the restaurant and chooses *Approve / publish*. *Suspend* hides a restaurant again.

| `SIGNUP_MODE` | Who can register | When a restaurant goes live |
|---|---|---|
| `open` (default) | Anyone | As soon as the owner confirms their email |
| `approval` | Anyone | After email confirmation **and** admin approval |
| `closed` | Nobody (sign-up page is 404) | Restaurants are created by the platform admin |

Owners then manage everything themselves: menu, translations, photos, tables and QR codes, staff accounts and roles, payments and reports.

**Email is required** for public sign-up (confirmation links and password resets). Configure SMTP with the `EMAIL_*` variables. Without it, emails are only printed to the server console, which is fine for local testing.

## Platform console & subscriptions

Platform owners (superusers) manage the whole platform at **`/platform/`**. Your account menu in any dashboard has a *Platform console* link. Everyone else gets "not found".

| Page | What you can do |
|---|---|
| **Overview** | Restaurants (live / hidden), users, monthly recurring revenue, money collected in 30 days, open and overdue invoices, orders, subscription states, plan-change requests, newest restaurants |
| **Restaurants** | Search and filter by visibility or subscription state. On a restaurant's page: approve or suspend it, change its plan, set *paid until*, extend the trial, make it complimentary (free forever), cancel or reactivate, create invoices, approve plan-change requests, see usage against plan limits and staff, keep internal notes, and open its dashboard |
| **Users** | Search all accounts (owners, admins, unconfirmed, disabled). Enable or disable an account, email a reset link, set a temporary password (shown once), confirm an email, grant or remove platform-admin rights. You can't disable or demote yourself |
| **Invoices** | Open, overdue, paid and void invoices. *Mark paid* records the method (bank transfer, cash, mobile money, card) and a reference, which extends the subscription. *Void*. Printable invoice / PDF. **Generate due invoices** |
| **Plans** | Create and edit plans: monthly price, tables, dishes, staff accounts, active or hidden. Limits apply immediately |
| **Billing settings** | Default plan for new sign-ups, trial length (default 30 days), grace period (default 7 days), when to create renewal invoices, and the **payment instructions** shown to owners (bank account, mobile money…) |
| **Audit log** | Every console action, with who did it, when and from which IP |

**How a subscription works**

```
Sign-up → Trial (30 days) → invoice created 7 days before the end → owner pays (bank transfer…)
        → you click "Mark paid" → Active until the end of the paid period
Not paid → "Payment due" (7-day grace, ordering still works) → Expired: customers can't order
```

- Three starter plans are created: **Free** ($0: 5 tables, 30 dishes, 3 staff), **Standard** ($15: 30 / 200 / 15, the default) and **Pro** ($35: 300 / 500 / 50). Edit them in *Plans*.
- Free plans and complimentary restaurants never expire and are never invoiced.
- **Plan limits** stop owners from *adding* more tables, dishes or staff than their plan allows. Existing data is never deleted.
- When a subscription **expires**, the menu stays visible but ordering stops (QR and waiter orders). The owner can still log in, see the banner and pay. Paying an invoice reactivates it straight away, and billing restarts from the payment date rather than charging for the time it was switched off.
- **Owners** see a **Billing** page with their plan, status, usage bars, invoices (printable), your payment instructions, and *Switch to …*, which sends you a plan-change request by email and shows it on the overview. Dashboard banners warn them 7 days before the trial ends, and when payment is due or the subscription has expired.

**Automatic renewal invoices:** click *Generate due invoices*, or run the command daily:

```bash
python manage.py generate_invoices          # e.g. cron: 0 6 * * * cd /srv/qrmenu/app && .venv/bin/python manage.py generate_invoices
```

Payments are recorded manually in v1. `apps/billing` is designed so an online provider (card or QR payments) can mark invoices paid later.

## Onboarding a real restaurant

With public sign-up, owners register themselves (see above). The platform operator can also create restaurants directly:

```bash
# A) Command line: creates the restaurant, the owner account (asks for a password) and tables 1–20
python manage.py create_restaurant "Warung Sederhana" --owner budi --email budi@example.com --tables 20
```

**B) Platform admin:** go to `/admin/` → *Restaurants* → *Add*, and add the owner under *Restaurant staff*.

Then the owner logs in at `/accounts/login/` and:

1. **Restaurant**: uploads the logo and cover image and sets the address, hours, currency and service charge.
2. **Menu**: creates categories (Breakfast, Main Meals, Rice, Noodles, Seafood, Chicken, Beef, Snacks, Drinks, Desserts…), then adds dishes with photos, prices, add-ons and translations.
3. **Tables & QR**: creates the tables and clicks **Print all QR codes**, then places a card on each table.
4. **Staff**: adds kitchen and waiter accounts.

The restaurant is then ready to take orders.

---

## How it works

### QR code and access model

Each table has an active **QR code** that holds a random secret:

```
https://menu.example.com/r/<restaurant-slug>/t/<table-number>/?k=<secret>
```

(The long form `/restaurant/<slug>/table/<n>/?k=…` also works.)

When the code is scanned:

1. The server checks the secret against the table's active code.
2. The table's **bill** (table session) is started, or continued if one is already open.
3. The phone's session cookie gets permission to order for **that table's current bill**.
4. The browser is redirected to the same URL **without** the secret, so it isn't shared by accident.

**Shared tables:** several customers can sit at one table. Each phone scans for itself and gets its own anonymous customer id. On the table page, staff see **one bill per guest** ("Guest 1 · Ana", "Guest 2"…), each with its own **💵 Paid** button and change calculation. *Whole table paid* still settles everyone at once.

**The permission expires after payment, for each phone separately**, so nobody can keep ordering after leaving the restaurant:

| Event | Effect |
|---|---|
| All orders *this phone* placed since scanning are paid | That phone can no longer order. **Other customers at the table are not affected** |
| Staff press *Close session* / *Paid & free the table* | Every phone that scanned that table before then must scan again |
| `TABLE_ACCESS_HOURS` (6 h) pass since the scan | Access expires |

When every order on the table's bill is paid, the bill closes automatically for bookkeeping. This doesn't affect customers who are still at the table: their next order starts a new bill.

The customer sees *"Your bill is paid. Thank you! To order again, please scan the QR code on your table."* Scanning again (at the table) starts a new bill.

Opening the URL *without* a valid secret only shows the menu. That stops people from placing prank orders for tables they aren't sitting at. **Regenerate QR code** revokes the old secret immediately.

Customers never log in. Each order gets an unguessable UUID (`/o/<uuid>/`), and that UUID is the only way to see the order. Orders placed in a browser are also remembered in its session, so "Your orders" appears on the menu.

### Order lifecycle

```
new ──► preparing ──► ready ──► completed
 │  ╲                  │
 │   ► accepted ───────┘ (optional queue step)
 └──────────► cancelled ◄── (from new / accepted / preparing)
```

The kitchen's **ACCEPT ORDER** button moves an order straight from `new` to `preparing` and records `accepted_at`. Every change is written to `OrderStatusHistory` along with the staff member who made it and the time. All changes go through `apps/orders/services.py`, which enforces the allowed transitions and broadcasts real-time events once the database transaction commits.

**Prices are always calculated on the server.** The client sends only item IDs, quantities, add-on IDs and notes. The server checks that every item and add-on belongs to that restaurant, is available, and is in a visible category. Names and prices are copied onto the order, so later menu edits never change past orders.

### Real-time

Django Channels groups:

| WebSocket path | Who | Receives |
|---|---|---|
| `/ws/kitchen/<slug>/` | Logged-in staff of that restaurant only | `new_order`, `order_updated`, `availability` |
| `/ws/orders/<uuid>/` | The customer holding the order UUID | `order_status` (customer-safe fields only) |
| `/ws/menu/<slug>/` | Anyone viewing the menu | `availability` (sold out / back in stock) |

If a connection drops, clients reconnect with back-off. The KDS reloads its queue after reconnecting and once a minute as a safety net. Order status pages poll every 15 s while their socket is down.

### Data model

| Model | Purpose |
|---|---|
| `User` | Staff/admin accounts (customers never need one) |
| `Restaurant` | Profile, branding, currency, service charge, order-number counter |
| `RestaurantStaff` | Links a user to a restaurant with a **role** |
| `Table` | Table number, label, seats, active flag |
| `QRCode` | Secret token per table; old codes are kept as revoked |
| `TableSession` | One sitting at a table; at most one open session per table (DB constraint) |
| `MenuCategory` / `MenuItem` / `MenuItemOption` | Menu, add-ons, availability, prep time |
| `Order` / `OrderItem` / `OrderItemOption` | Orders with snapshot names and prices; per-restaurant sequential numbers (#1001…) |
| `OrderStatusHistory` | Audit trail of every status change |
| `Payment` | Payment records (manual in v1; ready for gateways) |
| `Notification` | Staff notifications (e.g. new order) |

Every table carries timestamps and has indexes for the dashboard's queries, such as `(restaurant, status, created_at)`. Translatable models (`Restaurant`, `MenuCategory`, `MenuItem`, `MenuItemOption`) store translations in a `translations` JSON column. The base columns hold the English text, which is the fallback when a translation is missing.

---

## Roles & permissions

Roles apply **per restaurant**. A user can be an owner at one restaurant and a waiter at another.

| Capability | Owner | Manager | Kitchen | Waiter / Cashier |
|---|:-:|:-:|:-:|:-:|
| Kitchen queue, update order status | ✓ | ✓ | ✓ | ✓ |
| View orders, tables, QR codes | ✓ | ✓ | ✓ | ✓ |
| Mark dishes sold out / available | ✓ | ✓ | ✓ | ✓ |
| Cancel orders | ✓ | ✓ | | ✓ |
| Enter orders for guests without a phone (➕ New order) | ✓ | ✓ | | ✓ |
| Record payments, close table sessions | ✓ | ✓ | | ✓ |
| Edit menu, tables & QR codes, restaurant profile | ✓ | ✓ | | |
| Reports & CSV export | ✓ | ✓ | | |
| Manage staff accounts | ✓ | | | |

The rules are defined in [apps/core/permissions.py](apps/core/permissions.py). If a user requests a restaurant they don't belong to, they get **404**, not 403, so one restaurant's staff cannot even confirm that another restaurant's dashboard exists.

### Security summary

- Staff authentication uses Django sessions, plus DRF token auth for API clients. Failed logins are rate-limited: 5 failures lock out that username from that IP for 15 minutes.
- Role-based permissions are checked on every view, API endpoint and WebSocket connection.
- Data is isolated per restaurant: every query is filtered by the restaurant taken from the URL *and* the user's membership. This is covered by tests.
- CSRF protection is enforced everywhere, **including the anonymous order endpoint**.
- Inputs are validated by serializers and forms, with model validators on prices, quantities (1–50) and table numbers.
- Image uploads are checked against an allow-list (JPEG/PNG/WebP), limited to 5 MB, protected against decompression bombs, then **re-encoded to WebP** under a random filename. EXIF data and any embedded payloads are discarded.
- Order changes are staff-only. Customers can only read their own order through its UUID.
- Rate limits: placing orders (10/min per IP), public reads (120/min) and API token requests.
- Production defaults: HTTPS redirect, HSTS, secure cookies, `X-Frame-Options: DENY`, no-sniff, and a strict referrer policy.

---

## REST API

All endpoints return JSON.

**Public (customers)**

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/public/r/<slug>/menu/?lang=tet` | Translated menu |
| POST | `/api/v1/public/r/<slug>/orders/` | Requires a QR-scanned session and a CSRF token |
| GET | `/api/v1/public/orders/<uuid>/` | Order status |

Example order request:

```json
{
  "items": [
    {"menu_item": 4, "quantity": 2, "options": [3], "note": "No spicy"},
    {"menu_item": 13, "quantity": 2}
  ],
  "customer_name": "Ana",
  "customer_phone": "",
  "note": "",
  "payment_method": "pay_at_counter"
}
```

It returns `201 {"token", "number", "status_url"}`. Errors: `403 scan_required`, `409 unavailable` (with the IDs of the unavailable items), `409 not_accepting`, `429` when rate-limited.

**Staff** (session or `Authorization: Token <key>`; get a token with `POST /api/v1/auth/token/` and `username`/`password`)

| Method | Path |
|---|---|
| GET | `/api/v1/r/<slug>/orders/?active=1&status=new,preparing&table=12&session=<id>&since=<iso>` |
| GET | `/api/v1/r/<slug>/orders/<id>/` |
| POST | `/api/v1/r/<slug>/orders/new/` with `{"table": 5, "guest": "", "items": [{"menu_item": 4, "quantity": 2, "options": [3]}], "customer_name": "", "note": ""}`: waiter order (`table` null = counter; `guest` empty = new guest) |
| POST | `/api/v1/r/<slug>/orders/<id>/status/` with `{"status": "preparing", "note": ""}` |
| POST | `/api/v1/r/<slug>/orders/<id>/payments/` with `{}` (cash, full balance → Paid), `{"tendered": "20.00"}` (response includes `change`) or `{"method": "cash", "amount": "5.00", "reference": ""}` for partial payments |
| POST | `/api/v1/r/<slug>/receipts/print/` with `{"orders": [12, 13]}`: print a receipt on the network receipt printer |
| GET | `/api/v1/r/<slug>/menu-items/` |
| POST | `/api/v1/r/<slug>/menu-items/<id>/availability/` with `{"is_available": false}` |
| GET / POST | `/api/v1/r/<slug>/notifications/` (POST marks them as read) |

To create API tokens for staff, a superuser can open `/admin/` → *Tokens*, or a client can call the token endpoint.

---

## Configuration

Settings are read from environment variables. A `.env` file in the project root is loaded automatically; see [.env.example](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_DEBUG` | `false` | Development mode |
| `DJANGO_SECRET_KEY` | (none) | **Required** in production |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated host names |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | (empty) | e.g. `https://menu.example.com` |
| `PUBLIC_BASE_URL` | (request host) | Base URL encoded into printed QR codes |
| `DATABASE_URL` | SQLite | e.g. `postgres://user:pass@host:5432/db` |
| `REDIS_URL` | (none) | Channel layer and cache. **Required for more than one worker process** |
| `NUM_PROXIES` | `0` | Set to `1` behind Nginx (needed for correct client IPs and rate limits) |
| `TIME_ZONE` | `Asia/Dili` | Used for reports and day boundaries |
| `MEDIA_ROOT` | `./media` | Where uploaded images are stored |
| `TABLE_ACCESS_HOURS` | `6` | How long a QR scan lets a phone order |
| `THROTTLE_ORDER_CREATE` | `10/min` | Order rate limit per IP |
| `PLATFORM_NAME` | `QR Menu` | Name shown on the landing page and in emails |
| `SUPPORT_EMAIL` | (empty) | Shown to owners (e.g. on suspended restaurants) |
| `SIGNUP_MODE` | `open` | `open`, `approval` or `closed` (see above) |
| `PLATFORM_ADMINS` | (empty) | `Name <email>, …`: gets "waiting for approval" emails |
| `ADMIN_URL` | `admin/` | Path of the platform admin. Use something non-obvious in production |
| `SIGNUPS_PER_IP_PER_HOUR` | `5` | Sign-up rate limit |
| `MAX_TABLES_PER_RESTAURANT` / `MAX_MENU_ITEMS_PER_RESTAURANT` / `MAX_STAFF_PER_RESTAURANT` | `300` / `500` / `50` | Abuse limits per restaurant |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` | (console) | SMTP for confirmation and password-reset emails |
| `CASH_DRAWER_NETWORK_ENABLED` | `false` | Allow the server to open cash drawers on network printers. **Self-hosted only**; see [Cash drawer](#cash-drawer) |

> Set `PUBLIC_BASE_URL` to your final domain **before** printing QR codes. The printed codes contain the full URL.

---

## Deployment (Linux VPS)

`deploy/setup.sh` installs QR Menu on an Ubuntu/Debian server, **including a server that already runs other apps. It never touches them:**

- It installs only **missing** packages (`apt --no-upgrade`), so nothing already installed is upgraded or restarted.
- It **adds** its own nginx site, systemd service, user, PostgreSQL database and role, cron file and certificate, all named `qrmenu`. It never edits, removes or restarts anything else: nginx is only *reloaded*, after `nginx -t` passes, and QR Menu's site is removed again if the test fails.
- It picks a **free local port** (8170–8199) and an **empty Redis database** (7–15), so nothing collides.
- It gets the HTTPS certificate with certbot's *webroot* mode, which doesn't touch other sites' nginx config.
- It **stops with a clear message** if anything with the same name exists, if ports 80/443 belong to something other than nginx, or if the domain is already configured elsewhere.

```bash
# 0. DNS: A record  qrmenu.timorstore.com → <server IP>
# 1. On the server (as root)
git clone https://github.com/januariocosta84/qrmenu.git /srv/qrmenu/app
cd /srv/qrmenu/app

# 2. Dry run: reports what it would add and any conflicts, and changes NOTHING
MODE=check DOMAIN=qrmenu.timorstore.com bash deploy/setup.sh

# 3. Install
DOMAIN=qrmenu.timorstore.com EMAIL=you@example.com bash deploy/setup.sh

# 4. Platform owner account
sudo -u qrmenu .venv/bin/python manage.py createsuperuser
```

**Update:** `cd /srv/qrmenu/app && sudo -u qrmenu git pull && sudo DOMAIN=qrmenu.timorstore.com EMAIL=you@example.com bash deploy/setup.sh`
**Logs:** `journalctl -u qrmenu -n 100 --no-pager`
**Remove (QR Menu only):** `sudo bash deploy/uninstall.sh` keeps the database and images and backs them up to `/root`. Add `PURGE=1` to delete everything.

**Backups:** back up the `qrmenu` PostgreSQL database (`pg_dump qrmenu`) and `/srv/qrmenu/media`.

**Before opening sign-up to the public, check that you have:**
- [ ] Working SMTP in `/srv/qrmenu/app/.env`: register a test restaurant and confirm that the email arrives
- [ ] `SIGNUP_MODE` chosen (`approval` is the default from the script), and `PLATFORM_ADMINS` / `SUPPORT_EMAIL` set
- [ ] A strong platform-owner password; the Django admin path is random (see `ADMIN_URL` in `.env`)
- [ ] Payment instructions in Platform console → Billing settings
- [ ] Nightly database and media backups
- [ ] Terms of service and a privacy policy for owners (not included)

## Cash drawer

There are two ways to open a cash drawer. Restaurants choose in **Restaurant settings → Receipt printer & cash drawer**, which also has a step-by-step guide and a **Print test slip** button.

### Through the cashier computer's printer (works on the cloud)

The drawer plugs into the receipt printer installed on the cashier computer (USB, Wi-Fi or Bluetooth), and the printer driver's "open cash drawer before printing" option opens it whenever something prints. With **A cash drawer is plugged into the receipt printer** switched on, every cash payment and every **Open drawer (no sale)** prints a small 80/58 mm slip (reason, order numbers, time, staff), so the drawer opens and there's a paper trail. In "Always print" receipt mode the receipt itself opens the drawer, so no extra slip prints. Openings are logged like network openings. Tip: a Chrome shortcut with `--kiosk-printing` prints without the print dialog.

### Network printer (self-hosted only)

> **Self-hosted only.** It's off unless `CASH_DRAWER_NETWORK_ENABLED=true`. On a public cloud server, keep it off: the server can't reach printers inside restaurants anyway, and enabling it would let restaurant accounts make your server open connections to internal network addresses. When it's off, the drawer settings and buttons are hidden, and payments work as normal.

The drawer plugs into a **network receipt printer** (Ethernet or Wi-Fi) using the printer's RJ11 "DK" port. The server sends the standard ESC/POS drawer-kick command (`ESC p 0 25 250`) to the printer's raw port. This works with Epson TM series, Xprinter, Bixolon, Rongta, and Star printers in ESC/POS mode.

**Setup**
1. Find the printer's IP address. Hold FEED while switching the printer on to print a self-test page, or check your router. Give the printer a fixed IP in the router so the address doesn't change.
2. In **Restaurant settings**, tick **Cash drawer enabled**, enter the **printer host** (for example `192.168.1.50`) and **port** (`9100`), then save.
3. Click **Test cash drawer**. If it doesn't open, try **Pin 5**, since some drawers use the other pin.

**What triggers it**

| Action | Opens drawer? |
|---|---|
| 💵 Mark paid / Cash received (orders list, order page, kitchen card) | ✓ |
| 💵 Whole table paid | ✓ (once) |
| "Other method" with method = Cash | ✓ |
| Bank transfer, card or QR payment | no |
| 🗄 Open drawer (top bar, no sale) | ✓ (owner, manager, waiter only) |

Every opening is logged with the user, the reason, the order and whether it succeeded. You can see the log at the bottom of Restaurant settings. If the printer is off or unreachable, **the payment is still saved**. The waiter sees a warning, and the failure is logged.

For safety, the printer address must be on the local network (`192.168.x.x`, `10.x.x.x`, `172.16–31.x.x`).

> **Hosting note:** the *server* connects to the printer, so the server must be able to reach the printer's IP. This works out of the box when the app runs on a computer in the restaurant. If you move the app to a cloud VPS, connect the VPS to the restaurant network with a VPN (for example WireGuard or Tailscale), or run a small local relay.

## Extending

### Add a language
1. Add it to `LANGUAGES` in [apps/core/i18n.py](apps/core/i18n.py) and add a block of strings to `UI_STRINGS`.
2. That's all. Menu translation fields for the new language appear in the dashboard forms automatically, and no migration is needed because translations live in a JSON column.

### Dashboard translations
The staff dashboard uses Django's gettext catalogs in [locale/](locale/) (`pt`, `tet`, `id`); the customer menu keeps its own strings in `apps/core/i18n.py`. After changing dashboard text (`{% translate %}` in templates, `_()` in Python, `_()` in `static/js/dashboard.js`, `kds.js`, `pos.js`), update and compile the catalogs (needs the `gettext` package):

```bash
python manage.py makemessages -l pt -l tet -l id --no-location --ignore=.venv --ignore=staticfiles --ignore=media --ignore=tests
python manage.py makemessages -d djangojs -l pt -l tet -l id --no-location --ignore=.venv --ignore=staticfiles --ignore=media --ignore=static/js/menu.js --ignore=static/js/order_status.js
# translate the new empty msgstr entries in locale/*/LC_MESSAGES/*.po, then:
python manage.py compilemessages --ignore=.venv
```

Django has no Tetun catalog of its own, so its form errors and date names are listed in [apps/core/django_strings.py](apps/core/django_strings.py) and translated only in `locale/tet`. To add a dashboard language, add it to `LANGUAGES` in [config/settings.py](config/settings.py) and run the commands above with `-l <code>`.

### Add an online payment method
The payment layer is in [apps/payments/](apps/payments/):
1. Subclass `PaymentProvider` in `providers.py`. Implement `start()` to create the payment and return a redirect URL or QR payload, and `handle_webhook()` to verify the signature and mark the `Payment` as succeeded.
2. Register it in `PROVIDERS` under its `PaymentMethod` code (`bank_transfer`, `card`, `online_gateway`, `qr_payment` are already defined).
3. Add the code to `PaymentMethod.CUSTOMER_CHOICES` to offer it at checkout.

`Order.payment_status`, `Payment.provider_reference` and `Payment.metadata` are already in place for this.

### Other natural next steps
Required option groups (e.g. "choose a size"), a kitchen ticket printer, SMS/WhatsApp notifications, menu scheduling (breakfast hours), discounts, and per-station queues (bar vs. kitchen).

---

## Testing

```bash
python manage.py test tests
```

The suite in [tests/test_ordering.py](tests/test_ordering.py) covers:

- QR access (including the view-only plain URL and revoked codes)
- QR access expiring after payment (per phone, unaffected strangers at shared tables, staff freeing the table, cancelled orders, scanning again) and per-guest bills
- server-side price calculation, add-ons and service charge
- sold-out, cross-item and cross-restaurant tampering
- CSRF enforcement for anonymous customers
- order rate limiting
- the status workflow and invalid transitions
- restaurant isolation
- role permissions
- payments (one-tap, partial, whole table, double-payment and role checks)
- waiter order entry: table/counter, new vs existing guest, adding to a phone guest's bill, validation, roles, isolation
- cash change: change calculation, short cash refused, whole-table change, comma decimals
- public sign-up: email confirmation, private preview, approval mode, closed mode, honeypot, duplicate emails, rate limits, unique URLs, password reset, per-restaurant limits, drawer disabled on public deployments
- platform console & billing: superuser-only access, every restaurant and user action, the audit log, plans and limits, trial / grace / expired states and ordering, invoice numbering and periods, paying to extend, generating due invoices, plan-change requests, the owner Billing page and banners
- receipts: the print prompt after each kind of payment, Ask/Always/Never modes, the receipt contents, bills for unpaid orders, the exact ESC/POS bytes and paper widths, an offline printer, restaurant isolation
- the cash drawer: the exact ESC/POS bytes sent to a fake printer, pin 5, offline printer, non-cash payments, no-sale roles, printer IP validation
- menu editing with translations
- image re-encoding and rejection of fake images
- end-to-end WebSocket delivery to the kitchen and to customers

---

## Project structure

```
config/            settings, URLs, ASGI (HTTP + WebSocket routing), WSGI
apps/
  core/            i18n strings, permissions/roles, image processing, middleware,
                   template tags, management commands (seed_demo, create_restaurant)
  accounts/        staff User model, rate-limited login
  restaurants/     Restaurant, RestaurantStaff, Table, QRCode, TableSession, QR rendering
  menu/            MenuCategory, MenuItem, MenuItemOption
  orders/          Order models, services (business rules), WebSocket consumers, realtime fan-out
  payments/        Payment model, provider abstraction
  storefront/      customer pages + public API, QR session access
  dashboard/       staff pages, KDS, staff API, forms
templates/         storefront/, dashboard/, accounts/
static/            css/ and js/ (vanilla, no build step)
deploy/            nginx.conf, systemd unit
tests/             test suite
```

---

## Known limitations

- **The Tetum translations need a native speaker's review.** This covers the UI strings in `apps/core/i18n.py`, the demo menu names, and the dashboard catalogs in `locale/` (the Portuguese and Indonesian dashboard text should get a quick review too).
- Without `REDIS_URL`, real-time updates only work within **one** server process. Set Redis in production.
- No billing or subscriptions for restaurants yet. The platform is free to use until you add a plan system.
- No built-in terms of service or privacy policy pages, and no self-service account deletion.
- For a public cloud deployment, the cash drawer needs a different approach: a small local print agent in each restaurant, or a printer with a browser API (Epson ePOS / Star WebPRNT).
- Add-ons are optional multi-select extras; there are no required or single-choice option groups yet.
- Online payments are prepared for but not implemented. In v1, staff mark orders paid when they receive the money.
- The cash drawer needs a **network** receipt printer that the server can reach (see the hosting note in [Cash drawer](#cash-drawer)). USB and Bluetooth printers aren't supported yet.
- Customer browser notifications (push) aren't included. The status page updates live while it is open, and on refresh.
