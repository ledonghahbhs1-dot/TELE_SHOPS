# Task 049 — Banner sản phẩm theo ngôn ngữ (vi/en)

Branch `feat/product-banners` từ `origin/main` 0eccc1f (chưa chứa PR #5/#6). Không push main/merge/deploy/Railway/DB, không gọi Telegram thật.

## Xác nhận ngôn ngữ ảnh (đã mở từng ảnh)
| Nguồn | Nội dung thấy | File đặt tên | Kích thước | Bytes |
|---|---|---|---|---|
| 395a2fb2…-clean.png | "Special Offer", "Get Offer", "Everything in Free, plus" = EN | chatgpt_go_3m_en.png | 1024x572 | 623,592 |
| 35844b47…-clean.png | "Đăng ký gói", "Đăng ký Go" = VI | chatgpt_go_3m_vi.png | 1024x571 | 1,110,224 |
| 6300950339200423027.jpg | "24-MONTH PLAN" = EN | gemini_pro_account_en.jpg | 1280x714 | 177,090 |
| watermarked_img_1114…-clean.png | "GÓI 24 THÁNG" = VI | gemini_pro_account_vi.png | 1024x571 | 1,070,418 |
| 6309758784….jpg | "GÓI 12 THÁNG" = VI | gemini_plus_vi.jpg | 1024x571 | 109,105 |
| watermarked_img_4535…-clean.png | "12-MONTH PLAN" = EN | gemini_plus_en.png | 1024x571 | 1,012,316 |
Tất cả < 10 MB, cạnh dài ≤ 1280 px (không phải giảm). Copy nguyên bản, SHA-256 nguồn = đích cho cả 6 file. JPG giữ đuôi .jpg. SKU khớp `product_content.json`/`tg_products` (chatgpt_go_3m, gemini_pro_account, gemini_plus).
Gemini Pro EN và các ảnh VI có logo/ký hiệu góc dưới phải (nguyên bản, không chỉnh).

## Code (shop_bot.py)
- `get_product_banner_path(sku, lang=None)`: `assets/products/<sku>_<lang>.(png|jpg|jpeg)` -> ảnh ngôn ngữ kia -> ảnh cũ `assets/<sku>.jpg` -> `None` (giữ hành vi chỉ văn bản).
- `send_product_screen(..., lang)`: ảnh gửi bằng `reply_photo` trước nút mua (caption = giới thiệu + nút, hoặc caption ngắn rồi tin riêng nếu >1000 ký tự – như cũ). Lần đầu upload file, lưu `file_id` vào dict trong bộ nhớ `_banner_file_ids` (key = đường dẫn + mtime); các lần sau dùng file_id; file_id lỗi thì tự upload lại. Không có bảng DB mới.
- Đổi ngôn ngữ: trang sản phẩm được dựng lại theo `lang` hiện tại mỗi lần mở (đổi ngôn ngữ thực hiện ở menu chính), nên ảnh đổi theo.

## Test
`tests/test_product_banners.py` (7 test): đủ 6 file hợp lệ (magic bytes, <10MB), chọn đúng ảnh vi/en cho cả 3 SKU, fallback sang ngôn ngữ kia, SKU không ảnh -> None, upload 1 lần + dùng lại file_id + đổi en dùng ảnh en, file_id cũ lỗi -> upload lại, SKU không ảnh gửi văn bản. Sửa `tests/test_multi_product.py` (1 test patch `get_product_banner_path=None` vì gemini_pro_account nay có ảnh).
`python -m unittest discover -s tests` -> `Ran 152 tests ... OK`, `$LASTEXITCODE` = 0 (145 + 7).

## Lưu ý
- `assets/gemini_plus.jpg` cũ giữ làm fallback cuối.
- Dockerfile dùng `COPY . .` và `.dockerignore` không loại assets (đã grep), nên ảnh mới vào image.
