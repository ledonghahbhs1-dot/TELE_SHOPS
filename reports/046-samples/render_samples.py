"""Renders SIMULATED bot messages (vi + en) to samples.json / samples.html. No network, no Telegram, no production DB.

Run from the repo root:  python reports/046-samples/render_samples.py
"""
import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent

import shop_bot  # noqa: E402
import i18n  # noqa: E402

PLUS, PRO, GPT = "gemini_plus", "gemini_pro_account", "chatgpt_go_3m"


def item(sku, name, price, in_stock=True, max_qty=1):
    return {"sku": sku, "name": name, "description": "", "type": "item", "price_vnd": price, "price_usdt": None,
            "in_stock": in_stock, "max_qty": max_qty}


def buttons(markup):
    return [[{"text": b.text, "callback_data": b.callback_data, "url": b.url} for b in row] for row in markup.inline_keyboard]


def main():
    tmp = tempfile.TemporaryDirectory()
    shop_bot.DATA_DIR = tmp.name
    shop_bot.DB_PATH = str(Path(tmp.name) / "shop_database.db")
    shop_bot.CLAIMED_LINKS_FILE = str(Path(tmp.name) / "claimed_links.txt")
    with patch.dict(os.environ, {"USDT_WALLET": ""}):
        shop_bot.init_database()
    conn = shop_bot.get_db_connection()
    conn.execute("UPDATE products SET price = 14999 WHERE id = 'gemini_plus'")
    conn.commit()
    conn.close()
    user = type("U", (), {"id": 777, "first_name": "Minh"})()
    samples: dict = {"vi": [], "en": []}

    def catalogue(gpt_in_stock):
        payload = {"success": True, "products": [item(PLUS, "Google AI Plus", 14999, True, 50),
                                                 item(PRO, "ACCOUNT GEMINI PRO FULL BENEFITS", 125000, True, 1),
                                                 item(GPT, "CHATGPT GO 3 THÁNG", 100000, gpt_in_stock, 1)]}
        return patch.object(shop_bot, "_fetch_catalogue_payload", return_value=payload)

    def stock(gpt):
        counts = {PLUS: 293, PRO: 1, GPT: gpt}
        return patch.object(shop_bot, "get_stock_count", side_effect=lambda sku="gemini_plus": counts.get(sku, 0))

    with patch("urllib.request.urlopen", side_effect=OSError("network disabled")), \
         patch.object(shop_bot, "sync_web_product_price", return_value=True):
        for lang in ("vi", "en"):
            shop_bot.set_user_lang(user.id, lang)
            add = lambda title, text, markup=None: samples[lang].append(  # noqa: E731
                {"title": title, "text": text, "buttons": buttons(markup) if markup else []})
            with catalogue(False), stock(0):
                shop_bot.refresh_catalogue(force=True)
                add("Main menu (/start)", shop_bot.welcome_message(user, lang), shop_bot.main_menu_keyboard(user.id, lang))
                add("Language picker (/language)", i18n.t(lang, "lang_title"), shop_bot.language_keyboard(lang))
                add("Shop: ChatGPT Go OUT OF STOCK", shop_bot.shop_message(lang), shop_bot.shop_keyboard(user.id, lang))
                add("ChatGPT Go page: OUT OF STOCK", shop_bot.render_product_intro(GPT, 0, lang))
            with catalogue(True), stock(5):
                shop_bot.refresh_catalogue(force=True)
                add("Shop: ChatGPT Go in stock", shop_bot.shop_message(lang), shop_bot.shop_keyboard(user.id, lang))
                add("ChatGPT Go page: in stock", shop_bot.render_product_intro(GPT, 5, lang))
                add("Google AI Plus page", shop_bot.render_product_intro(PLUS, 293, lang))
                add("Gemini Pro Account page", shop_bot.render_product_intro(PRO, 1, lang))
                for sku in (GPT, PRO, PLUS):
                    add(f"Stock-in notification: {shop_bot.sku_title(sku, lang)}",
                        shop_bot.format_stock_added_notification(3, 8, sku=sku, lang=lang),
                        shop_bot.stock_notification_keyboard(sku, lang))
                body, btns = shop_bot.format_delivered_links_message("TGS1A2B3C4D", ["https://one.google.com/offer/EXAMPLE"], PLUS, lang)
                add("Delivery message (Google AI Plus)", body, shop_bot.InlineKeyboardMarkup(btns))
                body, btns = shop_bot.format_delivered_links_message("TGS1A2B3C4D", ["https://chatgpt.com/offer/EXAMPLE"], GPT, lang)
                add("Delivery message (ChatGPT Go offer link)", body, shop_bot.InlineKeyboardMarkup(btns))
                add("ChatGPT Go guide", shop_bot.render_guide(GPT, lang))
                bill = shop_bot.build_vietqr_bill(lang, "TGS1A2B3C4D", shop_bot.sku_title(GPT, lang), 1, 100000, "ACB", "00000000", "EXAMPLE", "14:30:00")
                add("VietQR invoice (ChatGPT Go)", bill, shop_bot.InlineKeyboardMarkup(shop_bot.build_vietqr_buttons(lang, "TGS1A2B3C4D", "4")))
    (OUT / "samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")

    def clean(text):
        text = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)
        return text.replace("\n", "<br>")

    for lang in ("vi", "en"):
        cards = []
        for entry in samples[lang]:
            rows = "".join('<div class="row">' + "".join(f'<span class="btn">{html.escape(b["text"])}</span>' for b in row) + "</div>"
                           for row in entry["buttons"])
            cards.append(f'<section><h3>{html.escape(entry["title"])}</h3><div class="bubble">{clean(entry["text"])}</div>{rows}</section>')
        page = f"""<!doctype html><meta charset="utf-8"><title>046 samples ({lang})</title>
<style>body{{font:14px/1.45 system-ui,sans-serif;background:#0e1621;color:#e6edf3;margin:0;padding:16px;display:grid;
grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px}}section{{max-width:380px}}h3{{font-size:12px;color:#8aa;margin:0 0 6px}}
.bubble{{background:#182533;border-radius:12px;padding:10px 12px;word-wrap:break-word}}code{{font-family:ui-monospace,monospace}}
.row{{display:flex;gap:4px;margin-top:4px}}.btn{{flex:1;text-align:center;background:#2b5278;border-radius:8px;padding:7px 6px;font-size:13px}}</style>
{''.join(cards)}"""
        (OUT / f"samples-{lang}.html").write_text(page, encoding="utf-8")
    print("ok", {k: len(v) for k, v in samples.items()})
    tmp.cleanup()


if __name__ == "__main__":
    main()
