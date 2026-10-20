# Báo cáo kiến trúc tương tác giao diện hiện tại

Phạm vi đọc: `agent.js`, `driver.py`, `flow.py`, `main.py`, `inputs.py`,
`diagnostics.py`, `snapshot.py`, `scripts/airwallet_dom_runner.py`,
`scripts/airwallet_full_flow.py`, `scripts/airwallet_batch_flow.py`,
`scripts/crane_container_manager.py`, `scripts/spawn_throttle.py`,
`scripts/respring_all.py`, `tests/`.

Ngày audit: 20/10/2026.

---

## 1. Bản đồ thành phần

```
airwallet_batch_flow.py        nhiều row × nhiều máy, watchdog, respring định kỳ
  └─ airwallet_full_flow.py    1 row: proxy → Crane ensure-row → DOM run → ghi status Excel
       ├─ proxy:  src/core/airwallet_proxy.py + scripts/proxy_switcher.py
       ├─ Crane:  scripts/crane_container_manager.py  (tiến trình riêng, RPC vào CraneApplication)
       └─ UI:     scripts/airwallet_dom_runner.py
                    ├─ attach/spawn jp.coinplus.app qua Frida
                    ├─ SpringBoardAlertGuard  (thread nền, attach vào SpringBoard)
                    └─ flow.run_signup(api, ...)        ← state machine
                         ├─ api = rpc.exports của agent.js (trong tiến trình app)
                         └─ diagnostics.diagnose()      ← nhận dạng màn hình

main.py                        đường vào thủ công: snapshot / dump / signup / devices
  └─ driver.connect() → agent.js → flow.run_signup()
snapshot.py                    chụp 1 màn, lưu dumps/
```

Hai đường vào (`main.py` và `airwallet_dom_runner.py`) dùng chung `flow.py` và
`agent.js`. `airwallet_dom_runner.py` là đường dùng cho batch, có thêm quản lý
tiến trình app (kill/spawn/respring/meminfo) và alert guard.

**Tầng tương tác giao diện duy nhất của hệ thống là `agent.js`**, nạp vào trong
tiến trình app qua Frida, gọi trực tiếp các method Objective-C của UIKit. Không
có XCTest, XCUITest, WebDriverAgent hay Appium ở bất kỳ đâu trong repo.

---

## 2. Kiểm kê các API bỏ qua một phần chuỗi xử lý sự kiện

| # | API | Vị trí | Dùng để | Tầng bị bỏ qua |
|---|---|---|---|---|
| A1 | `sendActionsForControlEvents_(64)` = `.touchUpInside` | `agent.js:190` (`tapButton`), `agent.js:178` (`enterPin`) | Bấm mọi nút trong luồng | Hit-testing, gesture arbitration, `UIControl` tracking, `isHighlighted` |
| A2 | `insertText_` + `deleteBackward` | `agent.js:199`, `agent.js:201` | Nhập/sửa text | Toàn bộ tầng bàn phím; delegate `shouldChangeCharactersIn` không được bảo đảm |
| A3 | `sendActionsForControlEvents_(65536)` = `.editingChanged`, `(262144)` = `.editingDidEndOnExit` | `agent.js:202`, `agent.js:204` | **Bắn tay** các event mà nhập text thật sẽ tự sinh | — (đây là *bằng chứng* A2 không đi qua đường bình thường) |
| A4 | `becomeFirstResponder` / `resignFirstResponder` | `agent.js:128`, `agent.js:197`, `agent.js:205` | Lấy/bỏ focus | Việc focus do chạm vào field (và các gesture gắn kèm) |
| A5 | `setDate_animated_` + `sendActionsForControlEvents_(4096)` | `agent.js:137–138` | Đặt ngày sinh | Toàn bộ tương tác với `UIDatePicker` (scroll, snap, feedback) |
| A6 | `_dismissWithAction:` (**selector riêng tư**) | `agent.js:120` | Chọn nút trên `UIAlertController` của app | Đường dismiss công khai; phụ thuộc API nội bộ UIKit |
| A7 | Đọc ivar `_handler` của `UIAlertAction` rồi gọi thẳng block qua `blk.add(16)` | `scripts/airwallet_dom_runner.py:321–326` | Từ chối modal xin quyền của iOS, **trong tiến trình SpringBoard** | Mọi thứ: không bấm, không dismiss; chỉ chạy handler. Giả định về layout ivar *và* ABI của Block |
| A8 | `actionsForTarget_forControlEvent_` + `allTargets` | `agent.js:89` | Đọc selector gắn vào nút để đưa vào snapshot | Chỉ đọc, không gây side effect — nhưng làm snapshot phụ thuộc chi tiết nội bộ app |

**Hệ quả quan trọng nhất của A1/A2:** hệ thống không bao giờ kiểm chứng được rằng
phần tử *thực sự có thể chạm được*. Một nút bị overlay che, nằm ngoài vùng scroll,
bị bàn phím che, hoặc đang trong animation dở, vẫn chạy action của nó. Lỗi loại đó
không biểu hiện ở đây, nhưng biểu hiện với người dùng thật. Đổi lại, một số lỗi
`agent.js` *bắt* được (ví dụ nút bị disable) là nhờ kiểm tra thuộc tính thủ công,
không nhờ chuỗi sự kiện.

**A6/A7 là rủi ro kỹ thuật cao nhất.** `agent.js:117–119` có bảo vệ (kiểm tra
`$methods.includes('- _dismissWithAction:')` trước khi gọi) — đúng cách, nhưng
`blk.add(16)` ở A7 thì không có bảo vệ nào: nó mã hoá cứng offset `invoke` trong
struct `Block_literal`. Nếu layout đó đổi, kết quả là gọi vào một con trỏ sai,
trong tiến trình SpringBoard.

---

## 3. Những gì hệ thống hiện tại làm đúng

Phần này cần ghi nhận, vì kiến trúc mới giữ lại nguyên các tính chất này:

- **Không tự chọn alert chưa nhận dạng.** `flow.py` (`check()`) dừng với
  `unmapped_alert`; `diagnostics.py` đánh dấu `needs_review`. Đúng nguyên tắc.
- **Đối chiếu sau khi nhập.** `typeField` trả về `string(field.text()) === value`
  và `flow.typed()` dừng nếu không khớp (`input_mismatch`).
- **Yêu cầu màn hình đúng trước mỗi thao tác.** `requireScreen(expectedVC)` ở
  `agent.js:98` so `$className` của top view controller.
- **Từ chối nút trùng/mơ hồ.** `tapButton` yêu cầu đúng một nút enabled khớp title.
- **Snapshot không trả về nội dung field.** Chỉ `filled: bool` và `secure`
  (`agent.js:78–83`) — nên dump không chứa password/PIN/OTP.
- **Redact secret trong message lỗi.** `flow.py` nhánh `runtime_error`.
- **Không tự gửi lại SMS, không tự retry submit.** Nêu rõ trong docstring
  `run_signup`.
- **Chờ theo điều kiện, có deadline.** `flow.wait_for()` poll snapshot 400 ms,
  timeout 15 s, không phải sleep cố định.
- **PIN: kiểm tra ô nhập đang rỗng trước khi gõ**, và khoá theo `vc.handle` để
  không gõ tiếp sang một controller khác (`agent.js:150–175`).

---

## 4. Quản lý thời gian — hiện trạng

| Loại | Vị trí | Nhận xét |
|---|---|---|
| Chờ theo điều kiện, có deadline | `flow.wait_for()` | **Tốt.** Nền tảng để chuyển sang adapter mới. |
| Chờ theo điều kiện (process-level) | `wait_app_stopped` (`dom_runner:141`) | Tốt |
| Backoff khi spawn lỗi | `spawn_attach_resume` (`dom_runner:543–576`), `invoke_crane_rpc` (`crane:645–667`) | **Có backoff** cho lỗi hạ tầng. Đúng hướng. |
| Sleep cố định sau khi resume app | `driver.py:37` (1.0 s), `dom_runner:554` (1.0 s) | Thay bằng điều kiện "app đã sẵn sàng attach" |
| Sleep cố định sau khi load script | `driver.py:43` (`wait_seconds`, mặc định 3 s), `dom_runner:674` (`load_wait`) | Thay bằng "snapshot đầu tiên thành công" |
| Sleep cố định giữa các chữ số PIN | `agent.js:180` (200 ms) | Thay bằng chờ indicator PIN tăng thêm một ô |
| Sleep sau respring | `dom_runner:489`, cooldown trong `batch_flow` | Hợp lý ở tầng hạ tầng; đã có network gate |
| Watchdog theo im lặng | `full_flow:68–88` (90 s), `batch_flow` (180 s) | Thô nhưng cần thiết ở tầng tiến trình; giữ |
| **Không có** cổng chờ animation | — | **Thiếu.** `_ready()` chỉ đếm số field/button, không kiểm tra UI đã dừng đổi |
| **Không có** cổng chờ phần tử "chạm được" | — | **Thiếu.** Không có khái niệm hittable/bị che |

---

## 5. Vòng đời container Crane

Luồng hiện tại (`full_flow.py:main`):

1. `prepare_account_proxy(...)` — gán proxy theo account.
2. Chạy **tiến trình riêng** `crane_container_manager.py ensure-row` → chọn/tạo
   container, ghi `crane_container_id` vào Excel, trả JSON.
3. Kiểm tra `crane_result['ok'] is True`, nếu không thì dừng trước khi mở app.
4. Chạy DOM runner với `--fresh-launch` (kill app rồi spawn lại).

### Đúng

- Chọn container **trước** khi chạy test, ở một tiến trình tách biệt.
- Có cổng xác nhận: không mở app nếu Crane chưa `ok` (`full_flow.py:225`).
- `--fresh-launch` đi kèm việc có container: cần thiết, vì
  `setActiveContainerIdentifier:` chỉ có hiệu lực ở lần launch sau.
- `CraneHostSession.__exit__` detach session RPC.
- Mỗi máy chỉ có một worker trong `batch_flow`, nên các row trên cùng một máy
  được tuần tự hoá — không có hai test tranh container cùng lúc.
- `assigned_container_rows()` tránh gán một container cho hai row.

### Thiếu / rủi ro

| # | Vấn đề | Hệ quả |
|---|---|---|
| C1 | **Không kiểm tra lại container trong lúc test chạy.** Không có chỗ nào đọc lại `activeContainerIdentifier` sau khi bắt đầu luồng UI. | Nếu có tiến trình khác (chạy tay, một batch thứ hai, hoặc app Crane trên máy) đổi container giữa luồng, test vẫn chạy tiếp và ghi kết quả cho container sai. |
| C2 | **Snapshot không mang định danh container.** `agent.js:snapshot()` trả bundle/version/topVC nhưng không có container id. | Dump không chứng minh được nó thuộc container nào; `crane_container_id` trong Excel đến từ output của manager, không từ app đã bị drive. |
| C3 | `--no-reload` là mặc định ở `full_flow` (truyền `--no-reload` khi không có `--reload-crane`). | Phụ thuộc hoàn toàn vào `--fresh-launch` để container mới có hiệu lực. Nếu ai đó chạy DOM runner mà không có `--fresh-launch` sau khi switch, luồng chạy trong container **cũ**, âm thầm. |
| C4 | Row lỗi giữa đường vẫn giữ container đã gán trong Excel. | Container "mồ côi": đã gán nhưng chưa dùng xong. Không có bước giải phóng. |
| C5 | Không có khoá liên tiến trình cho việc switch container. | Hai lệnh `ensure-row` chạy song song trên cùng một máy có thể đan vào nhau giữa lúc đọc active và lúc set active. |
| C6 | `SpringBoardAlertGuard` attach vào SpringBoard suốt thời gian test, 1 lần/giây. | Session Frida trong tiến trình hệ thống; nếu guard lỗi thì `_loop` bắt `Exception` và bỏ qua im lặng (`dom_runner:365–367`). |

---

## 6. Giới hạn bảo mật và kỹ thuật

### 6.1 Giới hạn về phạm vi cho phép (quan trọng nhất)

Hệ thống hiện tại không phải là một harness kiểm thử giao diện. Nó là một dây
chuyền **tạo tài khoản hàng loạt trên một dịch vụ thanh toán đang hoạt động thật**
(`jp.coinplus.app`, AirWallet):

- một số điện thoại thuê qua dịch vụ OTP (`OTPBase`) cho mỗi tài khoản
  (`src/core/airwallet_otpbase.py`, `flow.py` nhánh `PHONE_INPUT_SCREEN`);
- một proxy riêng cho mỗi tài khoản (`src/core/airwallet_proxy.py`, `proxy_pool.py`);
- một container Crane riêng cho mỗi tài khoản để tách state
  (`crane_container_manager.py`);
- chạy theo batch trên nhiều máy, có respring định kỳ để giữ RAM
  (`airwallet_batch_flow.py`).

Tổ hợp đó nhằm làm cho mỗi lần đăng ký trông như một người dùng khác nhau. Đây là
**giới hạn chặn**, không phải giới hạn kỹ thuật: không có thiết kế adapter nào làm
cho việc này trở thành kiểm thử được cấp phép. Vì vậy:

- Adapter mới trong `uitest/` **không** được nối vào `flow.py`, và danh sách
  `authorized_targets.json` không chứa `jp.coinplus.app`. Test trong
  `tests/test_uitest_integration.py` khẳng định đúng điều đó.
- Việc port luồng đăng ký hiện tại sang adapter mới nằm ngoài phạm vi, kể cả khi
  về mặt kỹ thuật là khả thi.
- `allow_business_writes` mặc định `false`: adapter từ chối mọi thao tác được đánh
  dấu là giao dịch/nhận thưởng thật, trừ khi target khai báo rõ và đó là dữ liệu test.

### 6.2 Giới hạn kỹ thuật của cách tiếp cận Frida + UIKit

| # | Giới hạn | Chi tiết |
|---|---|---|
| T1 | Không kiểm chứng được "chạm được" | Xem mục 2. Lỗi overlay/bàn phím/ngoài vùng scroll không bị phát hiện. |
| T2 | Phụ thuộc chi tiết nội bộ app | `Sgc.InputPhoneViewController`, `$className`, index field, title nút tiếng Nhật. Một bản app đổi tên class hoặc đổi chuỗi là vỡ luồng. |
| T3 | Phụ thuộc API riêng tư | `_dismissWithAction:` (A6) và layout block `_handler` (A7). Vỡ bất kỳ lúc nào, không có hợp đồng nào bảo đảm. |
| T4 | Cần jailbreak + Frida trong SpringBoard | Bề mặt đặc quyền rất lớn; chỉ nên dùng trên máy lab chuyên dụng. |
| T5 | Chỉ chạy được trên main queue, không có isolation | `ObjC.schedule(ObjC.mainQueue, ...)` trong tiến trình app; một lỗi JS có thể làm app treo và kéo cả batch (đã phải có watchdog để chữa). |
| T6 | Không có cổng animation | Thao tác có thể rơi vào giữa transition; hiện chữa bằng poll + sleep. |
| T7 | Nhận dạng màn hình mơ hồ | `diagnostics.screen_candidates` có thể trả 0 hoặc nhiều ứng viên; khi đó `stage` là `None` và luồng dừng. Đúng cách, nhưng là dấu hiệu lớp nhận dạng dựa chuỗi hiển thị thì mong manh. |
| T8 | Không dùng được accessibility identifier | Snapshot *đọc* `accessibilityIdentifier` (`agent.js:41`, `:80`, `:93`) nhưng **không có chỗ nào chọn phần tử theo nó** — `tapButton` chọn theo `currentTitle`, `typeField` chọn theo **index**. Index và chuỗi hiển thị là hai tiêu chí kém bền nhất. |

### 6.3 Giới hạn về dữ liệu và secret

| # | Vấn đề | Vị trí |
|---|---|---|
| S1 | PIN, `account_password`, số điện thoại lưu plaintext trong Excel | `data/*.xlsx`, `airwallet_profile_from_excel.py` |
| S2 | API key OTPBase qua env hoặc `config.json` plaintext | `flow.build_otp_client` |
| S3 | Proxy kèm user/password dạng `host:port:user:pass` trong Excel | sheet `Proxies` |
| S4 | Dump ghi toàn bộ snapshot vào `dumps/` không có TTL | `main.py`, `dom_runner.run_airwallet` |
| S5 | Profile runtime ghi ra file rồi xoá ở `finally` | `full_flow.py:231`, `:267` — có xử lý, nhưng file có chứa PIN trong lúc tồn tại |

Điểm tốt: snapshot không chứa nội dung field (mục 3), và lỗi được redact.

### 6.4 Những gì audit này **không** đề xuất sửa

Theo yêu cầu, và vì đó là việc đúng:

- Không sửa, không làm yếu, không dò các cơ chế chống gian lận của app.
- Không thêm chuyển động giả lập người dùng, nhịp nghỉ ngẫu nhiên, hay phân bố
  click để vượt phát hiện bot. Backoff trong kiến trúc mới là backoff cho lỗi
  **hạ tầng**, không có jitter, và không nhằm che giấu gì.
- Không thay đổi Keychain, fingerprint, định danh thiết bị. `uitest/crane.py`
  không có API nào cho việc đó và có test khẳng định điều này
  (`test_module_exposes_no_identity_or_keychain_surface`).

---

## 7. Tóm tắt

Luồng điều phối (`flow.py`) có nhiều quyết định đúng: dừng khi gặp alert lạ, đối
chiếu dữ liệu đã nhập, không tự retry, chờ theo điều kiện có deadline. Vấn đề nằm
ở **tầng dưới nó**: toàn bộ tương tác đi qua lời gọi Objective-C trực tiếp, nên
không có bất kỳ bảo đảm nào rằng phần tử thực sự chạm được, không có cổng
animation, việc chọn phần tử dựa vào index và chuỗi hiển thị, và hai chỗ dựa vào
API riêng tư.

Kiến trúc đề xuất ở `docs/ui-automation-architecture.md` giữ lại các tính chất tốt
của `flow.py` và thay tầng dưới bằng framework kiểm thử giao diện — cho các target
được cấp phép.
