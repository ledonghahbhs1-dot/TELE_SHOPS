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

- `/stock` - shows available vs. sold count.
- `/orders` - shows the 20 most recent orders and their status.
- `/addlinks` - reply to a `.txt` file attachment, or `/addlinks <link1>\n<link2>...`, to top up inventory without restarting the bot.

## Notes

- Never commit a real inventory `.txt` file to this repo (`seed_links.txt` is gitignored) - the links themselves are the product.
- The SePay transaction API field names in `utils/sepay_api.py` are best-effort and have not been verified against a live account from this environment - test with one real small transfer after deploying, and check the logs if a payment isn't auto-detected.
