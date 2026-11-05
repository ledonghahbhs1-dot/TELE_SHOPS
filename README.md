# TELE_SHOPS — original AGY shop bot

The active application is `shop_bot.py`, copied from `D:\gpl\shop_bot.py`.
It includes AGY's original custom emoji IDs, VIP menus, product banner, quantity
selection, VietQR and USDT screens, order history, activation guide and admin tools.
`main.py` is a compatibility entry point for existing Railway start overrides.
The previous aiogram modules remain in Git history and are not used by this app.

## Railway

- Set `BOT_TOKEN` (or `SHOP_BOT_TOKEN`) and `ADMIN_ID` in the bot service Variables.
- Attach a Railway Volume mounted at `/data`; set `SHOP_DATA_DIR=/data`.
  SQLite orders and settings must survive redeploys. Run one replica.
- Use the included Dockerfile and start command `python shop_bot.py`.
- This original AGY implementation does not require `WOLFMOD_API_SECRET`.
- Keep Vercel's `TG_SALES_BOT_TOKEN` equal to the active bot token for the original
  `/api/tg/orders/*` and `/api/tg/stock/*` integration. These are different routes
  from the newer signed `/api/bot/*` integration; verify the legacy routes are
  enabled on the website before accepting orders.
- Stop any other running instance using the same token before Railway starts polling.

Manage stock from the Wolfmod dashboard. No local inventory files or databases
are included in this repository or imported automatically.

USDT checkout now creates a Plisio invoice through Wolfmod's authenticated
`POST /api/tg/orders/plisio` endpoint. The Plisio API key remains on Vercel as
`PLISIO_API_KEY` (or `PLISIO_SECRET_KEY`); no wallet or provider key is needed on Railway.
`TG_PLISIO_CURRENCY` on Vercel defaults to `USDT_TRX`. The bot displays the provider's
invoice link and the server-priced total. Use the network and QR on that invoice.
The existing signed `/api/webhook/plisio?json=true` webhook verifies the invoice,
currency and received amount before marking the shared order paid. The customer
can then press the bot's payment-check button to receive the complete link bundle.
Late payments are retained for administrator review. No customer button can confirm payment.
The old `/setusdt` wallet setting is retained for compatibility with AGY's admin UI;
it does not control Plisio checkout.

## Local use

Copy `.env.example` to `.env`, fill the token, and set `SHOP_DATA_DIR=./data`.
Install `requirements.txt`, then run `python shop_bot.py`.
The original database in `D:\gpl` has not been copied or changed. A fresh deployment
starts with new local order history/settings; existing AGY orders need a deliberate
SQLite migration into the mounted Volume if they must be retained.

## Changes to the original AGY source

The interface and menus are retained. Deployment adaptations add a configurable
persistent data directory, the missing `timedelta` import, import-safe UTF-8 stream
configuration. USDT checkout uses the web's Plisio integration, stock reads use
the authenticated website API, and delivery claims the complete paid bundle once.
The banner is copied unchanged from the original AGY assets.

Run `python -m unittest discover -s tests -v` for offline checks; these do not send
Telegram messages or create real payments. Old aiogram tests cover the inactive
previous implementation; `test_agy_shop.py` covers the active AGY application.
