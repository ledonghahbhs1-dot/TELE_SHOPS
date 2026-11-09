# Bot Binance Pay (USDT) — thiết kế UX hội thoại / Conversation UX

> **Trạng thái / Status:** CHỈ THIẾT KẾ (design only). Chưa gắn vào code chạy; không sửa `shop_bot.py`.
> Task 044. Nhánh: `docs/bot-binance-pay-ux` (từ `feat/binance-pay-usdt-rebased`, chưa merge).

## 0. Sự thật về luồng hiện có (đã đọc code, không đoán)

| Điều | Thực tế trong code | Hệ quả thiết kế |
|---|---|---|
| Bot là **thanh toán đơn hàng**, không phải nạp ví | `show_binance_checkout` tạo đơn `TGS…` cho một SKU × số lượng; số USDT = **giá đúng** của đơn do web trả về (`/api/tg/orders/binance-pay`). | Không có bước "nhập số USDT". Bước 2 của kịch bản là **chọn sản phẩm + số lượng** (có sẵn). Nếu sau này muốn nạp ví bằng bot cần endpoint mới (xem §9, câu hỏi mở #1). |
| Mã đơn | `TGS` + 4–17 ký tự `[A-Z0-9]` (`ORDER_CODE_RE`). Web dùng `BPW…`. | Hai hệ dùng chung bảng `tg_binance_pay_claims` (mỗi ID Binance chỉ claim 1 lần). |
| Hạn đơn | 30 phút (`ORDER_TTL_MINUTES`), hóa đơn hiển thị giờ hết hạn `HH:MM:SS`. | Bot không có đồng hồ đếm ngược sống; dùng giờ hết hạn + nhắc. |
| Kiểm tra | Khách **dán Order ID** (8–32 chữ số). Tối đa **5 lần/đơn**, cách nhau **≥10 giây** (`too_fast`, `too_many_attempts`). Lỗi mạng Binance **hoàn lại lượt**. | "Tôi đã chuyển" phải dẫn tới bước dán ID, không tự kiểm tra được. |
| Chờ ID | `AWAITING_BINANCE_ID`: 20 phút, tối đa 3 lần nhập sai định dạng. | Giữ nguyên các giới hạn này. |
| Nút "Hủy" | Bot đã có `cancel_order_<order_id>` cho đơn pending (USDT/VietQR). | Tái sử dụng; chưa xác minh nó đóng đơn Binance Pay phía web (câu hỏi mở #2). |
| Nút "Sao chép" | Inline button thường **không** sao chép. Bot API có `copy_text` (CopyTextButton, python-telegram-bot ≥ 21.7; `requirements` đang `>=21.3,<22.0`). | Mặc định dùng `<code>…</code>` (chạm để chép). Nút Copy chỉ thêm nếu nâng PTB; nếu client cũ không hỗ trợ thì ẩn. |
| Kết quả web | `paid`, `needs_review`, `expired`, `cancelled`; hoặc `pending` + lý do `not_found/amount_mismatch/invalid_transaction/before_order/already_used`; HTTP 429 `too_fast/too_many_attempts`; 400 `invalid_order_id`; 503 `can_cau_hinh`/lỗi tạm. | Bảng thông điệp §5 ánh xạ 1-1; **không bao giờ hiện mã kỹ thuật** cho khách. |

Quy ước: `{…}` là biến; tin nhắn dùng HTML parse mode, số tiền/ID/mã đơn trong `<code>`. Emoji theo bộ `vip_em` hiện có (ở đây ghi bằng emoji thường).

---

## 1. Sơ đồ trạng thái

```
[S0 MENU] ──chọn Binance Pay──▶ [S1 TẠO HÓA ĐƠN] ──ok──▶ [S2 HÓA ĐƠN] ──"Tôi đã chuyển"──▶ [S3 CHỜ ID]
                                      │ lỗi                    │ Hủy                              │ dán ID
                                      ▼                        ▼                                  ▼
                                 [E1 KHÔNG TẠO ĐƯỢC]       [S6 ĐÃ HỦY]                      [S4 ĐANG KIỂM TRA]
                                                                                                  │
        ┌───────────────┬───────────────┬───────────────┬───────────────┬───────────────────────┤
        ▼               ▼               ▼               ▼               ▼                       ▼
   [R1 THÀNH CÔNG] [R2 CHƯA THẤY] [R3 SAI SỐ TIỀN] [R4 ĐÃ DÙNG] [R5 HẾT HẠN / DUYỆT TAY]  [R6 HẾT LƯỢT / LỖI TẠM]
```

---

## 2. Trạng thái S0 — Chọn phương thức (đã có, chỉ đổi nhãn)

**Thông điệp:** (màn chọn phương thức thanh toán hiện tại của đơn `{sku} × {qty}`)
**Nút:** hàng mới chèn khi `binance_pay_enabled()`:

| Nút (vi) | Nút (en) | callback_data | Điều kiện |
|---|---|---|---|
| `💠 Nạp Binance Pay (USDT)` | `💠 Pay with Binance Pay (USDT)` | `cp_<sku>_<qty>` (= `cb_qty("binance", sku, qty)`) | `BINANCE_PAY_ENABLED=1` |

**Chuyển trạng thái:** bấm nút → S1. Bấm lại cùng nút trên cùng tin nhắn → dùng lại cùng `order_id` (hash của user/chat/message/qty/sku), không tạo đơn thứ hai.

---

## 3. Trạng thái S1 — Tạo hóa đơn

Bot gọi web `POST /api/tg/orders/binance-pay`. Hiện "đang tạo" bằng `query.answer()` (toast) rồi sửa tin nhắn.

### Lỗi tạo (E1) — sửa tin nhắn tại chỗ

| Mã web (ẩn với khách) | vi | en |
|---|---|---|
| `product_unavailable` | `Sản phẩm này chưa hỗ trợ Binance Pay hoặc số lượng vượt mức cho phép. Vui lòng chọn cách thanh toán khác.` | `This product doesn't support Binance Pay or the quantity is not allowed. Please choose another payment method.` |
| mạng / `invalid_response` / khác | `Chưa thể tạo hóa đơn Binance Pay. Bạn chưa bị trừ gì. Vui lòng thử lại hoặc chọn VietQR.` | `We couldn't create the Binance Pay invoice. You haven't been charged. Please try again or choose VietQR.` |

**Nút:** `Chọn lại phương thức` (`cb_qty("select", sku, qty)`), `Về sảnh` (`main_menu`).
**Chuyển:** → S0.

---

## 4. Trạng thái S2 — Hóa đơn (tin nhắn chính)

**vi**
```
💎 HÓA ĐƠN USDT — BINANCE PAY 💎

🏷 Mã đơn: <code>{order_code}</code>
📦 Sản phẩm: {product} × {qty}
💵 Số tiền: <code>{amount_usdt}</code> USDT   ← chạm để chép
🆔 Binance Pay ID nhận: <code>{receiver_id}</code>   ← chạm để chép
⏳ Hạn thanh toán: <code>{HH:MM:SS}</code> (còn 30 phút)

⚠️ Chuyển CHÍNH XÁC {amount_usdt} USDT, không làm tròn.
Chọn Binance Pay (không chuyển on-chain). Sai số tiền hoặc sai loại tiền sẽ không được xác nhận tự động.

Cách chuyển:
1) Binance → Pay → Gửi, nhập ID nhận ở trên.
2) Nhập đúng số USDT, xác nhận.
3) Mở chi tiết giao dịch, giữ lại “Order ID”, rồi bấm nút bên dưới.
```
**en**
```
💎 USDT INVOICE — BINANCE PAY 💎

🏷 Order: <code>{order_code}</code>
📦 Product: {product} × {qty}
💵 Amount: <code>{amount_usdt}</code> USDT   ← tap to copy
🆔 Receiver Binance Pay ID: <code>{receiver_id}</code>   ← tap to copy
⏳ Pay before: <code>{HH:MM:SS}</code> (30 minutes left)

⚠️ Send EXACTLY {amount_usdt} USDT. Do not round.
Choose Binance Pay (not an on-chain withdrawal). A wrong amount or currency can't be confirmed automatically.

How to pay:
1) Binance → Pay → Send, enter the receiver ID above.
2) Type the exact USDT amount and confirm.
3) Open the payment details, keep the “Order ID”, then tap the button below.
```

**Nút (inline):**

| Hàng | Nút vi / en | callback_data | Ghi chú |
|---|---|---|---|
| 1 | `✅ Tôi đã chuyển — dán ID lệnh` / `✅ I've paid — paste Order ID` | `bpid_<order_code>` | → S3 |
| 2 | `📋 Chép số tiền` / `📋 Copy amount` · `📋 Chép ID nhận` / `📋 Copy ID` | `copy_text` (nếu PTB ≥ 21.7) | tùy chọn, ẩn nếu không hỗ trợ |
| 3 | `❌ Hủy đơn` / `❌ Cancel` | `cancel_order_<order_code>` | → S6 |
| 4 | `🏠 Về sảnh` / `🏠 Home` | `main_menu` | giữ đơn pending |

**Nhắc hạn (tùy chọn, 1 lần):** còn ~5 phút mà đơn vẫn `pending` → gửi `⏳ Đơn {order_code} sắp hết hạn (còn 5 phút). Đã chuyển rồi? Bấm “Tôi đã chuyển” để dán ID lệnh.` / `⏳ Order {order_code} expires in 5 minutes. Already paid? Tap “I've paid” and paste the Order ID.`

**Chuyển:** `bpid_` → S3 · Hủy → S6 · quá hạn không thao tác → R5 (khi khách dán ID web sẽ trả `expired`/`needs_review`).

---

## 5. Trạng thái S3 — Chờ ID, S4 — Đang kiểm tra

### S3 — chờ khách gửi ID (tin nhắn mới)
Đặt `AWAITING_BINANCE_ID[user]` = {order, 20 phút, bad=0}.

**vi:** `🏷 Gửi Order ID Binance Pay cho đơn <code>{order_code}</code>. Dán dãy số trong chi tiết giao dịch Binance, ví dụ <code>453343187271753728</code>. Chỉ gửi chữ số (8–32 số).`
**en:** `🏷 Send the Binance Pay Order ID for order <code>{order_code}</code>. Paste the number from the Binance payment details, e.g. <code>453343187271753728</code>. Digits only (8–32).`

**Nút:** `❌ Hủy nhập` / `❌ Stop` (`bpstop_<order_code>`, xóa `AWAITING_BINANCE_ID`).

**Điều kiện chuyển:**
- Tin nhắn khớp `^\d{8,32}$` → S4.
- Sai định dạng: `bad += 1`; `bad < 3` → nhắc lại (không gọi web, không tốn lượt); `bad = 3` → thoát S3.
  - vi: `ID lệnh phải là dãy 8–32 chữ số, ví dụ 453343187271753728. Vui lòng gửi lại.` / dừng: `Đã dừng nhận ID lệnh. Bấm lại “Tôi đã chuyển” khi bạn có ID.`
  - en: `The Order ID must be 8–32 digits, e.g. 453343187271753728. Please send it again.` / stop: `Stopped waiting for the Order ID. Tap “I've paid” again when you have it.`
- Quá 20 phút → xóa trạng thái; tin nhắn kế tiếp xử lý như bình thường (không trả lời cứng).

### S4 — đang kiểm tra
Trả lời ngay `🔎 Đang kiểm tra với Binance…` / `🔎 Checking with Binance…` (sửa thành kết quả khi xong). Gọi `confirm_web_binance` (timeout 25 s). Trong lúc chờ, bỏ qua tin nhắn ID khác (xem §7).

---

## 6. Thông điệp kết quả (R1–R6)

Mọi trạng thái lỗi đều **thân thiện, không lộ mã kỹ thuật**. Cột "Web trả" chỉ để dev ánh xạ.

| ID | Web trả | Giữ S3? | Lượt | vi | en |
|---|---|---|---|---|---|
| R1 | `paid` | thoát | — | `✅ Thanh toán thành công! Đơn {order_code} đã xác nhận, link đang được gửi…` (giao hàng bằng `send_paid_order_delivery`). Nếu giao thất bại: `Thanh toán đã xác nhận nhưng link chưa gửi được lúc này. Vui lòng liên hệ @wolfmodyt kèm mã đơn.` | `✅ Payment confirmed! Order {order_code} is paid, your item is on its way…` Fallback: `Payment is confirmed but we couldn't deliver automatically. Please contact @wolfmodyt with your order code.` |
| R2 | `not_found` | có | tính | `Chưa thấy giao dịch này. Binance có thể mất khoảng 1 phút để hiện. Kiểm tra lại Order ID và thử lại sau 10 giây.` | `We can't see this payment yet. Binance can take about a minute. Check the Order ID and try again in 10 seconds.` |
| R3 | `amount_mismatch` | có | tính | `Số tiền không khớp: đơn này cần đúng {amount_usdt} USDT. Đừng chuyển thêm — hãy liên hệ @wolfmodyt kèm mã đơn {order_code}.` | `The amount doesn't match: this order needs exactly {amount_usdt} USDT. Don't send more — contact @wolfmodyt with order {order_code}.` |
| R4 | `already_used` | có | tính | `Order ID này đã được dùng cho một đơn khác. Nếu không phải của bạn, hãy gửi Order ID của chính giao dịch bạn đã chuyển.` | `This Order ID was already used for another order. If it isn't yours, send the Order ID of your own payment.` |
| R4b | `before_order` | có | tính | `Giao dịch này có trước khi tạo đơn nên không dùng được. Hãy chuyển lại và gửi Order ID mới.` | `This payment is older than your order, so it can't be used. Pay again and send the new Order ID.` |
| R4c | `invalid_transaction` | có | tính | `Đây không phải giao dịch hợp lệ tới shop. Kiểm tra bạn trả bằng USDT qua Binance Pay đúng ID nhận ở hóa đơn.` | `This isn't a valid payment to us. Check you paid in USDT through Binance Pay to the receiver ID on the invoice.` |
| R5a | `expired` / `cancelled` | thoát | — | `Đơn {order_code} đã hết hạn hoặc bị hủy. Chưa chuyển? Hãy tạo đơn mới. Đã chuyển rồi? Đừng chuyển lại — liên hệ @wolfmodyt kèm mã đơn.` | `Order {order_code} has expired or was cancelled. Not paid? Create a new order. Already paid? Don't pay again — contact @wolfmodyt with the order code.` |
| R5b | `needs_review` | thoát | — | `Đơn {order_code} cần admin kiểm tra thủ công (thanh toán đến sau hạn). Liên hệ @wolfmodyt kèm mã đơn.` | `Order {order_code} needs a manual check (the payment arrived after the deadline). Contact @wolfmodyt with the order code.` |
| R6a | `too_fast` (429) | có | không tốn | `Bạn thử hơi nhanh. Vui lòng đợi khoảng 10 giây rồi gửi lại.` | `That was a bit quick. Please wait about 10 seconds and send it again.` |
| R6b | `too_many_attempts` (429) | thoát | hết | `Đơn này đã dùng hết lượt kiểm tra. Đã chuyển rồi? Liên hệ @wolfmodyt kèm mã đơn {order_code}. Chưa chuyển? Hãy tạo đơn mới.` | `This order has used all its checks. Already paid? Contact @wolfmodyt with order {order_code}. Otherwise create a new order.` |
| R6c | lỗi mạng/Binance (503 lỗi tạm, `unavailable`) | có | hoàn lại | `Tạm thời chưa kiểm tra được với Binance. Lượt này không bị tính. Thử lại sau ít phút.` | `We can't reach Binance right now. This check wasn't counted. Please try again in a few minutes.` |
| R6d | `can_cau_hinh` (503) | thoát | — | `Giao dịch này cần admin kiểm tra thủ công. Liên hệ @wolfmodyt kèm mã đơn {order_code}.` | `This payment needs a manual check. Contact @wolfmodyt with order {order_code}.` |
| R6e | `invalid_order_id` (400) | có | không tốn | như S3 "sai định dạng" | như S3 bad format |
| R6f | khác / không rõ | có | — | `Không thể xác nhận giao dịch này lúc này. Vui lòng thử lại sau ít phút.` | `We couldn't confirm this right now. Please try again in a few minutes.` |

**Nút kèm kết quả:**
- R2/R3/R4/R4b/R4c/R6a/R6c/R6e/R6f (còn ở S3): `✅ Tôi đã chuyển — dán ID lệnh` (→ S3 lại), `💬 Hỗ trợ` (url `https://t.me/wolfmodyt`).
- R3 thêm nút `💬 Gửi mã đơn cho admin` (url có sẵn mã đơn trong text).
- R5/R6b/R6d: `🛒 Tạo đơn mới` (`main_menu`), `💬 Hỗ trợ`.
- R1: `📦 Đơn của tôi`, `🏠 Về sảnh`.

**Đếm lượt hiển thị cho khách (tùy chọn):** sau R2/R3/R4*, thêm dòng `Còn tối đa {n}/5 lượt kiểm tra.` / `Up to {n} of 5 checks left.` — **web không trả số lượt còn lại**, bot chỉ đếm cục bộ (ước lượng, như UI web). Xem §9 #3.

---

## 7. Quy tắc chống spam bấm nút / gửi ID

1. **Khóa theo (user, order)**: khi đang S4 (gọi web) đặt cờ `checking[user]=True`; mọi `bpid_*` / tin nhắn ID trong lúc đó → `query.answer("Đang kiểm tra, vui lòng đợi…", show_alert=False)` hoặc bỏ qua im lặng; mở khóa trong `finally`.
2. **Cooldown 10 giây phía bot** (khớp web): sau mỗi lần gọi web (trừ R6c hoàn lượt thì cũng giữ 10 s vì web vẫn ghi `last_attempt_at`) lưu `next_ok[user]=now+10s`. Gửi sớm hơn → trả R6a ngay **không gọi web**.
3. **Định dạng sai không tốn lượt, nhưng tối đa 3 lần** rồi thoát S3 (giữ nguyên `BINANCE_MAX_BAD_INPUTS`).
4. **Chống tạo đơn trùng**: `order_id` suy ra từ (user, chat, message, qty, sku) nên bấm nhiều lần chỉ dùng lại một đơn; mỗi user tối đa 5 đơn Binance Pay đang pending (đồng bộ giới hạn web); vượt → `Bạn đang có quá nhiều đơn chờ thanh toán. Hãy hoàn tất hoặc hủy đơn cũ.` / `You have too many unpaid orders. Finish or cancel an old one first.`
5. **`query.answer()` ngay** cho mọi callback (tránh Telegram quay vòng và người dùng bấm lại).
6. **Hủy**: `cancel_order_*` chỉ chạy khi đơn `pending`; bấm lại sau khi hủy → `Đơn này đã được hủy.` / `This order is already cancelled.`
7. **Không lặp thông điệp**: kết quả cuối (R1, R5, R6b, R6d) xóa `AWAITING_BINANCE_ID`, và gỡ bàn phím của hóa đơn để không bấm lại được.
8. **Rate limit chung** theo `deps.limited` của web vẫn là hàng rào cuối; bot không được thử lại tự động khi gặp 429.

---

## 8. Trạng thái S6 — Đã hủy

**vi:** `Đã hủy đơn {order_code}. Nếu bạn đã lỡ chuyển tiền, đừng chuyển lại — liên hệ @wolfmodyt kèm mã đơn.`
**en:** `Order {order_code} cancelled. If you already sent the payment, don't pay again — contact @wolfmodyt with the order code.`
**Nút:** `🛒 Tạo đơn mới`, `🏠 Về sảnh`. **Chuyển:** → S0.

---

## 9. Câu hỏi mở / khoảng trống cần quyết trước khi triển khai

1. **Nạp ví bằng bot?** Yêu cầu gốc nêu "nhập số USDT" cho bot; code hiện chỉ có thanh toán **đơn hàng** với số tiền cố định theo giá. Cần xác nhận: (a) giữ checkout theo đơn (kịch bản trên), hay (b) thêm "nạp ví" cho bot → cần endpoint web riêng (không có trong hợp đồng hiện tại).
2. **Hủy đơn Binance Pay:** `cancel_order_*` đã có ở bot nhưng chưa xác minh nó vô hiệu hóa đơn phía web (`tg_orders`); nếu chưa, khách "Hủy" rồi vẫn có thể dán ID cho đơn đó.
3. **Số lượt còn lại:** `/api/tg/orders/binance-pay/confirm` và `/api/topup/binance-pay/confirm` đều **không** trả `attempts_left`. UX chỉ đếm cục bộ. Đề xuất thêm `attempts_left` (và `retry_after_s` ở mọi phản hồi 200) — đây là thay đổi hợp đồng nên chưa làm.
4. **Nút Copy** cần `python-telegram-bot ≥ 21.7` (hiện `>=21.3,<22.0`).
5. **Đồng hồ đếm ngược** không khả thi trong Telegram (không tự cập nhật); dùng giờ hết hạn + nhắc 1 lần.
