# Thiết kế: liên kết Telegram với tài khoản web để xem số dư ("Ví của tôi")

Trạng thái: **ĐỀ XUẤT, chưa có code, chờ chủ shop duyệt.** Không có migration nào được chạy; SQL bên dưới chỉ để duyệt.
Phạm vi v1: bot chỉ **xem số dư** và mở trang nạp tiền trên web. **Không** thanh toán bằng số dư trong bot.

Căn cứ đã đọc (origin/main của repo web, chỉ đọc): bảng `users(id SERIAL, email, password_hash, display_name, balance INTEGER, ...)` ở `api/index.ts`; xác thực bot bằng chữ ký HMAC ở `api/_lib/tg-sales-helpers.ts` + middleware `botAuth` trong `api/_lib/tg-sales-routes.ts`; rate limit dùng chung ở `api/_lib/rate-limit.ts`.

## 1. Luồng đề xuất (và phản biện)

1. Khách đăng nhập web, vào Profile, bấm **Liên kết Telegram**.
2. Web (xác thực bằng phiên đăng nhập) tạo **mã một lần**: 8 ký tự Crockford base32 (≈ 40 bit) từ `crypto.randomBytes` (CSPRNG), hết hạn **10 phút**, chỉ lưu `code_hash`. Tạo mã mới thì mã chưa dùng cũ của tài khoản đó bị vô hiệu.
3. Khách mở bot, bấm **Ví của tôi** -> **Liên kết**, gửi mã trong chat riêng.
4. Bot gọi `POST /api/tg/wallet/link` kèm `tg_user_id` (lấy từ update của Telegram, KHÔNG phải do khách gõ) và `code`.
5. Web kiểm tra mã nguyên tử (một câu lệnh), gắn `tg_user_id` vào tài khoản, trả kết quả. Bot báo thành công.

Phản biện / chỗ cần chỉnh so với bản nghiêng của bạn:

- **Xác thực bot**: web đã có sẵn chữ ký `X-Bot-Timestamp` + `X-Bot-Signature = HMAC-SHA256(TG_BOT_API_SECRET, ts\nMETHOD\nPATH?QUERY\nsha256(body))`, so sánh `timingSafeEqual`, cửa sổ thời gian, chống replay bằng `tg_bot_nonces`. Dùng lại đúng `botAuth` này, **không** tự viết cách "so secret thô". Bot Python đã có client ký sẵn (`utils/wolfmod_api.py`, hàm `sign_request`).
- **Giới hạn theo IP phải đặt đúng chỗ**: ở endpoint `/api/tg/wallet/link`, IP mà web thấy luôn là IP Railway của bot, không phải IP khách, nên limit theo IP ở đó vô nghĩa (hoặc chặn nhầm cả bot). Hạn chế chia như sau:
  - Endpoint **tạo mã** (phiên web): giới hạn theo `user_id` (5 mã/giờ) **và** theo IP thật của khách (10/giờ).
  - Endpoint **nhập mã** (bot): giới hạn theo `tg_user_id` (5 lần sai/giờ, rồi khoá 1 giờ) **và** một trần **toàn cục** lần sai (ví dụ 300/giờ) để chặn kẻ tạo hàng loạt tài khoản Telegram để đoán mã.
- **Đoán mã**: 40 bit + sống 10 phút + trần lần sai ở trên => xác suất đoán trúng thực tế ~ không. Vì mã là ngẫu nhiên entropy cao nên băm bằng `HMAC-SHA256(pepper, code)` hoặc SHA-256 là đủ (không cần bcrypt); `code_hash` UNIQUE để tra cứu trực tiếp.
- **Không phân biệt lý do lỗi** (không tồn tại / hết hạn / đã dùng đều trả `invalid_or_expired_code`) để không lộ thông tin.
- **Chỉ nhận trong chat riêng** (`chat.type == "private"`), bỏ qua nhóm. Bot cố gắng xoá tin nhắn chứa mã sau khi xử lý (best-effort).
- **Lừa đảo (social engineering)**: kẻ xấu xin khách "gửi mã cho mình" rồi tự nhập vào bot của họ => Telegram của kẻ xấu xem được số dư tài khoản khách. Giảm thiểu: web hiển thị cảnh báo to cạnh mã ("Chỉ nhập mã này vào bot của WolfMod, KHÔNG gửi cho ai"); Profile hiển thị Telegram đang liên kết + nút **Gỡ**; bot khi liên kết xong hiện tên hiển thị đã che (ví dụ `Le Don***`) để khách nhận ra tài khoản; v1 chỉ xem số dư nên thiệt hại giới hạn ở lộ số dư. **Đây là lý do v1 không cho trả bằng số dư trong bot.** Khi làm v2 (thanh toán) cần thêm bước xác nhận trên web hoặc OTP.

## 2. Vì sao KHÔNG cho nhập username/email để xem số dư

Username/email không phải bí mật và không chứng minh người đang chat là chủ tài khoản: ai cũng đoán/biết được email người khác, nhập vào là xem được số dư (lộ thông tin tài chính, và là bước đầu để lừa đảo). Mã một lần sinh ra từ phiên **đã đăng nhập** mới chứng minh được "người này kiểm soát tài khoản web" và "Telegram ID này là của chính người đó" (telegram_id do Telegram cấp qua bot đã ký, không giả được từ phía khách).

## 3. Cơ sở dữ liệu (CHỈ LIỆT KÊ, KHÔNG CHẠY)

```sql
CREATE TABLE IF NOT EXISTS tg_wallet_link_codes (
  id            BIGSERIAL PRIMARY KEY,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash     CHAR(64) NOT NULL UNIQUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at    TIMESTAMPTZ NOT NULL,
  used_at       TIMESTAMPTZ,
  used_by_tg_id BIGINT
);
CREATE INDEX IF NOT EXISTS idx_tg_wallet_codes_user ON tg_wallet_link_codes(user_id) WHERE used_at IS NULL;

CREATE TABLE IF NOT EXISTS tg_wallet_links (
  id          BIGSERIAL PRIMARY KEY,
  user_id     INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  tg_user_id  BIGINT  NOT NULL UNIQUE,
  tg_username VARCHAR(64),
  linked_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- nhật ký (không chứa mã), để điều tra khi có tranh chấp
CREATE TABLE IF NOT EXISTS tg_wallet_link_events (
  id BIGSERIAL PRIMARY KEY, user_id INTEGER, tg_user_id BIGINT,
  event VARCHAR(24) NOT NULL,            -- linked | unlinked | code_issued | rejected
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Rate limit dùng lại `createPostgresRateLimitStore` có sẵn (khoá dạng `tgwl:fail:tg:<id>`, `tgwl:fail:all`, `tgwl:code:user:<id>`, `tgwl:code:ip:<ip>`), không thêm bảng. Chống replay dùng lại `tg_bot_nonces`.

Quan hệ nhiều-nhiều (quyết định v1: **1 tài khoản <-> 1 Telegram**, nhờ hai ràng buộc UNIQUE):
- Một `telegram_id` đã gắn tài khoản A mà nhập mã của tài khoản B: trả `409 telegram_already_linked`; phải gỡ liên kết cũ trước (không tự đè, tránh bị chiếm).
- Một tài khoản đã gắn Telegram X mà liên kết Telegram Y: trả `409 account_already_linked`; phải gỡ X trước.
- Nếu sau này cần nhiều Telegram/tài khoản: bỏ UNIQUE(user_id), giữ UNIQUE(tg_user_id), giới hạn số lượng (ví dụ 3).
- **Gỡ liên kết** được từ cả hai phía: web (đã đăng nhập) và bot (chủ Telegram tự gỡ, vì chỉ làm giảm quyền truy cập). Xoá dòng `tg_wallet_links`, ghi `tg_wallet_link_events`, vô hiệu mã chưa dùng của tài khoản.

## 4. Endpoint web cần thêm

Tất cả nằm trong **một file mới** `api/_lib/tg-wallet.ts` (xuất `registerTgWalletRoutes(app, deps)` giống `tg-bep20.ts`), `api/index.ts` chỉ thêm 1 dòng import + 1 lời gọi đăng ký. Các route phía bot đặt dưới `/api/tg/...` để đi qua `botAuth`.

| Route | Xác thực | Input | Output thành công | Lỗi |
|---|---|---|---|---|
| `POST /api/user/telegram-link/code` | phiên web + chống CSRF hiện có | không | `200 {code, expires_at}` (mã hiện đúng 1 lần) | 401, 409 `account_already_linked`, 429 |
| `GET /api/user/telegram-link` | phiên web | không | `200 {linked, tg_username?, linked_at?}` | 401 |
| `DELETE /api/user/telegram-link` | phiên web | không | `200 {ok:true}` | 401 |
| `POST /api/tg/wallet/link` | `botAuth` (HMAC) | `{tg_user_id:int, code:/^[A-Z0-9]{8}$/}` | `200 {linked:true, display_name_masked}` | 400 `invalid_input`, 404 `invalid_or_expired_code`, 409 `telegram_already_linked` / `account_already_linked`, 429 `too_many_attempts` (+`retry_after`), 401, 503 |
| `GET /api/tg/wallet?tg_user_id=` | `botAuth` | `tg_user_id` | `200 {linked:false}` hoặc `{linked:true, balance_vnd, display_name_masked}` | 400, 401, 503 |
| `POST /api/tg/wallet/unlink` | `botAuth` | `{tg_user_id}` | `200 {ok:true}` | 404 `not_linked`, 401 |

Xử lý nguyên tử khi nhập mã (một transaction, một câu lệnh quyết định):
`UPDATE tg_wallet_link_codes SET used_at=NOW(), used_by_tg_id=$tg WHERE code_hash=$h AND used_at IS NULL AND expires_at>NOW() RETURNING user_id;` rồi `INSERT INTO tg_wallet_links ... ` trong cùng transaction; vi phạm UNIQUE thì rollback (mã không bị tiêu hao) và trả 409.

**Giới hạn 12 function của Vercel**: Vercel chỉ tính file không bắt đầu bằng `_` trực tiếp trong `api/`. Hiện chỉ có `api/index.ts` và `api/itunes.ts` (2 function); thư mục `api/_lib/` không tính. Thiết kế này thêm **0 function mới** (1 file trong `_lib`, đăng ký qua Express của `index.ts`). `vercel.json` không cần đổi.

## 5. Bot hiển thị gì

- Nút **Ví của tôi** (đã có khung, bật bằng `WALLET_ENABLED=1`).
- Chưa liên kết: hướng dẫn 3 bước + nút url mở trang Profile web + nút **Nhập mã liên kết** (bot chờ tin nhắn tiếp theo, chỉ nhận chuỗi đúng định dạng).
- Đã liên kết: **số dư** (VNĐ), tên hiển thị đã che, nút url **Nạp tiền** (mở web), nút **Gỡ liên kết** (có bước xác nhận), nút Quay lại.
- Không có nút trả bằng số dư. Không lưu cache số dư lâu (gọi web mỗi lần mở), không log mã/số dư. Chuỗi hiển thị đi qua `i18n.py` (vi/en).
- Câu hỏi mở: `feat/retire-topup-page` trên máy web cho thấy trang nạp tiền có thể đang bị gỡ; cần chủ shop xác nhận URL nạp tiền đích trước khi làm nút "Nạp tiền".

## 6. Rủi ro bảo mật và test cần có

| Rủi ro | Giảm thiểu | Test |
|---|---|---|
| Replay mã | `used_at` đặt trong UPDATE nguyên tử; replay request bị `tg_bot_nonces` chặn | dùng mã lần 2 => 404; gửi lại cùng chữ ký => 401 |
| Brute force | 40 bit, 10 phút, 5 sai/giờ/telegram_id, trần toàn cục | 6 lần sai => 429 kể cả khi lần 6 đúng; trần toàn cục |
| telegram_id giả | chỉ nhận từ request có chữ ký bot; bot lấy id từ update Telegram, bỏ qua nhóm | thiếu/sai chữ ký, timestamp cũ => 401; `tg_user_id` không phải số nguyên dương => 400 |
| Race hai người cùng dùng một mã | UPDATE ... WHERE used_at IS NULL RETURNING (chỉ 1 dòng trả về) | chạy 2 request song song: đúng 1 thành công, 1 `404`; trạng thái DB chỉ 1 link |
| Race cùng telegram_id liên kết 2 tài khoản | UNIQUE(tg_user_id) + rollback không đốt mã | 2 mã khác tài khoản song song: 1 thành công, 1 `409` |
| Mã lộ/hết hạn | hết hạn 10 phút, mã mới vô hiệu mã cũ, chỉ lưu hash | mã hết hạn => 404; tạo mã mới => mã cũ 404; DB không chứa mã gốc |
| Lộ số dư cho người không phải chủ | chỉ trả theo `tg_user_id` đã gắn; không nhận email/username | telegram chưa liên kết => `linked:false`, không có số |
| Gỡ liên kết | xoá link, vô hiệu mã chưa dùng, ghi event | gỡ từ web và từ bot; sau gỡ `GET` => `linked:false` |
| Lộ thông tin qua lỗi | cùng một mã lỗi cho không tồn tại/hết hạn/đã dùng; log không chứa mã | so sánh phản hồi 3 trường hợp giống nhau |
| Đo thời gian | so sánh hash qua UNIQUE index, chữ ký qua `timingSafeEqual` | (đã có test chữ ký) |

## 7. Câu hỏi cần chủ shop chốt

1. 1 tài khoản <-> 1 Telegram (đề xuất) hay cho nhiều?
2. Đúng 8 ký tự / 10 phút / 5 sai mỗi giờ? Có muốn bot thông báo cho khách khi liên kết/gỡ liên kết?
3. URL "Nạp tiền" đích là gì (xem mục 5)?
4. Có muốn bước xác nhận trên web (khách bấm "Cho phép Telegram này") trước khi liên kết có hiệu lực? An toàn hơn, thêm một bước.
