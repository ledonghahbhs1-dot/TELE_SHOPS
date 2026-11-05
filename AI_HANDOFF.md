# AI handoff — TELE_SHOPS / Wolfmod

Updated: 2026-11-05. The user's preferred interface is the complete AGY VIP shop,
including its original custom emoji IDs, product banner and multi-quantity screens.
The AGY source was recovered from `D:\gpl\shop_bot.py`; the active copy is
`shop_bot.py`. Do not replace it with a simplified interface.

## Deployment and data

- GitHub: `ledonghahbhs1-dot/TELE_SHOPS`, branch `main`.
- Railway project `satisfied-youth`, production service `TELE_SHOPS`.
- Railway Volume `tele_shops-volume` is mounted at `/data`. SQLite orders,
  settings and delivery records live there; retain the Volume during redeploys.
- Railway `BOT_TOKEN` authenticates the Telegram bot. Vercel production has
  `TG_SALES_BOT_TOKEN` set to the same value. Never print either value in logs.
- Wolfmod web source: `ledonghahbhs1-dot/v0-key-verification-system`, branch
  `main`; Vercel project `v0-key-verification-system-api-server`, domain
  `https://www.wolfmod.xyz`. The Plisio integration starts at web commit `4600a34c`.
- Stock remains on Wolfmod's admin dashboard. Do not import local text files,
  seed files or private links into stock automatically. Do not commit secrets,
  SQLite databases or purchased links.

## Payments and delivery

- VietQR uses the AGY flow and shared Wolfmod orders. USDT uses the authenticated
  web route `/api/tg/orders/plisio`; the website creates the Plisio invoice with
  its own `PLISIO_API_KEY` or `PLISIO_SECRET_KEY`. Default network `USDT_TRX` can
  be configured using Vercel `TG_PLISIO_CURRENCY`.
- Plisio callbacks reach `/api/webhook/plisio?json=true`; the web checks the HMAC,
  invoice transaction ID, currency and amount before marking the order paid.
  Late payments are marked `needs_review`. The bot automatically checks local
  orders against web payment status and sends the claimed links and instructions.
- An unconfirmed payment check informs the buyer to contact @wolfmodyt if already
  paid, using AGY custom emoji IDs. It must not send a reconciliation alert to the
  admin; provider errors and confirmed payments awaiting stock have distinct text.
- The web claims the entire paid quantity atomically. The bot persists the bundle
  locally, and only the order owner can request it again after interruption.
- Never assert that web and bot changes preserve all orders without exercising
  pending, paid, expired and interrupted-delivery scenarios. The previous signed
  bot implementation used a separate durable web delivery queue; orders from that
  implementation are not automatically migrated into AGY's local SQLite database.
  Audit any outstanding old orders before assuming migration is complete.
- Both projects must remain API-compatible during deploys. Verify the active
  Railway deployment and Vercel production commit, not just GitHub push status.
- Dashboard `tg_products` prices are shared with AGY through authenticated
  `GET /api/tg/products/price`. The bot caches for 10 seconds for display and
  fetches fresh prices before creating VietQR; an unavailable price blocks new
  VietQR creation rather than silently using stale prices. Existing order amounts
  remain snapshots; Plisio invoices use the web price.
- Admin price changes via `PUT /api/admin/tg/products/:id` send a VIP-emoji
  Telegram notice to `ADMIN_ID` (same fallback as stock alerts), only when VND
  or USDT actually changes. The response field `price_notification` reports
  sent/failed/unchanged; a Telegram failure does not roll back the price change
  and the notice currently has no durable retry queue.

## Checks and outstanding security task

- Investigation 2026-11-05: admin reported a VietQR payment for `TGSB74D2E96`.
  Wolfmod initially recorded the 20,000 VND order as `pending`, with no payment
  reference and no matching row in `webhook_logs`. A read-only query found no
  webhook log rows at all in the preceding two hours; this does not prove whether
  SePay failed to send the webhook or Wolfmod failed to record it. During the
  follow-up live order, `TGS37939776` became `paid` at 2026-11-05 08:38:46 UTC
  with reference `96428678`; the corresponding `webhook_logs` query was still
  empty. A separate test order `TGSD146733F` remained `pending` at 08:39:23 UTC.
  The admin later supplied SePay transaction `96426765` and the order was
  reconciled as described below. Do not fabricate SePay references.
- Bot purchase history lists completed deliveries only, with links, and excludes
  pending/cancelled payment transactions. VietQR account details remain stored
  with orders. Keep those fields and the persistent `/data` volume.
- Delivery investigation 2026-11-05: the bot checked local 10-minute expiry and
  cancellation before asking Wolfmod whether an order was paid. The test order
  `TGS37939776` was paid in Wolfmod (`payment_ref=96428678`) but initially had no
  `delivered_at`; the old local order was auto-cancelled at its 10-minute mark.
  The bot callback and expiry task were changed to check the shared web order
  before local cancellation, so a paid order can still deliver on the customer's
  next check. Automatic delivery was subsequently deployed and verified.
- Admin manually confirmed receipt of the 20,000 VND transfer for `TGSE6F70932`
  (its transfer memo) on 2026-11-05. Wolfmod had no matching SePay webhook, so the
  order was set to `paid` with the explicit reference
  `ADMIN_CONFIRMED:TGSE6F70932`; this is an admin attestation, not a bank
  transaction ID. The order was subsequently returned to stock as an admin test.
  Do not replace this reference with a fabricated
  SePay transaction ID.
- Admin supplied SePay transaction list: `96509305` / `TGSE6F70932` and
  `96426765` / `TGSB74D2E96` were incoming 20,000 VND, but SePay showed no
  recognized payment code or webhook for either. The recognized examples
  `TGS37939776` and `TGS20000841` begin with digits after TGS. Investigate
  SePay's payment-code pattern and webhook filters: TGS suffixes can include
  A-F as well as digits. Based on the admin's confirmation and supplied SePay
  transaction, `TGSB74D2E96` was manually marked paid with reference
  `96426765`; paid_at is the manual reconciliation time. It was subsequently
  returned to stock as an admin test.
- Admin requested that four test deliveries be returned: `TGSC470FB61`,
  `TGSE6F70932`, `TGS37939776`, `TGSB74D2E96`. The four corresponding stock
  items (IDs 2, 3, 4, 5) were restored to `available`, web orders marked
  `cancelled` with `review_reason=admin_test_stock_return`, and local SQLite
  orders marked `test_returned` with `item_delivered` cleared. The stock count
  was 294 after the operation. Historical Telegram messages cannot be recalled;
  their links must not be used after return. Do not reactivate or redeliver the
  test orders, and do not treat `cancelled` as a monetary refund.
- Plisio diagnosis 2026-11-05: production has `PLISIO_SECRET_KEY` configured,
  `gemini_plus` is active at 1.00 USDT, and 294 links are available. No Telegram
  Plisio invoice rows exist. Railway previously logged only `HTTPError` when
  invoice creation failed. The bot now records only the HTTP status and a
  validated API error code. The Plisio handler also records/returns the
  provider's validated error code and HTTP status (never provider URLs, messages
  or secrets); inspect Railway/Vercel logs after the next retry to identify
  whether Plisio rejects the key/IP or invoice parameters. No real invoice was
  created during diagnosis.
- Root cause found later: Telegram transaction adapters in `api/index.ts` read
  `.rows` from `client.query`, although `withTransaction` already returns the
  rows array. This broke both stock claim and the Telegram Plisio flow; both
  adapters were corrected. Confirm a real Plisio invoice/callback separately.
  Bot polling is spaced to respect the 10/min status endpoint limit; automatic
  recovery scans the newest 100 local orders within seven days.

Run `python -m unittest discover -s tests -v` in this repository. Web checks include
`pnpm exec tsx scripts/test-tg-plisio.ts`, `pnpm run test:tg-runtime`, and
`pnpm run build`. The tests mock payment providers; they are not proof of a real
Plisio payment. Do not create real invoices or send customer messages as a test
without the user's authorization.

On 2026-11-05 the prior AGY deployment printed the Telegram bot token in Railway
HTTP logs. HTTPX request logging was disabled in commit `fc7070e`, but the existing
token should be revoked/rotated with BotFather. Update Railway `BOT_TOKEN` and
Vercel `TG_SALES_BOT_TOKEN` together, redeploy both, and confirm authenticated stock
access. Never paste the token into chat or commit it.
