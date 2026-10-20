# Bằng chứng còn thiếu để điều tra hạn chế tài khoản COIN+

Ngày: 20/10/2026 · Loại: chỉ đọc, không can thiệp ứng dụng đang chạy.

Đây là deliverable quan trọng nhất của cuộc điều tra, vì kết luận trung thực là:
**repository này không chứa bằng chứng phía server nào về việc hạn chế tài khoản.**
Tài liệu này liệt kê chính xác cái gì thiếu, vì sao cần, và cách thu thập hợp lệ.

---

## 1. Vì sao repo không có bằng chứng server

Phát hiện PHASE 1 (từ việc đọc chính code của project):

- **Không có HTTP client nào nói chuyện với COIN+.** Mọi lời gọi mạng trong repo
  (`response.json()`/`response.text`) đi tới: OTPBase (dịch vụ OTP SMS —
  `src/core/otp_web_client.py`, `airwallet_otpbase.py`), dịch vụ echo IP để kiểm
  tra proxy (`src/utils/proxy_health.py` → ipify/ifconfig/ipwho), tra cứu địa chỉ
  JP (`src/utils/prefecture.py`), và WebDriverAgent (adapter test —
  `uitest/backends/wda.py`). **Không lời gọi nào tới `jp.coinplus.app`.**
- `jp.coinplus.app` chỉ xuất hiện dưới dạng **bundle id** để Frida attach/spawn
  (`driver.py:8`, `airwallet_dom_runner.py:22`, `crane_container_manager.py:27`).
- Kiến trúc là **UI automation**: project điều khiển giao diện app qua Frida/UIKit.
  Mọi request/response giữa app và COIN+ xảy ra **bên trong tiến trình app**, và
  project **không bao giờ nhìn thấy** chúng. Không có HAR capture, không có
  response logging, không có proxy MITM cho traffic COIN+ (và theo RULES của
  nhiệm vụ cũng không được dựng — không hook TLS pinning, không chặn request).
- Tín hiệu "gần server" duy nhất mà pipeline từng ghi lại là **chữ hiển thị trên
  màn hình**: `diagnostics.py` dùng regex rút mã lỗi hiển thị
  (`diagnostics.py:10`) và khớp vài chuỗi tiếng Nhật đã biết, nhưng để nguyên
  "chưa gán ý nghĩa" (`diagnostics.py:104`). Đây là quan sát UI, không phải bằng
  chứng HTTP.

Hệ quả: các PHASE cần HTTP status / business error code / session error /
restriction response đều **không có gì để phân tích** trong checkout này.

## 2. Bằng chứng runtime trong checkout này

| Nguồn | Trạng thái |
| --- | --- |
| `dumps/` (UI snapshot JSON) | **Không tồn tại** |
| `logs/`, `*.log` | **Không tồn tại / rỗng** |
| `agents/runtime/` | **Không tồn tại** |
| HAR file | Không có |
| Screenshot thông báo khóa | Không có |
| Transaction / reward record | Không có |
| Campaign terms | Không có |

Toàn bộ dữ liệu vận hành bị `.gitignore` loại trừ và nằm trên máy vận hành, không
có ở đây.

## 3. Bằng chứng cần thu thập (hợp lệ) và nó chứng minh được gì

| # | Bằng chứng | Thu thập thế nào (hợp lệ) | Phân biệt được giả thuyết nào |
| --- | --- | --- | --- |
| M1 | **Screenshot thông báo khóa** kèm **timestamp quan sát** | Người vận hành chụp lại, ghi giờ nhìn thấy | Xác nhận trạng thái bị hạn chế (category A) — nhưng **không** cho biết nguyên nhân |
| M2 | **UI dump** (`dumps/*.json`) quanh thời điểm lỗi | Pipeline đã tự ghi khi chạy; lấy từ máy vận hành | Mã lỗi hiển thị, màn hình tại thời điểm đó; phân biệt lỗi client (B) vs thông báo server (A) |
| M3 | **Giờ cấp thưởng campaign** cho từng tài khoản | Lịch sử trong app / email xác nhận | Dựng khoảng cách "thưởng → hạn chế" (PHASE 3) |
| M4 | **Giờ tạo / xác minh / hoạt động cuối** mỗi tài khoản | Dump của pipeline (có timestamp tạo), log batch | Timeline per-account |
| M5 | **Phản hồi của COIN+ Support** khi hỏi trạng thái tài khoản | Liên hệ Support bằng danh tính chủ tài khoản | **Nguồn duy nhất** xác nhận lý do server — xem §5 |
| M6 | **Campaign terms** (điều kiện nhận thưởng) | Trang điều khoản của campaign | Kiểm tra giả thuyết D (eligibility review) |
| M7 | **HTTP response đã được sanitize** (nếu có, do người vận hành xuất hợp lệ) | Chỉ nếu đã tồn tại; **không** dựng MITM mới, không hook pinning | Business error code trực tiếp → có thể nâng A lên CONFIRMED |

## 4. Giới hạn về timestamp (ảnh hưởng PHASE 3)

Pipeline đóng dấu thời gian bằng **giờ máy cục bộ, phần lớn naive** (không tz):
`time.strftime('%Y%m%d-%H%M%S')` ở `main.py:89,105`, `airwallet_dom_runner.py:639`,
`datetime.now()` ở nhiều chỗ của `crane_container_manager.py`. Chỉ
`src/utils/proxy_pool.py:139` ghi `+07:00` (giờ VN) và browser context đặt
`Asia/Tokyo`.

Hệ quả cho forensics:
- Dump timestamp là **giờ máy vận hành**, không ghi tz → phải biết máy đó đặt tz
  nào mới quy đổi về JST được.
- Có khả năng lệch giữa giờ máy vận hành (có thể +07:00 VN) và JST (+09:00) =
  **2 giờ**. Mọi timeline phải ghi rõ giả định tz, nếu không sẽ sai 2 tiếng.
- **Khuyến nghị:** chuẩn hoá toàn bộ dump sang UTC có tz, rồi mới hiển thị JST.

## 5. Chỉ COIN+ Support xác nhận được

Những điều sau **không suy ra được** từ bất kỳ dữ liệu nào ở phía project, kể cả
khi thu thập đủ M1–M7:

- Quy tắc nội bộ nào kích hoạt hạn chế.
- Thời điểm server thực sự đánh dấu tài khoản (khác thời điểm app hiển thị thông báo).
- Việc hạn chế có liên quan campaign, số điện thoại, thiết bị, hay yếu tố khác.
- Tài khoản có khôi phục được không và bằng cách nào.

Việc hỏi Support cần danh tính chủ tài khoản. Xem phần ghi chú phạm vi trong
`docs/ui-automation-audit.md` §6.1 về bản chất pháp lý/điều khoản của việc mở
nhiều tài khoản bằng số điện thoại thuê trên dịch vụ thanh toán.

## 6. Ngoài phạm vi (theo RULES và theo nguyên tắc)

- Không dựng MITM/replay để lấy response COIN+; không hook TLS pinning.
- Không rút token/cookie/key từ Keychain hay memory.
- Không dùng tương quan dữ liệu để suy ra ngưỡng chống bot hay cách né phát hiện —
  kể cả khi có dữ liệu. Đó không phải incident response, đó là evasion.
