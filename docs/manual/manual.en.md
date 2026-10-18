# QR Menu User Manual

How to use QR Menu (qrmenu.timorstore.com) day to day, for restaurant owners, managers, kitchen staff, waiters and cashiers, and the customers who order from their phones.

## Roles at a glance

Every staff account has one role per restaurant, set by the owner on the Staff page. The role decides which pages appear in the left menu. Pages marked Standard or Pro need that plan; on the Free plan they show a PRO badge and an upgrade page.

| Page | Owner | Manager | Waiter / Cashier | Kitchen |
| --- | --- | --- | --- | --- |
| Overview | Yes | Yes | Yes | Yes |
| Kitchen queue | Yes | Yes | Yes | Yes |
| New order (take orders at the counter) | Yes | Yes | Yes | No |
| Orders | Yes | Yes | Yes | Yes |
| Tables & QR (bills) | Yes | Yes | Yes | Yes |
| Cash register (Pro) | Yes | Yes | Yes | No |
| Revenue entries (Pro) | Yes | Yes | Yes | No |
| Car wash (when switched on) | Yes | Yes | Yes | Yes |
| Menu: mark dishes sold out | Yes | Yes | Yes | Yes |
| Menu: add and edit dishes | Yes | Yes | No | No |
| Create tables, print QR codes | Yes | Yes | No | No |
| Reports | Yes | Yes | No | No |
| Analytics (Standard, Pro) | Yes | Yes | No | No |
| Expenses & profit (Pro) | Yes | Yes | No | No |
| Quotations (Pro) | Yes | Yes | No | No |
| Restaurant settings | Yes | Yes | No | No |
| Staff, Branches (Pro), Ordering API (Pro) | Yes | No | No | No |
| Billing, Business profile | Yes | No | No | No |

Cancelling orders and taking payments are open to owners, managers and waiters/cashiers, not kitchen staff.

## Getting started

Open **qrmenu.timorstore.com** on a phone, tablet or computer and click **Log in**. Staff accounts are made by the owner; only owners register themselves.

1. **Log in** with your email or username and password. Forgot it? Click **Forgot your password?** and follow the emailed link.
2. **Choose a restaurant.** If you work at more than one restaurant or branch, the dashboard lists them; click the one you are working at. You can switch later from your avatar (top right) → **Switch restaurant**.
3. **Pick your language.** Click the flag at the top: English, Português, Tetun or Bahasa Indonesia. The dashboard remembers it for your account.
4. **Turn on the sound** on every device that should ring for orders. Browsers only play sound after you allow it once: press **Enable sound** on the Kitchen queue page, or **Turn on the sound** in the red or green alert bar.

![The login page](login)

**Things you see on every page**

- **Live / Offline** pill (top bar): Live means new orders arrive by themselves. Offline means the connection dropped; it reconnects on its own.
- **Bell**: notifications such as new and cancelled orders. Open it to see the list and mark them read.
- **Red bar**: new orders waiting to be accepted. It beeps every 5 seconds until someone presses **Accept**.
- **Green bar**: food ready to pick up (waiters, cashiers, managers). It chimes every 15 seconds until someone presses **Picked up**.
- **View public menu** (bottom of the left menu): opens your menu as customers see it.
- On a phone, the left menu opens with the **☰** button and the main pages sit in the bottom bar. The **+** button there opens **New order**.
- To change your password or log out, click your avatar (top right).

![The Overview page on a computer: today's orders and revenue, quick links and the latest orders](overview)

![The dashboard on a phone, with the red new-order bar, the green ready-to-pick-up bar and the bottom menu](phone_dashboard)

## Customers: ordering from the table

Customers need no app and no account: they scan the QR code on the table with the phone camera and order from the menu that opens. Staff can share these steps with guests who ask.

1. **Scan the QR code on the table.** The menu opens with the table number at the top. Change the language with the language button (English, Tetun, Bahasa Indonesia).
2. **Choose dishes.** Tap a dish to see its photo and description, pick add-ons if offered, set the quantity, add a special instruction (e.g. “no spicy”), then **Add to Cart**. Sold-out dishes can't be added.
3. **Check the order.** Tap **View order**, change quantities or remove items. A name, phone number and a note for the kitchen are optional.
4. **Place Order.** The order goes straight to the kitchen screen and the staff dashboards.
5. **Follow the order.** The order page updates by itself: Order Received → Preparing → Ready. When it says “Your food is ready!”, staff bring it or call the guest.
6. **Order more** from the same page at any time; all orders from the table go on one bill.
7. **Pay** at the counter or to the waiter, by cash or the methods the restaurant accepts. The bill can be printed for the table.

![The customer's menu on a phone: categories, search, dish photos and add-ons](customer_menu)

![The customer's order page: it follows the order from received to completed](customer_order)

**Good to know**

- A scan lets that phone order for that table for up to 6 hours. After the bill is paid, or staff free the table, the phone must scan the QR code again to order.
- The public menu link without a table (e.g. shared online or found on Google) shows the menu only; ordering always needs the table's QR code.
- If the restaurant has a car wash, the table menu shows a **Car wash** tab: enter the plate number and vehicle type, and the wash is added to the same bill.

## Owners: setting up your restaurant

An owner registers once, builds the menu and tables, adds staff, and the restaurant goes live after the platform team approves it. The **Overview** page shows a “Get your restaurant ready” checklist that ticks off as you go.

### 1. Register

1. On the home page click **Register your restaurant** (or **Try the demo** first to look around a sample café).
2. Enter restaurant name, your name, email, phone (optional) and a password, and confirm you own or manage the restaurant.
3. Open the confirmation link sent to your email. Until then the dashboard asks you to verify your email.
4. The platform team reviews new restaurants. While you wait (“Waiting for approval”), you can set up everything below; customers cannot order yet.

![Try the demo: fill in the form and the demo link arrives by email](demo)

### 2. Business profile

Click **Complete now** on the yellow banner, or open **Business profile**. Enter the business registration number (e.g. SERVE certificate or tax number), owner name, address and phone. The team verifies it; a verified profile is needed before you can add branches.

### 3. Restaurant settings

Open **Restaurant** in the left menu and save after each change.

| Section | What to set |
| --- | --- |
| Profile | Name, description, logo, cover photo, address, phone, email, opening hours |
| Ordering | Accepting orders on/off, currency and symbol, service charge %, default preparation time, default menu language, “Show my menu on Google” |
| VAT | VAT on/off, rate, included in prices or added, label (VAT/IVA), tax ID printed on receipts |
| Receipts & printer | Ask / always / never print after payment, browser or network receipt printer, paper width (58 or 80 mm), receipt footer |
| Cash drawer | Open the drawer through the receipt printer, PIN, test slip |
| Bills | Print one combined bill or separate bills per service |
| Car wash | Switch on, car wash name, automatic tickets (see Car wash) |

Turn **Accepting orders** off to stop new orders for a while (e.g. kitchen closed); customers see that the restaurant is not taking orders.

![Restaurant settings: the Profile section](settings)

### 4. Menu

1. Open **Menu** → add a **category** (e.g. Rice, Drinks). Each category has a service: **Café** (goes to the kitchen) or **Car wash**. Hide a category to take it off the menu without deleting it.
2. **Add dish**: category, name, description, price, photo (JPEG, PNG or WebP up to 5 MB; resized automatically), preparation minutes, position (order on the menu), available.
3. **Add-ons & extras**: options customers can tick, e.g. “Fried Egg +$0.50”; price 0 for free options like “No onions”. Save to get two more empty rows.
4. **Translations**: add the name and description in Português, Tetun and Bahasa Indonesia; customers see their own language, English otherwise.
5. **Sold out**: on the Menu page, switch a dish to **Sold Out** and back. Any staff member can do this during service.

![The Menu page: categories with their dishes, the Available / Sold Out switch and the edit buttons](menu)

### 5. Tables and QR codes

1. Open **Tables & QR** → **Add a table** (numbers or short codes like 12 or A-3), or **Add many tables** to create a numbered range at once.
2. **Print all QR codes**, or open one table for its QR (download PNG). Each code shows your logo and name in the middle; print it at least 4 cm wide so phones scan it easily.
3. Put each code on its table. **New code** makes a fresh QR for a table: the old printed one stops working at once.
4. The Tables page shows each table as **Free** or **Occupied** with its open bill.

![Tables & QR codes: each table with its QR code, status and Print button](tables)

### 6. Staff and going live

- Add kitchen and waiter accounts on **Staff** (see Owner only).
- Open **View public menu** on your phone to check what customers see.
- When the platform team approves the restaurant, the banner disappears and the status shows **Live**: customers can order from the table QR codes.

## Waiters and cashiers

Waiters and cashiers answer new orders, take orders for guests without a phone, bring ready food and take payment. Keep the dashboard open on your phone or the counter tablet with the sound on.

### New order alarm

When a customer orders, every staff screen beeps and a red bar appears: “1 new order is waiting to be accepted”. It beeps every 5 seconds until someone presses **Accept** in the bar or on the Kitchen queue. Tap the order number to open it.

![The red bar (new orders waiting, with Accept) and the green bar (ready to pick up, with Picked up)](alarms)

### Taking an order (New order)

For guests without a smartphone, or takeaway at the counter.

1. Open **New order** (the **+** button on a phone).
2. Choose the **Table**, or **Counter / takeaway**, and the guest (**New guest** or one already at the table).
3. Tap dishes to add them (each shows its photo); use **More / Less** for quantities and **Note** for a dish (e.g. “no spicy”). **Search dishes** finds a dish fast.
4. Optional: the guest's name and a note for the kitchen.
5. Press **Send to kitchen**. The order goes on the table's bill like a QR order. Tick **Print one bill for this order** to print the bill straight away.

![New order: choose the table, tap dishes, check the order on the right, then Send to kitchen](new_order)

### Food ready: pick it up

When the kitchen presses **MARK AS READY**, you hear a “ding-dong” and see “Order #41 · Table 5 is ready. Please pick it up.” The green **Ready to pick up** bar chimes every 15 seconds until someone presses **Picked up** after serving.

### Tables and bills

1. Open **Tables & QR** and tap a table. **Current bill** lists every order and what is unpaid.
2. **Print bill** prints the unpaid orders. With café and car wash on one table, choose **One combined bill** or **Separate bill per service**. Earlier bills can be printed again as **Reprint (COPY)**.
3. **Add order** opens New order for this table.
4. After paying, **Paid & free table** closes the bill. Everyone who scanned the table must scan again to order.

### Taking payment

1. On the table, the order, or the Kitchen queue, press **Mark paid**.
2. For cash, enter the **Cash received from customer**; the screen shows the **change to give** in large type. **Exact amount** skips the count.
3. Orders from one table that were placed together (e.g. café + car wash) are paid together as one sum.
4. For bank transfer or another method, open the order → **Record payment** → choose the method and amount (partial payments are allowed).
5. Print the receipt when asked. The restaurant setting decides whether the receipt prints by itself, asks, or never prints.

### Cancelling an order

Open the order → **Cancel**, add a short reason (e.g. “customer left”). Kitchen staff cannot cancel orders.

### Cash register (Pro)

1. **Start of day**: open **Cash register**, count the notes and coins you put in the drawer as change, enter it as **Opening change**, then **Open cash register**.
2. During the day: record **Cash in** or **Cash out** with a reason (e.g. “bought ice”).
3. **End of day**: count all cash in the drawer, enter **Cash counted** and press **Close & reconcile**. The report shows Expected, Counted and the Difference (Exact, Over or Short). Closing can't be undone.

![Cash register: counting the opening change at the start of the day](cash_register)

### Revenue entries (Pro, branches)

Sales that didn't go through QR Menu (e.g. a catering job paid by transfer) can be added on **Revenue** → **Record revenue** with date, amount, payment method and a note, so reports stay complete.

## Kitchen staff

The kitchen works from one screen, **Kitchen queue**, best on a tablet or TV left open all day. Orders move across three columns as you press the big buttons.

1. **Set up the screen once**: open **Kitchen queue**, press **Enable sound** (it changes to “Sound on”), then **Full screen**. On a phone, switch columns with the New / Preparing / Ready tabs.
2. **NEW ORDERS**: a new order appears at the top with a beep, flashing for a few seconds. Each card shows the table (or Counter), the dishes, add-ons and notes. Press **ACCEPT ORDER** when you start: the order moves to Preparing and the red alarm stops on every screen.
3. **PREPARING**: a timer runs on each card. It turns red when the order takes longer than its preparation time.
4. **READY**: press **MARK AS READY** when the food is plated. Waiters hear a chime and see “ready to pick up” on their phones.
5. **COMPLETED**: once the food has gone to the table, press **COMPLETED** (or the waiter presses **Picked up**). The card leaves the screen.

![The Kitchen queue: New orders, Preparing and Ready columns with their big buttons](kitchen)

**Also on the kitchen screen**

- **Sold out**: kitchen staff can mark dishes **Sold Out** on the **Menu** page so customers can't order them; switch them back when available.
- Car wash orders never appear here; they have their own screen.
- The screen catches up by itself after the Wi-Fi drops (“Could not load orders — retrying…” means it is reconnecting).
- Managers and waiters see **Cancel** and **Mark paid** on the cards; kitchen accounts don't.

## Managers and owners

Managers run the day and look after the numbers; they have every page except staff, branches, API, billing and business profile.

### Orders

**Orders** lists every order from QR codes and waiters. Filter by status (New, Preparing, Ready, Completed, Cancelled), table, dates, or search by order number or customer name. Open an order to see its history, change its status, record a payment, print the receipt or cancel it.

### Reports

**Reports** shows, for the dates you pick: revenue, number of orders, average order, cancelled orders, VAT collected, revenue per day, revenue by service (café, car wash) and best sellers. **Export CSV** downloads the figures for Excel.

![Reports: revenue, orders, average order, revenue by service and best sellers](reports)

### Analytics (Standard, Pro)

Pick 7, 30 or 90 days or a date range. You see revenue per day against the previous period, busy times by weekday and hour, best-selling dishes, sales by category, dishes nobody ordered, how customers pay, where orders come from (QR, waiter, counter, API) and kitchen speed (time from order to ready, share ready on time).

![Analytics: revenue per day against the previous period, and busy times](analytics)

### Expenses & profit (Pro)

1. **Add an expense**: date, what was bought, category, amount and how it was paid.
2. Choose Today, This week, This month or This year: the page shows revenue, expenses and **Profit** (or **Loss**) = revenue from completed orders − expenses, with expenses by category.
3. **Export CSV** for your bookkeeping.

![Expenses & profit: today, this week, month and year, the day-by-day table and Add an expense](expenses)

### Quotations (Pro)

Price offers for catering, events and procurement bids, printed or saved as PDF.

1. **Company profile** (once): Quotations → **Company profile**. Write about your business, or press **Start from an example** and edit it. A line starting with ## becomes a heading; lines starting with - become a list.
2. **Digital signature** (once): on the same page, draw your signature or upload a photo of it, add the company stamp (optional), the signer's name and position, then **Save signature**.
3. **New quotation**: fill in the client (organisation, contact, address, tender reference), subject, event date, number of guests, dates, document language (English, Português, Tetun, Bahasa Indonesia) and status (Draft, Sent, Accepted, Rejected).
4. **Items**: **Add from menu** (one dish), **Add all menu items** (every dish at once, car wash excluded), or **Add custom line** (delivery, staff, equipment). Edit the unit, quantity and price of any line; the trash button removes it, **Remove all lines** clears the list. Quantity starts at the number of guests.
5. Set discount, VAT and terms (payment, bank details). Tick **Apply my digital signature and stamp** to sign it, then **Save quotation**.
6. **Print / PDF** opens the document in this order: company profile, the quotation with totals and amount in words, then menu pages with photos of the quoted dishes. Untick **Add company profile** or **Add menu pages with photos** to leave them out, then **Print / save as PDF**.
7. **Duplicate** copies a quotation for a similar job; the copy starts unsigned.

![The quotation editor: Add from menu, Add all menu items, Add custom line, and the editable lines](quotation_edit)

![Company profile and digital signature page](company_profile)

![Print / PDF: the company profile page comes first, with the options to leave pages out](quotation_print)

## Car wash

A café with a car wash runs both from one account: car wash orders skip the kitchen, get a ticket for the wash team, and can go on the same bill as the food.

**Set up (owner or manager)**

1. **Restaurant** settings → Car wash: switch it on, give it a name (e.g. “Timor Car Wash”), and choose whether tickets print automatically.
2. **Menu** → add a category with the service **Car wash** and add your packages (e.g. Basic Wash, Full Wash + Interior). A package can have its own price for motorbikes and pickups/SUVs; empty means the normal (car) price.
3. Open the **Car wash** page (it appears in the left menu) and print the **Walk-in car wash QR** for the wash bay or counter. **New code** replaces it; the old print stops working.

**Every day**

- **Customers at a table** order a wash from the **Car wash** tab of the table menu, with plate number and vehicle type; it goes on the table's bill.
- **Walk-in customers** scan the car wash QR; their order is billed under the plate number.
- **At the counter**: on the Car wash page, fill in **New car wash order** (plate, package, vehicle type, table or walk-in, customer name) → **Add car wash**. Waiters can also add packages on the **New order** screen.
- **Ticket**: print it for the wash team from the order row. With automatic tickets on, keep the Car wash page open and tickets print by themselves.
- **To table**: moves an unpaid walk-in wash onto a table's bill.
- **Paying**: walk-in cars waiting to pay are listed at the top → **Bill / pay**. A wash ordered with food is paid together with it. Paid car wash orders complete by themselves.
- **Bills**: print one combined bill (café and car wash sections with subtotals) or a separate bill per service; each gets its own number (R-, C-, W-) and can be reprinted as COPY.
- **Reports** split revenue by service, so you see café and car wash takings apart.

## Owner only

These pages are for the owner alone: they decide who works in the restaurant, how many branches it has and what it pays.

### Staff accounts

1. Open **Staff** → **Add staff member**: name, username, email, password and role (Manager, Waiter / Cashier, Kitchen). Give each person their own login; never share one account.
2. **From another branch**: someone who already works at one of your branches can use the same login here; pick them and their role.
3. **Edit** a member to change the role, or set them **Disabled** when they leave; they can no longer log in to this restaurant.
4. Your plan sets how many staff accounts you can have (Free 3, Standard 15, Pro 50).

![Staff: the team list and the Add staff member form](staff)

### Branches (Pro)

1. First complete your **Business profile** and wait until it shows **Verified**.
2. **Branches** → **Request a new branch**. The platform team checks that it is the same business under your brand name; until then it shows **Under review** and is hidden.
3. Each approved branch has its own menu, tables, QR codes and staff; you stay the owner with the same login. Switch between branches from your avatar → **Switch restaurant**.
4. The Branches page compares revenue per branch by day, week or month. Your plan includes a number of branch slots; more are a monthly add-on, and an unused extra branch can be removed.

### Billing

**Billing** shows your plan (Free, Standard or Pro), usage, and invoices. Pay by bank transfer using the **invoice number as the payment reference**; the subscription is extended once the payment is confirmed. If an invoice stays unpaid after the grace period, customers can't order until it is paid.

### Ordering API (Pro)

For a website, mobile ordering app or delivery partner that sends orders to your branches.

1. **Create an API key** (needs a verified business profile). Copy it at once: it is shown only once. Test keys create test orders that never reach the kitchen or revenue.
2. **Add a webhook**: your URL receives a POST whenever an order is created or changes status; check the X-QRMenu-Signature header with the signing secret. **Send test** checks it.
3. The **Request log** shows the last 100 requests (kept 90 days). **API documentation** describes every call. **Revoke** a key that leaked.

## Troubleshooting

Most problems come from sound being off, a dropped connection, or a table that needs to be scanned again.

| Problem | What to do |
| --- | --- |
| No sound for new orders | Press **Enable sound** (Kitchen queue) or **Turn on the sound** (alert bar) once on that device. Check the device volume and that the page is open, not closed or asleep. |
| New orders don't appear | Look at the top bar: **Offline** means no connection. Check Wi-Fi; the page reconnects and catches up by itself. Reload the page if it stays offline. |
| The alarm keeps beeping | An order is still New. Press **Accept** in the red bar or **ACCEPT ORDER** on the Kitchen queue, or cancel it if it was a mistake. |
| Customer can't order (“please scan the QR code”) | The bill was paid, the table was freed, or 6 hours passed since the scan. Ask the customer to scan the table's QR code again. |
| QR code doesn't open the menu | The code may be old (a **New code** was made) or printed too small. Print the current code from Tables & QR at least 4 cm wide. |
| “This menu isn't available right now” | The restaurant is not live yet (waiting for approval or email confirmation), or **Accepting orders** is off in Restaurant settings. |
| A dish can't be ordered | It is marked **Sold Out** or its category is hidden. Switch it back on the Menu page. |
| Receipt doesn't print | Check the receipt setting (ask / always / never) and the printer in Restaurant settings; use **Print test slip**. Bills can be reprinted as COPY. |
| Cash drawer is short or over | Check **Cash in / out** entries for missing ones (e.g. purchases from the till), then add a note on the report. |
| A page shows a PRO badge or upgrade page | That feature is not in your plan. The owner can upgrade on **Billing**. |
| A staff member can't see a page | Their role doesn't include it (see Roles at a glance). The owner can change the role on **Staff**. |
| Forgot password | **Forgot your password?** on the login page; the reset link goes to the email on the account. Without an email on the account, contact QR Menu support. |

Still stuck? Message QR Menu support on the WhatsApp number or email shown at the bottom of the home page.
