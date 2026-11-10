# Thiết kế: Ví riêng trong bot (không liên kết web)

Trạng thái: **chỉ thiết kế, chưa có code chức năng.** Thay thế `docs/bot-wallet-link-design.md` (luồng liên kết mã với tài khoản web, PR #9 / web #78). Mọi PR code của tài liệu này mặc định **TẮT** sau cờ `WALLET_ENABLED` (hiện có).

## 0. Quyết định của chủ shop

1. Mỗi khách (Telegram user id) có một số dư VNĐ riêng, chỉ nằm trong dữ liệu của bot.
2. Nạp tiền tự động bằng các cách thanh toán sẵn có của bot (VietQR/SePay, USDT, và nếu bật: Binance Pay, BEP20).
3. Khách trả tiền khi mua hàng bằng số dư (nút "Trả bằng ví").
4. Web `TG_WALLET_ENABLED` tắt; bot không gọi `/api/tg/wallet*` nữa.

## A. Kiểm tra lưu trữ (quan trọng nhất)

### Hiện trạng đọc từ code

| Điều đã xác nhận | Bằng chứng |
|---|---|
| DB là **một file SQLite** `shop_database.db` | `shop_bot.py:72-73` (`DATA_DIR`, `DB_PATH`), `shop_bot.py:211-215` (`sqlite3.connect(DB_PATH)`) |
| Thư mục dữ liệu lấy từ `SHOP_DATA_DIR`, nếu không có thì `RAILWAY_VOLUME_MOUNT_PATH`, nếu không có nữa thì `./data` cạnh code | `shop_bot.py:72` |
| Tài liệu của repo nói Volume `tele_shops-volume` gắn ở `/data`, `SHOP_DATA_DIR=/data`, chạy **một replica**, giữ Volume khi redeploy | `README.md:12-13`, `AI_HANDOFF.md:12-13` |
| `Dockerfile` **không** khai báo `VOLUME`; `railway.json` chỉ có builder và start command; cấu hình Volume nằm trong dashboard Railway, **không nằm trong repo** | `Dockerfile`, `railway.json` |
| Không có PRAGMA nào (không WAL, không `busy_timeout`, không `foreign_keys`); mỗi lần gọi mở một connection mới, timeout mặc định 5 giây | grep `PRAGMA\|journal_mode\|busy_timeout` trong `shop_bot.py` không có kết quả |
| Không có mã hay tài liệu sao lưu trong repo | grep `backup` trong `*.py` và `README.md`/`AI_HANDOFF.md` không có kết quả nào |

### Kết luận và rủi ro

- Nếu Volume **đang gắn đúng** ở `/data` và `SHOP_DATA_DIR=/data` thì số dư bền qua redeploy. Nếu **thiếu một trong hai**, DB nằm ở `./data` trong container (ổ tạm) và **toàn bộ số dư mất mỗi lần deploy/restart container**. Từ code không kiểm chứng được cấu hình Railway (tôi không đọc cấu hình Railway theo quy tắc của dự án). **Goof/Reviewer phải xác nhận thủ công** trước khi bật ví: trên Railway mở service `TELE_SHOPS` > Volumes (có `/data`) và Variables (`SHOP_DATA_DIR=/data`). Bot nên **tự cảnh báo** khi `WALLET_ENABLED=1` mà `DATA_DIR` không nằm dưới `RAILWAY_VOLUME_MOUNT_PATH` (log + từ chối bật ví).
- Volume vẫn là một ổ duy nhất: xoá Volume, hỏng file hoặc một lỗi migrate là mất số dư. Cần sao lưu.

### Đề xuất lưu trữ

**Phương án chọn: giữ SQLite trên Railway Volume** (đúng yêu cầu "dữ liệu chỉ nằm trong bot"), cộng với:

1. `PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON` ở **mỗi connection** (`get_db_connection`), vì tiền không được mất khi mất điện/restart.
2. Giao dịch ví luôn dùng `BEGIN IMMEDIATE` (khoá ghi ngay từ đầu, tránh deadlock đọc rồi nâng cấp khoá).
3. **Sao lưu:** job định kỳ (ví dụ 6 giờ/lần và trước mỗi lần cộng/trừ admin) dùng `sqlite3.Connection.backup()` hoặc `VACUUM INTO` sang `/data/backups/shop-YYYYMMDD-HHMM.db`, giữ 14 bản gần nhất; **lệnh admin `/backup`** gửi file DB riêng cho admin trong chat Telegram (chứa Telegram id + số dư: chỉ gửi cho admin, không log nội dung). Bản trong cùng Volume không cứu được khi mất Volume, vì vậy cần bản ngoài Railway: admin tải về định kỳ hoặc dùng Railway Volume backup nếu gói hỗ trợ.
4. Test tự động: khởi tạo DB tạm, ghi ví, đóng, mở lại, số dư còn nguyên.

**Phương án dự phòng:** nếu không thể bảo đảm Volume, lưu số dư ở Postgres của web qua API ký HMAC sẵn có. Bền hơn (có backup của nhà cung cấp) nhưng cần thêm route web, mở rộng bề mặt tấn công và **ràng buộc bot với web** mà Goof muốn tránh. Chỉ dùng khi Volume không đáng tin.

## B. Mô hình dữ liệu (chỉ liệt kê, chưa chạy)

Số tiền luôn là **VNĐ nguyên (INTEGER)**, không float, không làm tròn ở tầng ví.

```sql
CREATE TABLE IF NOT EXISTS wallet_accounts (
    user_id     INTEGER PRIMARY KEY,
    balance_vnd INTEGER NOT NULL DEFAULT 0 CHECK (balance_vnd >= 0),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Sổ cái bất biến: chỉ INSERT.
CREATE TABLE IF NOT EXISTS wallet_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('topup','purchase','refund','admin_adjust')),
    amount_vnd    INTEGER NOT NULL,           -- có dấu: +cộng, -trừ
    balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
    ref           TEXT NOT NULL,              -- order_id (topup/purchase/refund) hoặc id duy nhất (admin_adjust)
    note          TEXT,                       -- lý do (admin_adjust bắt buộc)
    actor         TEXT,                       -- 'system' hoặc telegram id admin
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (kind IN ('topup','refund') AND amount_vnd > 0) OR
        (kind = 'purchase'          AND amount_vnd < 0) OR
        (kind = 'admin_adjust'      AND amount_vnd <> 0)
    ),
    UNIQUE (kind, ref),                       -- idempotent: một tham chiếu chỉ ghi một lần cho mỗi loại
    FOREIGN KEY (user_id) REFERENCES wallet_accounts(user_id)
);
CREATE INDEX IF NOT EXISTS idx_wallet_ledger_user ON wallet_ledger(user_id, id);

CREATE TRIGGER IF NOT EXISTS wallet_ledger_no_update BEFORE UPDATE ON wallet_ledger
BEGIN SELECT RAISE(ABORT, 'wallet_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS wallet_ledger_no_delete BEFORE DELETE ON wallet_ledger
BEGIN SELECT RAISE(ABORT, 'wallet_ledger is append-only'); END;

-- Mở rộng bảng orders (ALTER có try/except như các cột cũ):
--   ALTER TABLE orders ADD COLUMN kind TEXT NOT NULL DEFAULT 'purchase';   -- 'purchase' | 'wallet_topup'
--   payment_method nhận thêm giá trị 'wallet' (đơn mua trả bằng ví).
-- Chỉ một đơn trả-bằng-ví đang xử lý mỗi khách (chống bấm đúp / race):
CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_inflight
    ON orders(user_id) WHERE payment_method = 'wallet' AND status = 'wallet_processing';
```

Quy ước:
- **Bất biến sổ cái:** `balance_vnd` luôn bằng tổng `amount_vnd` của khách; có test đối soát (mục F). Số dư không bao giờ UPDATE ngoài các hàm ví.
- Mọi thay đổi số dư đi qua **một module** `wallet_store.py` (API: `credit_topup`, `debit_purchase`, `refund`, `admin_adjust`, `get_balance`) – mỗi hàm là một transaction `BEGIN IMMEDIATE`, ghi ledger **và** cập nhật `wallet_accounts` trong cùng transaction.
- `ref`: topup = `order_id` của đơn nạp; purchase = `order_id` đơn mua; refund = `order_id` đơn mua (một đơn chỉ hoàn một lần); admin_adjust = `ADJ<token_hex(6)>`.

## C. Nạp tiền (đơn nạp ví)

### Cách bot xác nhận thanh toán hiện nay (đọc từ code)

- VietQR/SePay: bot tạo đơn SQLite (`shop_bot.py:2414`), đồng bộ lên web qua `POST /api/tg/orders/sync`; webhook SePay đánh dấu **paid trên web**; khi khách bấm "kiểm tra" bot hỏi `GET /api/tg/orders/status` (`check_web_order_status`, `shop_bot.py:1198`) và nếu `paid/delivered` thì giao hàng (`send_paid_order_delivery`, `shop_bot.py:1120`). Bot là worker polling, **không có HTTP server** nhận webhook.
- Vòng nền `auto_cancel_expired_orders_task` (`shop_bot.py:3270`, mỗi 15 giây) huỷ đơn `pending` quá 10 phút **nhưng bỏ qua đơn web đã báo `paid/delivered`**; hiện chưa có vòng nền nào tự giao hàng, giao hàng chỉ chạy khi khách bấm "kiểm tra".
- BEP20 / Binance Pay: bot hỏi web (`check_web_bep20`, `confirm_web_binance`), web xác minh on-chain / Binance.
- Hệ quả: **web vẫn là nơi xác nhận tiền vào**, dù số dư ví nằm ở bot. Thiết kế dưới đây giữ đúng ranh giới đó: web chỉ nói "đơn X đã thanh toán", bot tự cộng ví.

### Đơn nạp: loại đơn mới

- `orders.kind = 'wallet_topup'`, `product_id = 'wallet_topup'` (không thuộc `known_skus()`), `amount` = số VNĐ khách chọn, `payment_method` = `vietqr|plisio_usdt|bep20|binance`.
- Tái dùng: tạo hoá đơn (QR VietQR, hoá đơn USDT, BEP20, Binance), hạn 10 phút, màn "kiểm tra thanh toán".
- **Phải bỏ qua** đơn `wallet_topup` ở: `sync_all_pending_orders_on_startup` (`shop_bot.py:1212`, vì nó đồng bộ theo SKU), `render_orders_page` (liệt kê đơn đã mua; hiện riêng "Lịch sử ví"), `deliver_product`/kho (không có hàng để giao).
- **Điều kiện bên web (phát hiện quan trọng):** `POST /api/tg/orders/sync` (`api/_lib/tg-sales-routes.ts:963-1009`) với SKU lạ **không từ chối** mà gán `product_id = 1` và tên "Google Gemini Plus"; đơn nạp sẽ hiện trong dashboard admin như một đơn Gemini Plus và lọt vào thống kê/kho. Đề xuất một PR web nhỏ, sau cờ, chấp nhận `kind: "wallet_topup"` (không gán sản phẩm, không tính kho/doanh thu sản phẩm, vẫn khớp SePay theo mã đơn + số tiền, vẫn trả `status=paid`). **Cần Goof/Reviewer đồng ý** vì đây là thay đổi web (task này không làm). Không có thay đổi này thì PR 2 (nạp tiền) chưa nên bật.

### Điểm cộng tiền duy nhất, idempotent

Một hàm duy nhất `credit_topup(order_id, source_status)` trong `wallet_store.py`, mọi đường xác nhận đều gọi hàm này (nút kiểm tra, job quét nền, bất kỳ đường nào về sau):

```
BEGIN IMMEDIATE
  order = SELECT ... FROM orders WHERE order_id=? AND kind='wallet_topup'
  nếu không có / user không khớp           -> ROLLBACK, lỗi
  nếu order.status = 'completed'           -> ROLLBACK, trả "đã cộng" (idempotent)
  INSERT wallet_accounts(user_id) ON CONFLICT DO NOTHING
  UPDATE wallet_accounts SET balance_vnd = balance_vnd + order.amount
  INSERT wallet_ledger(kind='topup', amount=+order.amount, balance_after, ref=order_id, actor='system')
       -- UNIQUE(kind, ref): lần thứ hai (restart, bấm lặp, hai tiến trình) vi phạm UNIQUE -> ROLLBACK, coi như đã cộng
  UPDATE orders SET status='completed', completed_at=now WHERE order_id=? AND status IN ('pending','cancelled','expired')
COMMIT
```

- Cộng đúng **số VNĐ khách đã chọn khi tạo đơn**, không cộng theo số nhận được. USDT/BEP20: hoá đơn cố định ở thời điểm tạo (VNĐ ÷ 25.000 làm tròn 2 số, hiển thị USD **chỉ để tham khảo**); web đã yêu cầu khớp đúng số tiền nên sai số tiền không được xác nhận.
- **Hạn mức đề xuất:** tối thiểu 20.000đ, tối đa 2.000.000đ mỗi lần nạp, số dư tối đa 5.000.000đ, tối đa 3 đơn nạp đang chờ mỗi khách. Hằng số cấu hình, Goof chốt.
- Vòng nền 15 giây ở trên cần xử lý `wallet_topup` như đơn khác (đơn đã `paid` không bị huỷ) và nên **tự gọi `credit_topup`** cho đơn nạp web đã báo `paid`, để khách không phải bấm kiểm tra mới thấy tiền; `credit_topup` nhận cả đơn đã bị đánh dấu `cancelled/expired` nếu web báo `paid`.
- Webhook về hai lần / nút bấm lặp / restart: nhờ `UNIQUE(kind, ref)` và `status='completed'` trong cùng transaction nên chỉ cộng một lần.
- **Đơn nạp hết hạn nhưng khách vẫn chuyển:** cách bot xử lý đơn mua hiện nay là **hỏi trạng thái web trước, kiểm tra hết hạn cục bộ sau** (`shop_bot.py:2603-2617` – nếu web báo `paid` thì vẫn giao dù đơn cục bộ đã hết hạn); BEP20 trễ quá hạn thì web trả `needs_review` và bot hướng khách liên hệ admin (`shop_bot.py:2606-2614`). Với nạp ví **đề xuất giống hệt**: web báo `paid` → cộng (cộng ví không có rủi ro hết kho), `needs_review` → báo liên hệ admin, admin dùng `admin_adjust` sau khi đối soát. Tiền đã vào mà bot không cộng tự động thì luôn có đường cứu qua admin.

## D. Trả bằng ví ("Trả bằng ví")

Nút xuất hiện ở màn chọn phương thức (`show_order_confirmation`) khi `WALLET_ENABLED=1`, hiển thị số dư và đủ/thiếu. Giá lấy từ `sync_web_product_price` như các đơn khác.

Vì giao hàng là **cuộc gọi mạng tới web** (`claim_stock_from_web`, không rollback được bằng SQLite), dùng mô hình **trừ tiền rồi giao, hoàn khi thất bại chắc chắn** (hai giai đoạn, mỗi giai đoạn một transaction):

1. **Kiểm tra trước (không đổi dữ liệu):** SKU hợp lệ, số lượng hợp lệ, còn hàng theo số tồn web (như luồng hiện tại) – giảm số lần phải hoàn tiền.
2. **Giai đoạn 1, một transaction `BEGIN IMMEDIATE`:**
   - tạo đơn `payment_method='wallet'`, `status='wallet_processing'` (chỉ mục `uq_wallet_inflight` khiến lần bấm thứ hai, hoặc hai tiến trình, vi phạm ràng buộc → bỏ qua);
   - `UPDATE wallet_accounts SET balance_vnd = balance_vnd - :price WHERE user_id=:u AND balance_vnd >= :price`; `rowcount = 0` → ROLLBACK, báo "không đủ số dư" (cộng `CHECK(balance_vnd >= 0)` là chốt chặn thứ hai chống số âm);
   - ghi ledger `purchase` (`-price`, `ref=order_id`).
3. **Giai đoạn 2:** gọi `deliver_product` / `send_paid_order_delivery` (đã idempotent theo `order_id`: web trả đúng bó link cũ khi gọi lại – `AI_HANDOFF.md`, mục "claims the entire paid quantity atomically"). Thành công → `status='completed'`.
4. **Thất bại:**
   - **Chắc chắn** (web báo hết hàng / từ chối): transaction hoàn: ledger `refund` (`ref=order_id`, `UNIQUE` chống hoàn hai lần), cộng lại ví, `status='cancelled'`, `review_reason='wallet_refund'`.
   - **Không chắc** (lỗi mạng, timeout): **không hoàn ngay** (có thể web đã giao). Giữ `wallet_processing`, thử lại khi khách bấm "kiểm tra", và quét lại khi bot khởi động. Sau 15 phút vẫn không giao được → hoàn tiền và đánh dấu `needs_review` cho admin (vì có khả năng web đã claim).
5. **Restart giữa trừ tiền và giao hàng:** đơn `wallet_processing` còn trong SQLite; bản quét khởi động xử lý lại theo bước 3–4 (idempotent), nên không mất tiền khách, không giao hai lần.
6. Tin nhắn giao hàng gửi lỗi (Telegram) thì dùng lại cơ chế `delivery_notified_at` hiện có.
7. Hàng cần giao dạng link/key lấy từ kho web: cơ chế như hiện nay; ví chỉ thay bước "xác nhận đã thanh toán".

## E. Admin

Chỉ cho admin hiện có (`is_admin`, `shop_bot.py:58`), lệnh riêng như `admin_set_price` (`shop_bot.py:2894`):

- `/wallet <telegram_id>`: xem số dư + 10 dòng ledger gần nhất.
- `/wallet_add <telegram_id> <số_tiền> <lý do>` và `/wallet_sub ...`: gọi `admin_adjust`, `actor` = id admin, **lý do bắt buộc**, giới hạn mỗi lần (ví dụ ≤ 2.000.000đ), không cho số dư âm, trả lại dòng ledger vừa ghi để admin đối chiếu.
- `/backup`: gửi file sao lưu cho admin (mục A).
- Không có lệnh sửa/xoá dòng ledger (và trigger chặn). Sửa nhầm = thêm dòng `admin_adjust` ngược dấu.
- Không log số dư/lý do vào log chung; chỉ log id đơn và loại giao dịch.

## F. Rủi ro và test cần có

| Rủi ro | Test / biện pháp |
|---|---|
| Replay thanh toán (xác nhận hai lần, webhook trùng) | gọi `credit_topup` 2 lần và song song 20 lần → đúng 1 dòng ledger, số dư +1 lần |
| Race hai lần trả cùng lúc | hai tác vụ song song trả cùng số dư → chỉ một thành công (`uq_wallet_inflight` + `balance>=price`); số dư không âm |
| Restart giữa trừ tiền và giao hàng | ép lỗi sau giai đoạn 1; khởi động lại; đơn giao đúng một lần hoặc được hoàn đúng một lần |
| DB mất dữ liệu | cảnh báo khi `DATA_DIR` không nằm trên Volume; test sao lưu/khôi phục; hướng dẫn vận hành |
| Số âm | thử trừ quá số dư, `admin_adjust` âm quá số dư → bị chặn; `CHECK` DB chặn khi code sai |
| Làm tròn | mọi số VNĐ là `int`; test không nhận float/bool/âm/0; USD chỉ hiển thị |
| Đơn nạp hết hạn nhưng khách vẫn chuyển | web `paid` sau hạn → cộng; BEP20 `needs_review` → không cộng tự động; test cả hai |
| Đối soát | test bất biến: `balance_vnd == SUM(amount_vnd)` theo khách sau chuỗi thao tác ngẫu nhiên |
| Sổ cái bị sửa | test `UPDATE`/`DELETE` ledger bị trigger chặn |
| Khách chặn bot | gửi tin giao hàng/hoàn tiền lỗi `Forbidden` **không** làm mất giao dịch: ví đã ghi, tin nhắn thử lại sau; khách vẫn xem được trong "Đơn đã mua"/lịch sử ví khi mở lại |
| Gian lận hoàn tiền | hoàn tiền chỉ vào ví (xem câu hỏi mở), chống hoàn hai lần bằng `UNIQUE(kind, ref)` |
| Lộ dữ liệu | không log số dư/mã/chữ ký; `/backup` chỉ cho admin; không bao giờ đưa ví vào lệnh nhóm/công khai (`_is_private_chat` như luồng cũ) |
| Đơn nạp lẫn vào thống kê web | PR web nhỏ ở mục C; không bật nạp tiền khi chưa có |

## G. Kế hoạch code (PR nhỏ, mặc định tắt)

0. **PR 0 – Dọn luồng liên kết cũ** (trong `shop_bot.py`/`utils`/`i18n`): gỡ `AWAITING_WALLET_CODE`, `handle_wallet_callback`, `handle_wallet_code_message`, `wallet_request`, `render_wallet_screen`, các khoá i18n `wallet_*` liên quan, tiền tố ký `/api/tg/wallet*` trong `utils/wolfmod_api.py` và biến `WOLFMOD_API_SECRET` dành cho ví. Không đổi hành vi khi cờ tắt. (Đề xuất; có thể gộp vào PR 1.)
1. **PR 1 – Nền tảng:** `wallet_store.py`, bảng + trigger, PRAGMA, cảnh báo Volume, màn "Ví của tôi" xem số dư + lịch sử, test (idempotent, bất biến, không âm). Cờ `WALLET_ENABLED`.
2. **PR 2 – Nạp tiền:** đơn `wallet_topup`, chọn số tiền và phương thức, `credit_topup`, bỏ qua trong sync/đơn đã mua, hạn mức. Cờ riêng `WALLET_TOPUP_ENABLED=0`. **Phụ thuộc** PR web chấp nhận `kind: "wallet_topup"` (mục C).
3. **PR 3 – Trả bằng ví:** nút "Trả bằng ví", giai đoạn 1/2, hoàn tiền, quét lại khi khởi động. Cờ `WALLET_PAY_ENABLED=0`.
4. **PR 4 – Admin + sao lưu:** `/wallet`, `/wallet_add`, `/wallet_sub`, `/backup`, job sao lưu định kỳ.

Mỗi PR chạy toàn bộ `python -m unittest discover -s tests`, không gọi dịch vụ ngoài trong test (giả lập web như `test_bep20.py`).

## Câu hỏi mở cần Goof chốt

1. **Volume:** xác nhận Railway đã gắn Volume ở `/data` và `SHOP_DATA_DIR=/data` (và có nơi lưu bản sao lưu ngoài Railway không)?
2. **Hạn mức:** tối thiểu 20.000đ, tối đa 2.000.000đ/lần, số dư tối đa 5.000.000đ có hợp lý không?
3. **Hoàn tiền:** chỉ giữ trong ví (đề xuất, an toàn, không rút ra), hay có khi nào hoàn về ngân hàng/crypto (làm thủ công qua admin)?
4. **Web:** đồng ý làm PR web nhỏ cho `kind: "wallet_topup"` (không gán sản phẩm, không tính doanh thu sản phẩm) không? Hay chấp nhận nạp ví đi qua web như đơn thường (không khuyến nghị)?
5. **Nạp muộn:** đơn nạp hết hạn nhưng web đã nhận tiền: cộng tự động (đề xuất, giống đơn mua) hay luôn chờ admin duyệt?
6. **Phương thức nạp:** bật nạp bằng USDT/BEP20/Binance từ đầu hay chỉ VietQR ở bản đầu?
7. **Trả bằng ví:** cho phép mọi sản phẩm hay loại một số sản phẩm?
8. **Thời hạn hoàn tự động:** 15 phút cho đơn ví "không chắc" có ổn không?
9. **Dọn code cũ:** gỡ hẳn luồng liên kết (PR 0) hay giữ nguyên nhưng không dùng?
