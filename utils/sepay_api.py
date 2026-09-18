"""
SePay (my.sepay.vn) transaction-lookup client.

NOTE: I could not test this against a live SePay account/token from here, so the
exact JSON field names below are my best-effort match to SePay's publicly
documented "Transactions List" API. If the real response differs, only
_extract_transactions()/_field() below need adjusting - everything else
(matching/polling logic in handlers/user_handlers.py) is written against the
normalized dict this module returns, not the raw SePay shape.

Verify once with a real $1 test transfer after deploying (log lines are printed
for every raw response the first few times so this is easy to diagnose).
"""

from typing import Any, Dict, List, Optional

import aiohttp

import config
from utils.logger import logger

_ALIASES = {
    "id": ["id", "transaction_id"],
    "amount_in": ["amount_in", "amountIn", "amount"],
    "content": ["transaction_content", "content", "description", "addInfo"],
    "account_number": ["account_number", "accountNumber"],
    "transaction_date": ["transaction_date", "transactionDate", "date"],
}


def _field(tx: Dict[str, Any], key: str) -> Any:
    for alias in _ALIASES.get(key, [key]):
        if alias in tx and tx[alias] not in (None, ""):
            return tx[alias]
    return None


def _normalize(tx: Dict[str, Any]) -> Dict[str, Any]:
    amount_in = _field(tx, "amount_in")
    try:
        amount_in = int(float(amount_in)) if amount_in is not None else 0
    except (TypeError, ValueError):
        amount_in = 0
    return {
        "id": str(_field(tx, "id") or ""),
        "amount_in": amount_in,
        "content": str(_field(tx, "content") or ""),
        "transaction_date": _field(tx, "transaction_date"),
        "raw": tx,
    }


async def fetch_recent_transactions(limit: int = 30) -> List[Dict[str, Any]]:
    """
    Returns the most recent incoming transactions for the configured bank account,
    normalized to {id, amount_in, content, transaction_date, raw}. Returns an empty
    list (never raises) on any failure - callers should treat that as "try again
    next poll", not "no transactions".
    """
    if not config.SEPAY_API_TOKEN:
        logger.error("SEPAY_API_TOKEN is not configured - cannot check payments")
        return []

    url = f"{config.SEPAY_API_BASE_URL}/transactions/list"
    headers = {"Authorization": f"Bearer {config.SEPAY_API_TOKEN}"}
    params = {"account_number": config.ACCOUNT_NO, "limit": limit}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    logger.error("SePay API error: status=%s body=%s", resp.status, data)
                    return []
                transactions = data.get("transactions") or data.get("data") or []
                if not isinstance(transactions, list):
                    logger.error("Unexpected SePay response shape: %s", data)
                    return []
                return [_normalize(tx) for tx in transactions]
    except Exception as e:
        logger.error("Exception fetching SePay transactions: %s", e)
        return []


def find_matching_transaction(transactions: List[Dict[str, Any]], memo: str, min_amount: int) -> Optional[Dict[str, Any]]:
    """Finds the first transaction whose content contains `memo` (case-insensitive) with amount_in >= min_amount."""
    memo_upper = memo.strip().upper()
    for tx in transactions:
        if memo_upper in tx["content"].upper() and tx["amount_in"] >= min_amount:
            return tx
    return None
