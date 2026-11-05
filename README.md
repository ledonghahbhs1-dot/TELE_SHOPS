# TELE_SHOP

Telegram bot that sells single-use links (e.g. Google One / Gemini offer links) for a fixed price, paid via VietQR bank transfer and auto-confirmed through the SePay transaction API.

## How it works

1. User sends `/start` (or `/buy`) in a private chat with the bot.
2. Bot creates an order with a unique transfer memo (`GEM123456`) and shows a VietQR payment QR code.
3. A background task polls the SePay API every `POLL_INTERVAL_SEC` seconds, matching incoming transfers (by memo + amount) against pending orders.
4. On a match, the order is marked paid, one available link is atomically claimed from inventory, and it's DM'd to the buyer automatically. The buyer can also tap "I've Paid - Check Now" for an immediate check.

## Setup

1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in `BOT_TOKEN`, `OWNER_IDS`, and `SEPAY_API_TOKEN`.
3. Seed the initial inventory from a `.txt` file (one link per line, invalid/blank lines are skipped automatically):
   ```
   python seed_links.py "C:\path\to\your\links.txt"
   ```
   Run this again any time you want to add more stock - duplicates are skipped.
4. `python main.py`

## Admin commands (owner only, set via `OWNER_IDS`)

Inventory commands are accepted only in private chat to avoid exposing stock links in groups.

- `/stock` - shows available vs. sold count.
- `/orders` - shows the 20 most recent orders and their status.
- `/addlinks` - reply to a `.txt` file attachment, or `/addlinks <link1>\n<link2>...`, to top up inventory without restarting the bot.

## Railway deployment

Deploy this repository as a Dockerfile service with **one replica** (Telegram long polling).
No domain or PORT is required. Attach a Railway Volume at `/app/data` and set
`DATABASE_PATH=/app/data/shop.db` so inventory and orders survive redeployments.

Set `BOT_TOKEN`, `OWNER_IDS`, `SEPAY_API_TOKEN` and `ALLOWED_GROUP_IDS` in Railway Variables.
Check `PRODUCT_NAME`, `PRICE_VND`, `BANK_BIN`, `BANK_NAME`, `ACCOUNT_NO`, `ACCOUNT_NAME`
against your product and receiving bank account. Optional settings and defaults are in `.env.example`.
After deployment, start the bot privately as an owner and add stock using `/addlinks`.

## Restricting groups

- Set `ALLOWED_GROUP_IDS` to your group ID (e.g. `-1001234567890`), or a comma-separated list.
  Empty means the bot leaves all groups. Private customer conversations remain open.
- Start the bot privately from your owner account first. If you don't know the group ID,
  add the bot to the group once: it will leave and send the ID privately to every configured owner.
  Set that ID on Railway, redeploy, then add the bot again.
- In an approved group, an owner can use `/chatid`. `/start` and `/buy` show a button
  opening the bot privately so payment details and purchased links stay private.
- Promote the bot to administrator manually in Telegram. It cannot promote itself.
  Selling links needs no delete/ban/promote permissions. Group moderation is not implemented.
- After adding it to your group, use BotFather `/setjoingroups` -> select this bot -> Disable
  to stop new group additions at Telegram's level. Re-enable temporarily if you need to add it again.
  The allowlist also makes the bot leave other groups when membership/message updates arrive.
- If Telegram upgrades your group to a supergroup and changes its ID, update the allowlist.

## Notes

- Never commit a real inventory `.txt` file to this repo (`seed_links.txt` is gitignored) - the links themselves are the product.
- The SePay transaction API field names in `utils/sepay_api.py` are best-effort and have not been verified against a live account from this environment - test with one real small transfer after deploying, and check the logs if a payment isn't auto-detected.
