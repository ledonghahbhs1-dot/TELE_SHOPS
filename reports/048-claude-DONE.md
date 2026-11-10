# Task 048 — Gộp BEP20 + Binance Pay (nhánh docs/bot-binance-pay-ux) vào main

Branch: `feat/merge-bep20-binance-into-main` (từ `origin/main` 0eccc1f). Không push main, không merge, không deploy, không đụng Railway/DB, không gọi dịch vụ ngoài.
Lưu ý: PR #5 (bảo hành ChatGPT Go) chưa nằm trong base này; hai PR không đụng cùng file ngoài `i18n.py`/`reports/` nên không xung đột logic.

## Cách làm
`git cherry-pick a64ebcc 81fa094 11e22cf` -> 3 commit mới: 2ec3d42, c5afdad, 03b37ec (+ commit i18n key).

## Xung đột (duy nhất 1 chỗ, `shop_bot.py` ~dòng 2258, hàm kiểm tra thanh toán)
- HEAD (main): `notice = t(lang, "check_not_received")`
- a64ebcc: `notice = "<b>Hệ thống chưa nhận được thanh toán…</b>"` rồi nếu `bep20_stat.get("confirmations")` thì đổi sang thông báo "đang chờ xác nhận (x/y)".
- Giải quyết (giữ cả hai): `notice = t(lang, "check_not_received")`; nếu có `confirmations` thì `notice = t(lang, "bep20_confirming", n=..., need=...)`. Thêm khoá `bep20_confirming` vi/en vào `i18n.py`.
- `AI_HANDOFF.md`, `.env.example` auto-merge không xung đột. Không còn marker `<<<<<<<` (git grep exit 1).

## `git diff --stat origin/main HEAD`
.env.example +4, AI_HANDOFF.md +15, docs/bot-binance-pay-ux.md +201, i18n.py +4, shop_bot.py +426/-5, tests/test_bep20.py +307, tests/test_binance_pay.py +382 (7 file, +1334/-5). Không file nào của PR #4 bị xoá.

## Kiểm thử
- `python -m unittest discover -s tests` -> `Ran 183 tests ... OK`, `$LASTEXITCODE` = 0 (145 cũ + 38 mới).
- `unittest tests.test_bep20 tests.test_binance_pay tests.test_i18n tests.test_offer_delivery` -> 81 tests OK, exit 0.

## Cờ mặc định TẮT
- `bep20_enabled()` = `os.getenv("BEP20_ENABLED","0")=="1"`; `binance_pay_enabled()` tương tự với `BINANCE_PAY_ENABLED` (shop_bot.py). Nút chỉ được thêm trong `if binance_pay_enabled():` / `if bep20_enabled():` (shop_bot.py ~1164-1167).
- Test `test_disabled_by_default_no_button_no_web_call_no_order` có ở cả test_bep20.py và test_binance_pay.py, đều pass. `.env.example` có `BEP20_ENABLED=0`; `BINANCE_PAY_ENABLED` chưa có trong `.env.example` (mặc định code vẫn tắt).

## i18n: CHƯA chuyển (quá lớn, theo yêu cầu chỉ liệt kê)
Chuỗi tiếng Việt cứng trong code mới của `shop_bot.py` (~35 chuỗi, test hiện assert trên bản tiếng Việt):
- Nút: "USDT — Binance Pay (dán ID lệnh)", "USDT BEP20 — Quét QR ví", "Chọn Lại Phương Thức Thanh Toán", "Trở Về Sảnh VIP", "TÔI ĐÃ THANH TOÁN — KIỂM TRA", "TÔI ĐÃ CHUYỂN — DÁN ID LỆNH", "Liên Hệ Admin".
- Hoá đơn BEP20 (tiêu đề, Mã đơn, Sản phẩm, Mạng, Số tiền CHÍNH XÁC, Địa chỉ ví nhận, Hạn thanh toán, 2 dòng cảnh báo) và hoá đơn Binance Pay (tiêu đề, Số tiền, Binance Pay ID nhận, hướng dẫn 2 dòng).
- Alert: "Phương thức này chưa được bật.", "Số lượng không hợp lệ.", "Không tìm thấy đơn hàng.", "Đơn này không còn chờ thanh toán.", "Đã thấy giao dịch nhưng cần admin kiểm tra."
- Lỗi tạo đơn (BEP20 + Binance): hết hàng, sản phẩm chưa hỗ trợ, hệ thống bận, quá nhiều đơn chờ, lỗi chung.
- Prompt dán ID lệnh + 10 thông báo kết quả xác nhận (not_found, amount_mismatch, invalid_transaction, before_order, already_used, too_fast, too_many_attempts, unavailable, config, rejected).
- Tin nhắn: đã dừng nhận ID, ID sai định dạng, đã xác nhận nhưng chưa giao được, cần admin kiểm tra thủ công, đơn hết hạn, "cần admin kiểm tra thủ công" ở màn kiểm tra.
Đã i18n: chỉ thông báo "đang chờ xác nhận (x/y)" (`bep20_confirming`). Hậu quả: khi bật cờ, khách chọn tiếng Anh vẫn thấy các chuỗi trên bằng tiếng Việt. Đề xuất task riêng trước khi bật cờ ở production.

## Sau khi merge
Railway có thể đổi sang main: main sẽ chứa i18n/ChatGPT Go (PR #4) + BEP20/Binance (PR này) (+ bảo hành khi PR #5 merge). Cần giữ nguyên env `BEP20_ENABLED`/`BINANCE_PAY_ENABLED` hiện có trên Railway.
