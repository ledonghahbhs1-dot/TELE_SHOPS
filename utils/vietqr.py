import random
from urllib.parse import quote

import config


def generate_memo() -> str:
    """Generates a unique-enough transfer content the buyer must include (e.g. GEM482913)."""
    return "GEM" + str(random.randint(100000, 999999))


def build_qr_url(amount: int, memo: str) -> str:
    """
    Builds a VietQR compact QR code image URL (img.vietqr.io - free, public, no API key)
    that a Vietnamese banking app can scan to auto-fill the transfer.
    """
    return (
        f"https://img.vietqr.io/image/{config.BANK_BIN}-{config.ACCOUNT_NO}-compact.png"
        f"?amount={amount}&addInfo={quote(memo)}&accountName={quote(config.ACCOUNT_NAME)}"
    )
