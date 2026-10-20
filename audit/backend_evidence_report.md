# Báo cáo bằng chứng phía server — hạn chế tài khoản COIN+

Ngày: 20/10/2026 · Loại: chỉ đọc, không can thiệp app đang chạy, không thay đổi
code production. Đi kèm: [`missing_evidence.md`](missing_evidence.md),
[`backend_hypotheses.md`](backend_hypotheses.md),
[`account_timeline.csv`](account_timeline.csv),
[`error_classification.csv`](error_classification.csv).

---

## Tóm tắt điều hành

**Repository này không chứa bằng chứng phía server nào về việc hạn chế tài khoản,
nên không thể phân loại hay dựng timeline từ dữ liệu hiện có.** Kết luận duy nhất
được *hỗ trợ* (chưa CONFIRMED) là: trạng thái hạn chế có nguồn gốc từ server, vì
app hiển thị một chuỗi thông báo do server phát ra. **Nguyên nhân** của việc hạn
chế không xác định được từ bất kỳ dữ liệu nào ở phía project; chỉ COIN+ Support
xác nhận được.

Điều này đúng với cả ba lần cùng một câu hỏi được đặt ra: không có defect kỹ
thuật nào trong repo tạo ra một đợt hạn chế nhiều-tài-khoản. Việc đi tìm "trigger
giao dịch" cũng không khớp dữ liệu, vì bối cảnh nêu rõ các tài khoản chưa giao
dịch sau khi nhận thưởng.

---

## PHASE 1 — Kết quả đọc repository

Xác định bằng cách đọc chính code của project (grep HTTP client, response
handling, domain, timestamp). Không suy diễn endpoint hay schema.

| Hạng mục | Phát hiện | Vị trí |
| --- | --- | --- |
| HTTP client tới COIN+ | **Không tồn tại** | — |
| HTTP client có trong repo | Chỉ tới OTPBase, echo-IP (proxy), tra cứu JP, WebDriverAgent | `src/core/otp_web_client.py`, `src/utils/proxy_health.py`, `src/utils/prefecture.py`, `uitest/backends/wda.py` |
| `jp.coinplus.app` dùng làm gì | Chỉ là bundle id để Frida attach/spawn | `driver.py:8`, `airwallet_dom_runner.py:22`, `crane_container_manager.py:27` |
| Xử lý response COIN+ | **Không có** — app tự làm I/O mạng bên trong tiến trình; project chỉ drive UI | — |
| Business error code | Chỉ rút từ **chữ trên màn hình**, để "chưa gán ý nghĩa" | `diagnostics.py:10,103` |
| Authentication / session | Không quản lý session COIN+; app tự giữ | — |
| Account status / reward status | Không đọc từ API; chỉ suy từ màn hình (vd 'アカウントが作成されました') | `diagnostics.py:33` |
| Frida app lifecycle | attach/spawn/kill/respring | `airwallet_dom_runner.py` |
| Crane container lifecycle | chọn/switch/ensure container | `crane_container_manager.py` |
| Proxy configuration | một proxy/tài khoản, ghi IP exit | `src/core/airwallet_proxy.py`, `scripts/proxy_switcher.py` |
| Retry / timeout / logging | wait theo điều kiện, backoff lỗi hạ tầng, watchdog; log ra stderr | `flow.py`, `airwallet_dom_runner.py`, `airwallet_batch_flow.py` |
| Timestamp / timezone | **giờ máy cục bộ, phần lớn naive, không tz** | nhiều chỗ — xem `missing_evidence.md` §4 |

**Hệ quả:** các tín hiệu mà PHASE 4 cần (HTTP status, business error code, session
error, restriction response) không được pipeline ghi lại ở bất kỳ đâu. Tối đa chỉ
có UI dump và chuỗi thông báo trên màn hình.

## PHASE 2 — Bằng chứng thực có

Trong checkout này: **không có.** `dumps/`, `logs/`, `agents/runtime/`,
HAR, screenshot, transaction/reward record — đều không tồn tại hoặc rỗng (xem
`missing_evidence.md` §2). Dữ liệu vận hành bị `.gitignore` loại trừ, nằm trên
máy vận hành.

Không rút token/cookie/key từ Keychain hay memory; không hook TLS pinning; không
chặn/đổi request của app đang chạy — theo đúng RULES.

## PHASE 3 — Timeline

Không dựng được timeline thực vì không có dữ liệu tài khoản. Đã cung cấp
`account_timeline.csv` làm **template**, với kỷ luật timestamp bắt buộc:

- **event_timestamp** (giờ sự kiện xảy ra) tách khỏi **observed_timestamp** (giờ
  nhìn thấy). Bối cảnh cho biết "nhiều tài khoản nhận thông báo khóa ~06:02" — đó
  là **observed**, không phải thời điểm server đánh dấu.
- **estimated_interval** cho khoảng không chắc.
- Mọi giá trị quy về **JST (+09:00)**, ghi rõ tz gốc của nguồn (dump mặc định giờ
  máy, không tz — có thể lệch 2h nếu máy đặt +07:00 VN).

## PHASE 4 — Phân loại backend vs client

Chỉ một bản ghi, và nó là **E — Insufficient evidence** (xem
`error_classification.csv`, EV-000): không có HTTP response/HAR/log/dump nào về
COIN+ trong checkout để phân loại.

Khi thu thập được M1–M7, dùng template để phân loại từng quan sát. Nhắc lại
nguyên tắc: **server hiển thị trạng thái "bị hạn chế" không chứng minh quy tắc
nội bộ nào đã kích hoạt** — đó chỉ là category A (có hạn chế), không phải bằng
chứng về nguyên nhân.

## PHASE 5 — Correlation

**Không thực hiện được** — không có dữ liệu lịch sử nhiều tài khoản trong repo.

Và nêu rõ: ngay cả khi có dữ liệu, việc tương quan "tài khoản nào bị khóa cùng
lúc, cách thời điểm nhận thưởng bao lâu" để **suy ra ngưỡng phát hiện hoặc cách
né** nằm ngoài phạm vi — RULES của nhiệm vụ cấm, và đó là evasion chứ không phải
incident response. Phân tích tương quan hợp lệ duy nhất là để **trả lời Support /
kiểm tra điều kiện campaign**, cần dữ liệu M3/M6 mà hiện không có.

## Kết luận theo ba mức

| Mức | Nội dung |
| --- | --- |
| **CONFIRMED** | (không có) — không có bằng chứng trực tiếp nào trong checkout này. |
| **SUPPORTED HYPOTHESIS** | Trạng thái hạn chế có nguồn gốc **server-side**, suy từ việc app hiển thị chuỗi thông báo khóa do server phát ra (một quan sát UI, chưa có HTTP response kèm theo). |
| **UNKNOWN** | Nguyên nhân hạn chế; thời điểm server thực sự đánh dấu; liên hệ với campaign/số điện thoại/thiết bị; khả năng khôi phục. Tất cả cần M1–M7 và/hoặc COIN+ Support. |

## Trả lời trực tiếp câu hỏi điều tra

> Bằng chứng hiện có có phù hợp với giả thuyết server-side restriction / post-campaign review không?

Bằng chứng hiện có **không đủ để khẳng định hay bác bỏ** giả thuyết nào. Nó *phù
hợp* với "server-side restriction" ở mức hiển nhiên (app hiển thị thông báo của
server), nhưng *không phân biệt được* "post-campaign review" với các khả năng
khác, vì project không ghi lại gì về phía server. Muốn tiến xa hơn phải thu thập
M1–M7; muốn biết **lý do** thì phải hỏi COIN+ Support.
