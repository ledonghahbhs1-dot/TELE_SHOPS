# Wallet runbook (ví riêng của bot)

Tài liệu vận hành cho chủ shop. Không chứa khoá/mật khẩu: các giá trị bí mật chỉ đặt trong Railway Variables, không ghi vào đây hay vào chat.

## 1. Dữ liệu nằm ở đâu
- Số dư và sổ ví nằm trong SQLite của bot: `<DATA_DIR>/shop_database.db` (bảng `wallet_accounts`, `wallet_ledger`).
- `DATA_DIR` = `SHOP_DATA_DIR`, nếu không có thì `RAILWAY_VOLUME_MOUNT_PATH`, nếu không có nữa thì `./data`.
- **Trên Railway bắt buộc gắn Volume** (ví dụ mount `/data`) và đặt `SHOP_DATA_DIR=/data` (hoặc để Railway tự đặt `RAILWAY_VOLUME_MOUNT_PATH`). Không có Volume thì mỗi lần deploy số dư biến mất. Bot **từ chối bật ví** (`WALLET_ENABLED=1 bị từ chối`, xem log) nếu thư mục dữ liệu nằm ngoài Volume.
- Backup nằm ở `<DATA_DIR>/backups/shop-YYYYMMDD-HHMMSS.db`, giữ 14 bản mới nhất, tự tạo mỗi 6 giờ khi ví bật.

### Kiểm tra Volume và SHOP_DATA_DIR trước khi bật ví
1. Railway → service của bot → Volumes: có một Volume mount tại `/data`.
2. Đặt biến `SHOP_DATA_DIR=/data` trong Railway Variables của bot, rồi redeploy.
3. Kiểm tra bằng shell của service: `railway ssh`, rồi `ls -la /data`. Phải thấy `shop_database.db` (và sau 6 giờ hoặc sau `/backup` có thư mục `backups/`). Nếu `/data` trống hoặc không tồn tại thì dữ liệu đang nằm ngoài Volume: **chưa được bật ví**.
4. Khi bật `WALLET_ENABLED=1` mà thư mục dữ liệu nằm ngoài Volume, log có dòng `WALLET_ENABLED=1 bị từ chối` và ví vẫn tắt.

### Biến cần có ở web (Vercel)
`TG_WALLET_TOPUP_KIND_ENABLED=1` (nạp ví), `TG_WALLET_PAY_ENABLED=1` (trả bằng ví), `TG_WALLET_TOPUP_USDT_VND_RATE` (tỷ giá VND→USDT cho nạp USDT; giá trị do chủ shop quyết). Sau khi đổi biến phải redeploy. Không dán giá trị bí mật vào tài liệu hay chat.

## 2. Các cờ (mặc định đều TẮT; giá trị phải đúng `1`)
| Cờ (bot) | Làm gì | Cần thêm |
|---|---|---|
| `WALLET_ENABLED` | Bật ví, màn hình ví, lệnh admin, backup | Volume đúng |
| `WALLET_TOPUP_ENABLED` | Nạp ví bằng VietQR | web đã deploy route nạp (`kind: wallet_topup`) |
| `WALLET_PAY_ENABLED` | Nút "Trả bằng ví" | web: `TG_WALLET_PAY_ENABLED=1` và đã deploy `/api/tg/orders/wallet-paid`, `/wallet-cancel` |
| `WALLET_TOPUP_USDT_ENABLED` | Nạp ví bằng USDT (Plisio/BEP20/Binance Pay) | web: `TG_WALLET_TOPUP_KIND_ENABLED=1` và `TG_WALLET_TOPUP_USDT_VND_RATE`; mỗi phương thức còn cần cờ riêng như khi mua hàng |

Tắt một cờ là tắt tính năng ngay sau khi redeploy; số dư và sổ giữ nguyên.

## 3. Thứ tự bật (từng bước, thử xong mới sang bước sau)
1. **Chuẩn bị**: gắn Volume, đặt `SHOP_DATA_DIR`, deploy. Xác nhận web đã merge/deploy các route ví. Làm `/backup` thử (mục 5).
2. **Ví + nạp VietQR** (bật `WALLET_ENABLED` và `WALLET_TOPUP_ENABLED` trước, chưa bật cờ nào khác): đặt `WALLET_ENABLED=1`, `WALLET_TOPUP_ENABLED=1`, đợi nạp VietQR thử thành công. Dùng tài khoản của chính bạn nạp **20.000đ** (mức tối thiểu). Kiểm tra: tin báo "đã cộng", `/wallet <telegram_id>` thấy số dư 20.000 và dòng `topup`. Nạp lại cùng một lệnh/đơn không được cộng lần hai.
3. **Mua thử bằng ví**: web đặt `TG_WALLET_PAY_ENABLED=1`, bot đặt `WALLET_PAY_ENABLED=1`. Mua 1 sản phẩm rẻ bằng ví: nhận hàng, số dư giảm đúng giá, sổ có dòng `purchase`. Thử khi hết kho (hoặc sản phẩm không có hàng) để thấy tự hoàn tiền `refund`.
4. **USDT**: đặt `TG_WALLET_TOPUP_USDT_VND_RATE` ở web, rồi `WALLET_TOPUP_USDT_ENABLED=1` ở bot. Thử từng phương thức bằng khoản nhỏ nhất, bắt đầu từ cái bạn đã dùng ổn ở luồng mua hàng.
5. Theo dõi log 24 giờ đầu trước khi quảng bá.

Hạn mức hiện tại: nạp 20.000đ–2.000.000đ mỗi lần, số dư tối đa 5.000.000đ, tối đa 3 đơn nạp đang chờ mỗi khách, admin chỉnh tối đa 2.000.000đ mỗi lệnh.

## 4. Lệnh admin (chỉ admin, chỉ chat riêng, chỉ khi `WALLET_ENABLED` hợp lệ)
- `/wallet <telegram_id>`: xem số dư và các dòng sổ mới nhất.
- `/wallet_add <telegram_id> <số_tiền> <lý do>`: cộng tiền (ví dụ `/wallet_add 123456789 50000 bồi thường đơn lỗi`). Phải có lý do (≤200 ký tự).
- `/wallet_sub <telegram_id> <số_tiền> <lý do>`: trừ tiền; không thể làm số dư âm.
- `/backup`: tạo backup mới (giữ 14 bản) và **gửi file về chat riêng của admin**. Lưu file đó lại: đây là cách đơn giản nhất để có bản sao ngoài Volume.
- Mỗi lệnh `wallet_add/sub` tạo **một dòng sổ mới** (không sửa/xoá được dòng cũ). Gửi lệnh hai lần là hai dòng, nên kiểm tra bằng `/wallet` trước khi gửi lại.

## 5. Sao lưu và khôi phục
- Backup tự động mỗi 6 giờ, giữ 14 bản. Backup cùng Volume với DB, nên **định kỳ gõ `/backup` và lưu file bot gửi về chat** (hoặc tải file từ Volume) rồi cất ở nơi khác.
- Khôi phục (mất/hỏng DB):
  1. Tắt bot (stop service) để không ghi vào DB.
  2. Trong Volume: đổi tên `shop_database.db` hiện tại thành `shop_database.db.broken`, xoá `shop_database.db-wal` và `shop_database.db-shm` nếu có.
  3. Chép bản backup muốn dùng thành `shop_database.db` (ví dụ `cp backups/shop-20260101-120000.db shop_database.db`).
  4. Bật lại bot. Khi khởi động bot tự tạo bảng còn thiếu; kiểm tra `/wallet` vài khách quen.
  5. Giao dịch xảy ra sau thời điểm backup sẽ không có: đối chiếu đơn nạp/đơn ví gần đây với web và dùng `/wallet_add`/`/wallet_sub` (kèm lý do) để chỉnh.
- Dùng backup mới nhất còn nguyên vẹn; nếu mở bằng `sqlite3` báo lỗi thì thử bản cũ hơn.

## 6. Lỗi hay gặp
| Hiện tượng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| Ví không bật dù `WALLET_ENABLED=1` | Dữ liệu không nằm trong Volume | Gắn Volume, đặt `SHOP_DATA_DIR` trong Volume, redeploy |
| Nạp VietQR báo lỗi "không khả dụng" | Web chưa deploy/bật route `kind: wallet_topup` hoặc web lỗi | Kiểm tra web; bot huỷ đơn nên không có đơn treo |
| Khách chuyển khoản nhưng chưa thấy cộng | Chờ vòng quét nền (vài phút); đơn hết hạn vẫn được cộng nếu web báo đã trả | Bảo khách bấm kiểm tra; nếu web báo `needs_review` thì admin đối soát và `/wallet_add` |
| "Trả bằng ví" báo bận | Khách có đơn ví đang xử lý (mỗi khách 1 đơn một lúc) | Chờ; đơn tự xong hoặc tự hoàn tiền |
| Đơn ví treo ở "đang xử lý" | Web chậm/lỗi mạng | Bot tự thử lại mỗi phút; quá 15 phút tự hoàn tiền và báo admin đối soát với web |
| Admin nhận cảnh báo "refunded after 15 min" | Không xác nhận được giao hàng | Mở đơn trên web: nếu đã giao thì trừ lại bằng `/wallet_sub` kèm lý do; nếu chưa giao thì không cần làm gì |
| Web trả `wallet_pay_disabled` | Bot bật `WALLET_PAY_ENABLED` nhưng web chưa đặt `TG_WALLET_PAY_ENABLED=1` | Bật cờ web hoặc tắt cờ bot; khách được hoàn tiền tự động |
| Số dư khác sổ | Không được xảy ra | Dừng bật thêm tính năng, giữ nguyên DB và backup, báo kỹ thuật |
