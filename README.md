# TELE_SHOPS — shared Wolfmod store

Telegram storefront for the existing Wolfmod database and admin dashboard.
Prices, products, inventory, payments, orders and delivery receipts are stored by
Wolfmod. The bot uses signed HTTPS requests; it never connects directly to Supabase.

## Railway variables

Copy `.env.example` into Railway Variables and fill in:

| Variable | Value |
| --- | --- |
| `BOT_TOKEN` | New token from BotFather; revoke the old exposed token first. |
| `OWNER_IDS` | Your Telegram user ID; comma-separated for several owners. |
| `WOLFMOD_API_SECRET` | Same secret as `TG_BOT_API_SECRET` on Wolfmod Vercel; at least 32 characters. |
| `WOLFMOD_API_URL` | `https://www.wolfmod.xyz` |
| `PRODUCT_SKU` | `gemini_plus` |
| `ALLOWED_GROUP_IDS` | Your negative group ID, for example `-1001234567890`; empty rejects all groups. |
| `POLL_INTERVAL_SEC` | `15` |

Deploy using the included Dockerfile with **one replica**. No port, domain, Volume,
SQLite, Supabase connection string, price, or SePay API token is needed on Railway.
Copy `.env.example` to `.env` for local use, install `requirements.txt`, and run `python main.py`.

## Wolfmod configuration

Set `TG_BOT_API_SECRET` on Vercel to match Railway's `WOLFMOD_API_SECRET`, then redeploy.
The website's existing authenticated SePay webhook confirms payments in `tg_orders`.
Keep the receiving bank account synchronized with SePay. Default receiving details
match the existing Wolfmod account; optional Vercel overrides are `TG_BANK_BIN`,
`TG_BANK_NAME`, `TG_ACCOUNT_NO`, `TG_ACCOUNT_NAME`.

`TG_SALES_BOT_TOKEN` on Vercel is optional for automated delivery by this bot.
Set it to the same new Telegram token if you also use dashboard manual delivery or
the website's Telegram payment notifications. Never use an admin session/password
as the bot's API credential.

Manage inventory, active products and prices at:
https://www.wolfmod.xyz/m7k9xq2r/dashboard#tg-sales

## Customer flow

1. `/start` reads the active product and current price from Wolfmod.
2. `/buy` creates a server-priced, idempotent order in the shared dashboard and shows
   its VietQR QR code with the server's `TGS…` bank-transfer memo.
3. SePay's existing webhook marks that order paid. Clicking “I've paid” only queries
   the server; it cannot confirm or change payment status.
4. The bot polls the database-backed delivery queue. A transaction reserves stock
   for that paid order; only after Telegram accepts the message does a signed receipt
   mark the dashboard order delivered.
5. `/orders` or `/myorders` shows only the sender's orders.

Orders and unfinished deliveries survive Railway restarts. A lost receipt is retried
without allocating another link. If the process crashes after Telegram accepts a
message but before the receipt is stored, the same purchased links may be resent after
the two-minute lease expires; inventory is not allocated twice.

## Owner and groups

- `/stock`: count the shared inventory, only in the owner's private chat.
- `/dashboard` and `/addlinks`: open the existing admin dashboard. Upload stock there;
  there is no second inventory or Telegram-based bulk export/import.
- Start the bot privately as an owner before adding it to a group. If an unknown
  group adds it, the bot leaves and privately sends its ID to the owner. Put that ID
  in `ALLOWED_GROUP_IDS`, redeploy Railway, then add the bot again.
- Promote the bot manually in your group. `/start` or `/buy` there opens a private
  customer conversation. The bot does not implement member moderation.
- After adding it to your group, BotFather `/setjoingroups` → Disable prevents new
  group additions. The allowlist also rejects unapproved groups.

## Migration and tests

The previous SQLite implementation is no longer used. Existing ignored local data
files are not uploaded or migrated automatically; the Wolfmod inventory is the source
of truth. Do not run the previous standalone SQLite deployment at the same time.

Run `python -m unittest discover -s tests -v`. Tests use local HTTP fixtures and mocked
Telegram delivery, without real payments, customer messages, or production stock.
