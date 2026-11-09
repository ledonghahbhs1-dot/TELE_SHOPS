# Task 046 — Bot i18n (vi/en), menu "Mua hàng", ChatGPT Go 3 tháng, thông báo nhập kho

Branch: `feat/bot-i18n-chatgpt-go` (từ `origin/main` 206cac6; không đụng `feat/binance-pay-usdt*`). Không PR, không merge, không deploy Railway.

## Kết quả kiểm thử (số thật)
- Baseline trước khi sửa: `python -m unittest discover -s tests` → 102 test, `OK`, exit code **0**.
- Sau khi sửa: `python -m unittest discover -s tests` → **137 test, OK, exit code 0** (102 cũ + 35 mới trong `tests/test_i18n.py`).
- Test hạ tầng: `tests/test_i18n.py` chặn `urllib.request.urlopen` (không gọi web/Telegram thật), dùng SQLite tạm (không đụng DB production), không đọc env/token.
- Test cũ phải sửa có chủ đích (menu chính đã đổi, trang sản phẩm dựng từ dữ liệu): `test_main_screen`, `test_multi_product`, `test_auto_discover`, `test_orders_guides` (+1 dòng `default_lang=vi` ở setUp các file cũ để chúng tiếp tục kiểm tra giao diện tiếng Việt).

## Đã làm
1. **i18n tập trung** `i18n.py`: `t(lang, key, **kw)`; thiếu bản dịch → dùng tiếng Anh + log warning; thiếu khóa → trả tên khóa + warning. Test quét AST: mọi khóa dùng trong code đều có, mọi khóa có đủ vi+en, placeholder vi/en khớp nhau.
2. **Lưu ngôn ngữ theo user_id**: bảng `user_lang` (`CREATE TABLE IF NOT EXISTS` — idempotent, không mất dữ liệu; test chạy `init_database` 3 lần).
   - **Quyết định mặc định**: khách mới → `en` (như web; Goof đổi bằng setting `default_lang`). Khách **chưa chọn nhưng đã có đơn hàng** → `vi`. Khách đã bấm chọn → luôn theo lựa chọn. (Bot không có bảng "users" nên khách cũ chưa từng đặt đơn sẽ thấy tiếng Anh.)
   - Nút `Ngôn ngữ` ở menu chính + lệnh `/language`; callback `lang_menu`, `setlang:vi|en`.
3. **Menu**: Menu chính = `Mua hàng` / `Đơn Hàng VIP Của Tôi` / `CSKH 24/7` / `Ngôn ngữ`. 3 sản phẩm nằm trong `Mua hàng` (`shop`). Toàn bộ callback_data cũ giữ nguyên (`main_menu`, `buy_*`, `select_qty_*`, `sq:/cv:/cu:`, `soldout_*`, `gd:`, `my_orders`, `support`, `confirm_pay_*`…) — có test.
4. **ChatGPT Go 3 THÁNG** (SKU đề xuất `chatgpt_go_3m`): 100.000 VNĐ ≈ **4.00 USD** (hằng số `BOT_VND_PER_USD = 25000` trong `i18n.py`, không dùng `USDT_VND_RATE` của web). Dùng cơ chế SKU/kho/giao hàng/thanh toán hiện có. Hết hàng → nhãn "HẾT HÀNG / OUT OF STOCK", nút không mua được, tạo đơn bị chặn. **Không thêm kho giả.**
5. **Trang chi tiết sản phẩm** (vi/en) dựng từ `product_content.json` (sửa không cần đụng code): tên, thời hạn, giá VNĐ+USD, quyền lợi, cách giao, bảo hành (chỉ hiện nếu có), lưu ý. Google AI Plus và Gemini Pro dùng nội dung có sẵn trong code (chuyển sang file, giữ ý nguyên, dịch EN).
6. **Thông báo nhập kho mới** (`stock_notify.py` + `stock_watch_task`): tên đầy đủ + ≤5 dòng quyền lợi + giá (VNĐ/USD) + tồn kho, theo ngôn ngữ từng người nhận (mỗi nhóm ngôn ngữ một bản), nút **Mua ngay** → trang chi tiết (`buy_<sku>`), không in mã SKU/link. Gộp: tối đa 1 tin / sản phẩm / 5 phút (lần nhập đầu gửi ngay, các lần sau trong 5 phút gộp thành một). Áp dụng cho mọi sản phẩm hiện có & sau này (đọc từ cùng danh mục).
7. Mẫu mô phỏng vi & en: `reports/046-samples/samples-vi.png`, `samples-en.png` (+ `.html`, `samples.json`, script `render_samples.py`).

## Thông báo nhập kho — hiện gửi thế nào (quan trọng)
- Ảnh "THÔNG BÁO NHẬP KHO VIP / Gói dịch vụ: gemini_pro_account" **không do bot này gửi**: nó nằm ở **web** (`api/_lib/tg-sales-routes.ts`, khi Goof nhập kho trên dashboard) và chỉ gửi cho `ADMIN_ID` (một người), in thẳng SKU nếu không phải `gemini_plus`. Repo bot không sửa được; tin này vẫn sẽ tới admin song song với tin mới → **Goof cần quyết định tắt/sửa đoạn đó bên web** (xem câu hỏi 6).
- Trong bot trước đây: chỉ có `/addstock` (chỉ `gemini_plus`) trả lời cho admin; `watch_and_notify_new_links` đã tắt; setting `notify_channel` có lưu nhưng không dùng.
- Mới: `stock_watch_task` so sánh tồn kho mỗi 60 giây (bắt cả kho nhập từ web). Người nhận mặc định: admin + `notify_channel` (ngôn ngữ kênh: setting `notify_channel_lang`, mặc định vi). Gửi cho **mọi khách** chỉ khi bật setting `stock_notify_customers=true` (mặc định TẮT). Tắt cả tính năng: `stock_notify_enabled=false`.

## Cách Goof nhập kho ChatGPT Go
Bot không có kho riêng cho SKU này; kho nằm trên web. Cần: (1) tạo sản phẩm loại `item` trên Wolfmod dashboard với **SKU `chatgpt_go_3m`**, giá 100000, max_qty 1; (2) nhập kho (tài khoản/link/mã, mỗi dòng một mục) ở tab TG Sales. Trước đó bot hiển thị sản phẩm "HẾT HÀNG" và không cho mua.

## Nội dung ChatGPT Go — **CẦN GOOF DUYỆT**
Nguồn: OpenAI Help Center "What is ChatGPT Go?" https://help.openai.com/en/articles/11989085-what-is-chatgpt-go , tra cứu **2026-11-09**. OpenAI có nhiều phiên bản bài viết, tính năng/giới hạn có thể đổi — cần kiểm tra lại trang trực tiếp. Đã chỉ ghi các ý có trong nguồn (gắn "theo OpenAI"): gói giá thấp mở rộng quyền so với Free; hạn mức cao hơn cho tạo ảnh/tải tệp/phân tích dữ liệu; "Think" trên web & mobile; chế độ giọng nói hạn mức như Free; không gồm mô hình legacy, Sora, API; có thể có quảng cáo. Cờ `needs_owner_approval: true` trong `product_content.json`. Cách giao hàng (tài khoản / link kích hoạt / mã) **chưa biết**; bảo hành **để trống (không hiển thị)**; chưa có hướng dẫn riêng (không bịa).

## Câu hỏi Goof cần chọn
1. **Gom menu**: đã hiểu "Mua hàng" = một mục chứa cả 3 sản phẩm (Google AI Plus, Gemini Pro, ChatGPT Go). Đúng ý chưa? Nút danh sách vẫn ghi giá/kho trên từng nút.
2. **Ngôn ngữ mặc định**: khách mới = English, khách đã có đơn = Tiếng Việt. Muốn mặc định khác (vd. tất cả vi) → đặt `default_lang`.
3. **Duyệt nội dung ChatGPT Go** (quyền lợi EN/VI) và **cách giao** (tài khoản / link / mã) + **chính sách bảo hành**.
4. **SKU**: dùng `chatgpt_go_3m` OK không? (nếu đổi, sửa khóa trong `product_content.json`). Cần tạo sản phẩm tương ứng trên web.
5. **Người nhận thông báo kho**: giữ admin + kênh, hay bật gửi cho mọi khách (`stock_notify_customers=true`)? Kênh dùng ngôn ngữ nào (`notify_channel_lang`)?
6. **Tin nhập kho cũ bên web** (admin-only, in mã kỹ thuật): tắt hoặc đổi cùng định dạng mới? (cần task bên web)
7. Gemini Pro: trang sản phẩm giờ lấy quyền lợi từ `product_content.json`, không lấy mô tả từ web nữa — chấp nhận? Ngoài ra thời hạn và bảo hành của Gemini Pro chưa có dữ liệu nên không hiển thị.
8. Giá USD hiển thị = VNĐ/25.000 cho mọi sản phẩm ở trang chi tiết (vd. Plus 14.999 ≈ 0.60 USD); số USDT thật lúc thanh toán vẫn do web/Plisio tính — chấp nhận?

## Phạm vi không đổi / còn lại
- Không đổi logic thanh toán, giá hiện có, quyền admin. Chuỗi chỉ dành cho admin (bảng quản trị, `/setprice`, `/addstock`…) và log vẫn tiếng Việt.
- `get_usdt_price` cho SKU có cờ `usd_from_rate` mà web chưa có giá USDT sẽ hiển thị VNĐ/25.000 (4.0); SKU khác giữ nguyên hành vi cũ.
