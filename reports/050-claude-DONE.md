# Task 050 — Menu mới (Cộng đồng, Ví) + thiết kế liên kết ví

Branch `feat/menu-wallet-community` từ `origin/main` fe2cc21. Không push main/merge/deploy/Railway/DB, không migration, không bật cờ, không đọc secret, không gọi dịch vụ ngoài.

## Phần A — code
- `i18n.py`: `btn_orders` = "Đơn đã mua"/"My Orders", `btn_support` = "Hỗ trợ"/"Support"; thêm `btn_wallet` ("Ví của tôi"/"My Wallet"), `btn_community` ("Cộng đồng"/"Community"), `wallet_coming_soon`.
- `shop_bot.py`: `wallet_enabled()` (`WALLET_ENABLED=="1"`, mặc định tắt), `community_url()` (chỉ nhận `https://t.me/<...>` không khoảng trắng, không thì ẩn nút; không hard-code link), `main_menu_keyboard()` bố cục: [Mua hàng(xanh) | Ví*] / [Đơn đã mua | Hỗ trợ] / [Cộng đồng*(url, full width)] / [Ngôn ngữ] (*chỉ khi bật). Handler `wallet` hiện "Tính năng ví đang được hoàn thiện" + nút về menu (không hiện số dư). callback_data cũ giữ nguyên: shop, my_orders, support, lang_menu.
- Icon: chỉ dùng emoji có sẵn trong `VIP_EMOJIS`: Ví = `wallet`, Cộng đồng = `user` (chưa có icon nhóm người; giữ `user`, cần id mới thì chủ shop xác nhận), các nút cũ giữ nguyên (crown/diamond/chat/globe).
- `.env.example`: `COMMUNITY_URL=` (trống), `WALLET_ENABLED=0`.
- Nút ngôn ngữ vẫn hiển thị "Ngôn ngữ: Tiếng Việt"/"Language: English" (cho biết ngôn ngữ hiện tại); chưa đổi thành "Ngôn ngữ" trơn.
- Test: `tests/test_menu_wallet_community.py` (7 test: menu mặc định vi/en, menu đầy đủ, Cộng đồng ẩn với 8 giá trị URL sai, Ví ẩn nếu cờ khác "1", không ký tự lỗi, callback ≤64 byte/chỉ 1 trong callback|url, handler wallet vi/en). Cập nhật nhãn ở `test_i18n.py`, `test_main_screen.py`.
- `python -m unittest discover -s tests` -> `Ran 197 tests ... OK`, `$LASTEXITCODE` = 0.

## Phần B — thiết kế (không code)
`docs/bot-wallet-link-design.md`. Điểm phản biện chính: (1) dùng lại chữ ký HMAC `botAuth` có sẵn thay vì so secret thô; (2) limit theo IP ở endpoint bot vô nghĩa vì web chỉ thấy IP Railway, đặt limit IP ở endpoint tạo mã và thêm trần lần sai toàn cục; (3) rủi ro lừa khách gửi mã cho kẻ khác => v1 chỉ xem số dư, thêm cảnh báo + gỡ liên kết hai phía; (4) 1 tài khoản <-> 1 Telegram, UNIQUE hai cột; (5) 0 function Vercel mới (api/ chỉ có index.ts và itunes.ts; module mới ở api/_lib/tg-wallet.ts); (6) câu hỏi mở: `feat/retire-topup-page` gợi ý trang nạp tiền có thể bị gỡ.
