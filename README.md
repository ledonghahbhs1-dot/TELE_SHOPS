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

USDT in the original AGY bot uses a configured BEP20 wallet; it does not create
Plisio invoices. The original local database still had a placeholder wallet.
Set `USDT_WALLET` to a real BEP20 address, or use the owner's `/setusdt` command.
Checkout rejects an unconfigured wallet before creating an order.

## Local use

Copy `.env.example` to `.env`, fill the token, and set `SHOP_DATA_DIR=./data`.
Install `requirements.txt`, then run `python shop_bot.py`.
The original database in `D:\gpl` has not been copied or changed. A fresh deployment
starts with new local order history/settings; existing AGY orders need a deliberate
SQLite migration into the mounted Volume if they must be retained.

## Changes to the original AGY source

The interface and menus are retained. Deployment adaptations add a configurable
persistent data directory, the missing `timedelta` import, import-safe UTF-8 stream
configuration and rejection of the placeholder USDT wallet. The banner is copied
unchanged from the original AGY assets.

Run `python -m unittest discover -s tests -v` for offline checks; these do not send
Telegram messages or create real payments. Old aiogram tests cover the inactive
previous implementation; `test_agy_shop.py` covers the active AGY application.
