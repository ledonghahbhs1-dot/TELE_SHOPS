"""Task 066-A: bot <-> web contract. Runs the bot's REAL functions (wallet_topup._parse_usdt / _usdt_error,
shop_bot._wallet_web_mark_paid / _wallet_web_cancel / claim_stock_detailed) against tests/fixtures/web_contract.json. Offline.

The same contract is asserted on the web side by scripts/test-wallet-bot-web-contract.ts (repo v0-key-verification-system).
Whoever changes a web route or the bot's request/parse code MUST update this fixture AND that script, or the contract drifts.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import shop_bot
import wallet_pay as wp
import wallet_topup as wt

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "web_contract.json").read_text(encoding="utf-8"))
ORDER_ID = FIXTURE["usdt"]["order_id"]
AMOUNT = FIXTURE["usdt"]["amount_vnd"]


class UsdtReplies(unittest.TestCase):
    def test_valid_replies_parse(self):
        for method in ("plisio", "bep20", "binance"):
            body = dict(FIXTURE["usdt"]["ok"][method])
            expect = body.pop("expect")
            found = wt._parse_usdt(method, ORDER_ID, AMOUNT, body)
            for name, value in expect.items():
                self.assertEqual(found[name], value, (method, name))

    def test_bad_replies_are_rejected(self):
        for method, cases in FIXTURE["usdt"]["bad_replies"].items():
            for case in cases:
                with self.assertRaises(ValueError, msg=(method, case["why"])):
                    wt._parse_usdt(method, ORDER_ID, AMOUNT, case["body"])

    def test_error_classification(self):
        for case in FIXTURE["usdt"]["errors"]:
            self.assertEqual(wt._usdt_error(case["status"], case["body"]).code, case["expect"], case)

    def test_usdt_routes_and_request_body(self):
        """The bot's real USDT_METHODS routes (what create_usdt_order posts to) and the request shape it builds."""
        self.assertEqual({m: r for m, (r, _p) in wt.USDT_METHODS.items()},
                         {"plisio": "/api/tg/orders/plisio", "bep20": "/api/tg/orders/bep20", "binance": "/api/tg/orders/binance-pay"})
        request = FIXTURE["usdt"]["request"]
        self.assertEqual(set(request), {"order_code", "tg_user_id", "tg_username", "kind", "amount_vnd", "currency"})
        self.assertEqual((request["kind"], request["currency"]), (wt.TOPUP_KIND, "USDT"))


class WalletPaid(unittest.TestCase):
    def test_requests_and_verdicts(self):
        spec = FIXTURE["wallet_paid"]
        for case in spec["cases"]:
            calls = []

            def post(path, payload, timeout=20, _c=case):
                calls.append((path, dict(payload)))
                status, body = _c["sync"] if path.endswith("/sync") else _c["paid"]
                return status, body

            with patch.object(shop_bot, "_post_web_json", post):
                self.assertEqual(shop_bot._wallet_web_mark_paid(dict(spec["order"])), case["expect"], case["name"])
            self.assertEqual(calls[0], ("/api/tg/orders/sync", spec["sync_request"]), case["name"])
            if case["paid"] is not None:
                self.assertEqual(calls[1], ("/api/tg/orders/wallet-paid", spec["paid_request"]), case["name"])
            else:
                self.assertEqual(len(calls), 1, case["name"])

    def test_network_error_is_uncertain(self):
        with patch.object(shop_bot, "_post_web_json", side_effect=OSError("down")):
            self.assertEqual(shop_bot._wallet_web_mark_paid(dict(FIXTURE["wallet_paid"]["order"])), wp.WEB_UNCERTAIN)


class WalletCancel(unittest.TestCase):
    def test_request_and_verdicts(self):
        spec = FIXTURE["wallet_cancel"]
        for case in spec["cases"]:
            calls = []

            def post(path, payload, timeout=20, _c=case):
                calls.append((path, dict(payload)))
                return tuple(_c["reply"])

            with patch.object(shop_bot, "_post_web_json", post):
                self.assertEqual(shop_bot._wallet_web_cancel(dict(spec["order"])), case["expect"], case["name"])
            self.assertEqual(calls, [("/api/tg/orders/wallet-cancel", spec["request"])], case["name"])


class Claim(unittest.TestCase):
    def test_request_and_verdicts(self):
        spec = FIXTURE["claim"]
        for case in spec["cases"]:
            calls = []

            def post(path, payload, timeout=20, _c=case):
                calls.append((path, dict(payload), timeout))
                return tuple(_c["reply"])

            with patch.object(shop_bot, "_post_web_json", post):
                self.assertEqual(shop_bot.claim_stock_detailed(spec["sku"], spec["order_id"]), tuple(case["expect"]), case["name"])
            self.assertEqual(calls, [("/api/tg/stock/claim", spec["request"], 15)], case["name"])


if __name__ == "__main__":
    unittest.main()
