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
  Late payments are marked `needs_review`. The customer presses the bot's check
  button to trigger the original AGY delivery flow.
- The web claims the entire paid quantity atomically. The bot persists the bundle
  locally, and only the order owner can request it again after interruption.
- Never assert that web and bot changes preserve all orders without exercising
  pending, paid, expired and interrupted-delivery scenarios. The previous signed
  bot implementation used a separate durable web delivery queue; orders from that
  implementation are not automatically migrated into AGY's local SQLite database.
  Audit any outstanding old orders before assuming migration is complete.
- Both projects must remain API-compatible during deploys. Verify the active
  Railway deployment and Vercel production commit, not just GitHub push status.

## Checks and outstanding security task

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
