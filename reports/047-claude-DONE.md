# Task 047 — Chính sách bảo hành ChatGPT Go 3 tháng: "1 đổi 1, liên hệ CSKH nếu lỗi"

Branch: `fix/chatgpt-go-warranty` (từ `origin/main` 0eccc1f). Không push main, không merge, không deploy, không gọi dịch vụ ngoài, không đọc secret.

## Việc 1 — Sửa câu bảo hành
- vi: "Bảo hành 1 đổi 1, liên hệ CSKH nếu lỗi."  /  en: "1-for-1 replacement warranty, contact support if faulty."
- `product_content.json` (chatgpt_go_3m): `warranty` từ `null` thành 2 câu trên (trang sản phẩm tự hiển thị dòng "Bảo hành"); note cũ "Liên hệ CSKH nếu link lỗi" đổi thành "kèm mã đơn hàng" (giữ note vì test yêu cầu notes không rỗng); xoá `owner_todo` đã hết hiệu lực.
- `product_guides.py`: dòng cuối hướng dẫn chatgpt_go_3m (vi/en) dùng câu mới.
- `i18n.py`: `offer_single_use` (tin nhắn giao offer link) dùng câu mới. Khoá này chỉ dùng ở `shop_bot.py:1044` cho offer link.
- Test: `tests/test_i18n.py` (assert câu cũ -> assert 2 câu mới), `tests/test_offer_delivery.py` (+2 assert trên tin nhắn giao hàng vi/en).
- Không đụng bảo hành Google AI Plus ("24h 1 đổi 1").

## Web (v0-key-verification-system)
`git grep -i -E "bảo hành|warranty|link lỗi|CSKH" origin/main -- api` -> không có kết quả (exit 1). `api/_lib/tg-stock-notice.ts` tồn tại trên origin/main nhưng KHÔNG nhắc bảo hành => không cần sửa, không mở PR web. (Bản local đang ở nhánh cũ, behind 22 và có thay đổi chưa commit, nên chỉ kiểm tra trên origin/main, không chạm.)

## Kiểm thử
`python -m unittest discover -s tests` -> `Ran 145 tests ... OK`, `$LASTEXITCODE` = **0** (trước khi sửa cũng 145 test).
(Môi trường không có pytest; dùng unittest như báo cáo 046.)

## Việc 2 — Chỉ đọc: `origin/docs/bot-binance-pay-ux` vs `origin/main`
Lưu ý: `main` local đang cũ (0f8d34f); so với `origin/main` = 0eccc1f sau `git fetch`. Merge-base của hai nhánh = 206cac6 (PR #3), tức nhánh docs tách ra TRƯỚC PR #4.

`git log origin/main..origin/docs/bot-binance-pay-ux` (3 commit main CHƯA có):
- 11e22cf docs(bot): Binance Pay USDT conversation UX design (vi/en), design only
- 81fa094 feat(shop): USDT Binance Pay - customer pays the exact price and pastes the order ID (off by default)
- a64ebcc feat(shop): USDT BEP20 payment via QR of the wallet address (off by default)

`git diff --stat 206cac6 origin/docs/bot-binance-pay-ux` (6 file): `.env.example` +4, `AI_HANDOFF.md` +15, `docs/bot-binance-pay-ux.md` +201, **`shop_bot.py` +426/-5**, `tests/test_bep20.py` +307, `tests/test_binance_pay.py` +382.
`git grep -i -E "BEP20_ENABLED|BINANCE_PAY|binance_pay|bep20_qr" origin/main -- *.py *.example` -> không có kết quả; trên nhánh docs có `bep20_enabled()`, `binance_pay_enabled()`, `create_web_bep20_order`, `show_bep20_checkout`, `create_web_binance_order`, `confirm_web_binance`... (cờ `BEP20_ENABLED`, `BINANCE_PAY_ENABLED`, mặc định TẮT).

Ngược lại main có mà nhánh docs không có: PR #4 (i18n.py, product_content.*, stock_notify.py, test_i18n, test_offer_delivery, ChatGPT Go, reports/046).

## Kết luận / rủi ro khi đổi Railway sang main
- CÓ: nhánh docs chứa code chạy bot (`shop_bot.py`) mà main chưa có: thanh toán USDT BEP20 QR và Binance Pay (off by default).
- Đổi Railway sang main ngay = MẤT 2 tính năng thanh toán đó (nếu env đang bật `BEP20_ENABLED`/`BINANCE_PAY_ENABLED` thì chúng ngừng hoạt động) và được thêm i18n + ChatGPT Go.
- Hai nhánh cùng sửa `shop_bot.py` và `AI_HANDOFF.md` => merge sẽ có xung đột; cần PR riêng gộp 3 commit (a64ebcc, 81fa094, 11e22cf) vào main (rebase/merge, tự xử lý xung đột, chạy lại test) TRƯỚC khi đổi nhánh deploy. Chưa làm vì task yêu cầu chỉ đọc.
- Không có nhánh "bridge" và thư mục `tasks/` trên repo nên báo cáo này đặt ở `reports/047-claude-DONE.md` trên nhánh sửa, theo mẫu 046.
- Ngoài phạm vi: `reports/046-samples/*` là ảnh/JSON render cũ, vẫn chứa câu cũ; chưa render lại.
