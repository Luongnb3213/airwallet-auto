# Báo cáo sửa lỗi: bảo mật dữ liệu, trạng thái container, chẩn đoán

Ngày: 20/10/2026 · Diff đầy đủ: `audit/CHANGES.diff` (+325 −64, 17 file)
Bằng chứng rò rỉ secret: `audit/SECRET_EXPOSURE_REPORT.md` (lập **trước** khi sửa)

Phạm vi tự giới hạn: chỉ các module phục vụ ổn định, chẩn đoán và bảo mật dữ liệu.
Không sửa logic luồng đăng ký, nhận thưởng hay giao dịch. Không thêm mô phỏng
người dùng, không che giấu Frida/jailbreak/proxy. Không chạy trên tài khoản thật.

---

## 1. Các lỗi đã sửa

### F1 — PIN plaintext sót lại trên đĩa sau khi watchdog SIGKILL `[CONFIRMED]`

**Nguyên nhân.** `airwallet_full_flow.py` ghi toàn bộ profile (gồm `pin`,
`account_password`) ra `agents/runtime/input_<device>_<row>_<pid>.json`, và chỉ
xoá trong `finally`. Nhưng `airwallet_batch_flow.py:528–537` diệt row treo bằng
`os.killpg(..., SIGKILL)` — **SIGKILL không bắt được**, nên `finally` không bao
giờ chạy. Mỗi row bị watchdog diệt để lại một file PIN plaintext vô thời hạn, với
quyền mặc định (đọc được bởi mọi account trên máy).

**Phát hiện then chốt:** file đó *không cần thiết*. `full_flow` gọi
`run_with_frida(...)` **trong cùng tiến trình**, và hàm đó lại `load_profile()`
đọc chính file vừa ghi. Secret ra đĩa chỉ để đọc lại ngay.

**Sửa.** Truyền profile trong bộ nhớ, bỏ hẳn việc ghi file.

| File | Thay đổi |
| --- | --- |
| `scripts/airwallet_full_flow.py:223–239` | Bỏ `runtime_profile` và lệnh ghi; đặt `parsed_dom_args.profile_data = profile` |
| `scripts/airwallet_full_flow.py:262–264` | Bỏ `finally` xoá file (không còn file) |
| `scripts/airwallet_full_flow.py:185–191` | Thêm `reap_stale(...)` lúc khởi động để thu hồi file sót từ bản cũ |
| `scripts/airwallet_dom_runner.py:619–625` | `run_with_frida` ưu tiên `args.profile_data`, chỉ `load_profile()` khi không có |
| `src/utils/secret_files.py` (mới) | `write_private_json`, `restrict_to_owner`, `is_owner_only`, `reap_stale` |

Lưu ý: đây là fix *loại bỏ* nguyên nhân, không phải giảm thiểu. Không còn file thì
SIGKILL không còn gì để bỏ sót.

### F2 — Thiếu redaction ở hai exception handler ngoài cùng `[CONFIRMED]`

**Nguyên nhân.** `flow.py:299–302` và `airwallet_full_flow.py:253–257` đã redact
`account_password`/`password`/`pin`/`otp`. Nhưng `main.py` và
`airwallet_dom_runner.py:main` in thẳng `str(error)`. Hai handler đó bắt mọi
exception phát sinh *ngoài* `run_signup` (chuẩn bị proxy, tạo OTP client, đọc
Excel), và exception của thư viện bên thứ ba thường nhắc lại input — một lỗi HTTP
có thể in URL kèm API key, openpyxl có thể in nội dung ô. Các dòng này đi vào
stderr rồi được `airwallet_batch_flow` thu vào log.

**Sửa.**

| File | Thay đổi |
| --- | --- |
| `src/utils/redaction.py` (mới) | `Redactor` + `redact()`: thu secret từ profile/settings/env, thay thế dài-trước-ngắn, không bao giờ raise |
| `main.py:48–52` | Hoist `config = {}` / `profile = None` ra trước `try`, để handler luôn truy cập được |
| `main.py:113–117` | `redact(str(error), profile=profile, settings=config)` |
| `scripts/airwallet_dom_runner.py:752–759` | `redact(str(exc), profile=args.profile_data)` |

Thiết kế có ý: **fail closed**. Thà redact quá tay (PIN 4 số có thể trùng chuỗi
khác) còn hơn lọt secret.

### F3 — File profile thường trú không giới hạn quyền `[CONFIRMED]`

**Nguyên nhân.** `airwallet_profile_from_excel.py:197–199` ghi profile (PIN,
`account_password`) bằng `write_text()` — quyền mặc định. Đây là file **thường
trú**, không phải tạm: nó là default `--profile-json` của `airwallet_dom_runner`,
nên PIN nằm trên đĩa đến khi có người xoá.

**Sửa.** `scripts/airwallet_profile_from_excel.py:197–216` dùng
`write_private_json`, rồi `is_owner_only()` kiểm tra lại và in cảnh báo nêu rõ
file chứa gì và quyền đã siết được hay chưa.

Trên Windows `os.chmod` chỉ bật/tắt read-only, nên `restrict_to_owner()` dùng
`icacls /inheritance:r /grant:r <user>:(R,W)`. **Đã kiểm chứng thực tế trên máy
này:** sau khi ghi, `icacls` chỉ còn một ACE `PC\admin:(R,W)`; test
`test_owner_only_check_notified_a_loosened_acl` cấp thêm `Everyone:(R)` và xác
nhận `is_owner_only()` phát hiện được — tức là phép kiểm tra có khả năng *fail*,
không phải luôn trả True.

### F4 — Race condition khi gán container Crane `[CONFIRMED]`

**Nguyên nhân.** Chọn container là một chuỗi read-decide-write trên workbook:

1. `crane_container_manager.assigned_container_rows()` đọc container mà các row
   khác đang giữ — **không có lock**;
2. `ensure_row()` chọn một container "còn trống";
3. `write_crane_assignment()` ghi — *chỉ bước này* có `excel_write_lock`.

`airwallet_batch_flow.py:696` tạo một thread cho mỗi thiết bị, mỗi thread spawn
`airwallet_full_flow`, và nó spawn `crane_container_manager.py ensure-row`
(`airwallet_full_flow.py:221`). Với N thiết bị là N tiến trình `ensure-row` chạy
song song trên cùng workbook. Hai tiến trình có thể cùng đọc "container X còn
trống", cùng chọn X, rồi cùng ghi → **hai tài khoản dùng chung một container**,
tức state ứng dụng bị trộn.

**Sửa.** `scripts/crane_container_manager.py:845–930` thêm
`container_assignment_lock()` — mutex liên tiến trình trên một **lock file
riêng**, và `ensure_row()` trở thành wrapper mỏng gọi `_ensure_row_locked()` bên
trong lock.

Hai chi tiết quan trọng:

- **Không dùng lại `excel_write_lock`.** Lock đó không reentrant: trên POSIX
  `fcntl.flock` từ fd thứ hai trong cùng tiến trình sẽ deadlock, và
  `write_crane_assignment` tự lấy lock đó khi ta đang giữ lock ngoài. Nên dùng
  file `.<name>.crane-assign.lock` tách biệt. Có test khẳng định lồng được.
- Lock giữ qua cả Crane RPC, nên việc gán bị tuần tự hoá giữa các thiết bị. Chấp
  nhận được vì `crane_slots_default()` đã giới hạn 2 phiên Crane host đồng thời.
  Nếu latency thành vấn đề, bước tiếp theo là claim container vào Excel *trước*
  RPC rồi nhả lock sớm (đã ghi trong docstring).

### F5 — Guard modal iOS chết âm thầm `[CONFIRMED]`

**Nguyên nhân.** `SpringBoardAlertGuard._loop` (`airwallet_dom_runner.py:362–367`)
bắt mọi exception rồi `pass`. Nếu phiên Frida vào SpringBoard chết, guard ngừng
từ chối modal xin quyền nhưng vòng chạy vẫn tiếp tục như thể đang được bảo vệ, và
dump không ghi lại gì — đúng mục "log bị thiếu khiến không thể xác minh kết quả".

**Sửa.**

| File | Thay đổi |
| --- | --- |
| `scripts/airwallet_dom_runner.py:344–350` | Thêm `failures`, `max_failures=5`, `degraded`, `last_error` |
| `scripts/airwallet_dom_runner.py:369–390` | Đếm lỗi liên tiếp, log lần đầu, dừng loop và đặt `degraded` sau 5 lỗi, reset khi sweep thành công |
| `scripts/airwallet_dom_runner.py:690–706` | `finish()` gắn `alert_guard: {degraded, failures, tapped, last_error}` vào result, nên dump giữ được bằng chứng |

### F6 — Snapshot không chứng minh được thuộc container nào `[CONFIRMED]`

**Nguyên nhân.** Phát hiện C2 trong `docs/ui-automation-audit.md`: snapshot không
mang định danh container, nên dump không chứng minh được nó thuộc container nào;
`crane_container_id` trong Excel đến từ output của manager, không từ app bị drive.

**Sửa.** Không chỉ *ghi* mà **đối chiếu** hai nguồn độc lập.

| File | Thay đổi |
| --- | --- |
| `uitest/crane_rpc.py` (mới) | `CraneRpcManager`: hiện thực `ContainerManager` trên `CraneHostSession`; mở session lazy và tái dùng; phân loại lỗi transport → `InfrastructureError` vs Crane không quản lý app → `ContainerNotReadyError`; `detach()` idempotent |
| `uitest/backends/wda.py:62–90` | Nhận `container_id_provider`, đọc một lần rồi cache |
| `uitest/backends/wda.py:146–163` | Mỗi `Snapshot` mang `container_id` |
| `uitest/adapter.py:331–352` | `_verify_container_stamp()`: stamp của snapshot phải khớp container mà session đang bind, lệch → `ContainerChangedError` |
| `uitest/adapter.py:64–75, 572` | `ActionRecord.container_id`, nên báo cáo run có container cho từng thao tác |

Stamp rỗng (không wire provider) **không** tính là lệch — không có bằng chứng thì
chấp nhận được, bằng chứng *sai* thì không.

### F7 — `driver.connect()` để lại app đã spawn khi attach thất bại `[CONFIRMED]`

**Nguyên nhân.** `driver.py:29-30` dùng **một** biến `suspended` cho hai nghĩa
khác nhau: "process đang bị suspend" và "chính ta đã spawn nó". Sau
`device.resume(pid)` thì `suspended = False`, nên thông tin "ta đã spawn" biến
mất. Nếu `device.attach()` hoặc `script.load()` lỗi sau đó, handler chỉ
`session.detach()` và **không kill pid** — app ta vừa mở nằm lại trên thiết bị,
không ai sở hữu.

Thêm một lỗi thứ hai trong cùng handler: `try: device.resume(pid) finally: ...`
— nếu `resume` lỗi lần nữa thì exception đó **thay thế** exception gốc, che mất
nguyên nhân thật.

**Sửa** (`driver.py:29-74`):

- Tách `spawned_here` khỏi `suspended`, để quyền sở hữu không bị mất sau resume.
- Trên đường lỗi: detach session (nếu có), rồi `device.kill(pid)` **chỉ khi** ta
  spawn nó. App đang chạy từ trước không bao giờ bị kill — nó không thuộc về ta.
- Mỗi bước dọn được bọc riêng, nên lỗi khi dọn không che exception gốc.
- Bỏ `device.resume()` trong handler: kill đã bao trùm cả trường hợp còn suspend,
  và `resume` vẫn chỉ được gọi đúng một lần (test sẵn có khẳng định điều này).

Cách làm này khớp với quy ước đã có trong repo: `spawn_attach_resume`
(`airwallet_dom_runner.py:543-576`) cũng kill pid khi thất bại.

### F8 — Leak file descriptor trong `spawn_throttle` `[CONFIRMED]`

**Nguyên nhân.** `spawn_throttle.py:98-104` mở descriptor rồi gọi `_try_lock`,
và chỉ bắt `OSError` để đóng. `msvcrt.locking` và `fcntl.flock` có thể raise
ngoài `OSError` (`ValueError` khi fd không hợp lệ, hoặc `KeyboardInterrupt` giữa
lời gọi). Khi đó descriptor leak suốt đời tiến trình — và vì hàm này chạy mỗi
lần spawn, leak tích luỹ theo số row.

**Sửa** (`scripts/spawn_throttle.py:98-115`): tách ba nhánh — `OSError` nghĩa là
slot đang bị giữ (đóng rồi thử slot kế), `BaseException` nghĩa là lỗi bất thường
(đóng rồi để exception bay lên), `else` mới nhận slot.

### F9 — `finally` dọn tài nguyên không độc lập `[CONFIRMED]`

**Nguyên nhân.** `main.py:119-123` và `airwallet_dom_runner.py:729-732` gọi các
bước dọn liền nhau không bọc:

```python
if guard is not None:
    guard.close()
if session is not None:
    session.detach()
```

`guard.close()` raise thì `session.detach()` bị bỏ qua → leak phiên Frida vào
tiến trình app. Hiện `close()` có try/except nội bộ nên khó raise, nhưng đó là
sự trùng hợp, không phải bảo đảm.

**Sửa.** Bọc từng bước riêng ở cả hai chỗ, và **báo lỗi ra stderr** thay vì nuốt
— đúng cái sai mà F5 đã chỉ ra ở guard modal.

| File | Thay đổi |
| --- | --- |
| `main.py:118-131` | Vòng lặp qua từng bước dọn, mỗi bước try/except riêng, in `Không dọn được <bước>` |
| `scripts/airwallet_dom_runner.py:729-743` | Cùng cách, cho `guard.close()` và `session.detach()` |

Lưu ý: yêu cầu chỉ nêu `main.py`, nhưng `run_with_frida` có **đúng** lỗi đó và là
đường mà batch dùng, nên sửa cả hai.

### F10 — Năm module test rơi khỏi suite trên máy không có `frida` `[CONFIRMED]`

**Nguyên nhân.** `driver.py` và các script trong `scripts/` import `frida` ở
module scope. `frida` là package native, chỉ cần khi điều khiển iPhone thật. Trên
máy không có nó, năm module test **không import được**:
`test_wrapper`, `test_launch_order`, `test_device_assignment`, `test_proxy`,
`test_respring` — tức 50 test, trong đó có hai module phủ đúng
`airwallet_dom_runner` và `airwallet_full_flow`. `python -m unittest discover -s
tests` (lệnh ghi trong README) báo lỗi import rồi bỏ qua chúng, nên suite âm thầm
teo lại còn những module tình cờ không chạm Frida. Đó là lý do tôi phải chạy
chúng qua một runner ngoài repo ở các bước trước.

**Sửa.**

| File | Thay đổi |
| --- | --- |
| `tests/frida_stub.py` (mới) | `install_frida_stub()` đăng ký `frida`/`frida_tools` rỗng vào `sys.modules` khi thiếu package thật; thêm `real_frida_available()` và `using_stub()` |
| 5 module test cần frida | Thêm 4 dòng bootstrap **trước** các import project (vì chính các import đó mới kéo frida vào) |
| `tests/test_credential_hygiene.py`, `tests/test_alert_guard_diagnostics.py`, `tests/test_resource_cleanup.py` | Bỏ ba bản stub inline tôi viết ở bước trước, dùng stub chung |
| `tests/test_frida_stub.py` (mới) | 10 test cho chính stub |
| `README.md` | Ghi rõ không cần cài `frida` để chạy test |

Hai tính chất được thiết kế có ý, và đều có test:

- **No-op khi có `frida` thật.** Trên máy vận hành, test chạy với package thật;
  stub không bao giờ che nó (`test_does_nothing_when_a_real_frida_is_present`).
- **Inert và ồn ào.** Stub không liệt kê thiết bị nào, và mọi
  `get_device`/`spawn`/`attach`/`kill` raise `RuntimeError` nói rõ đây là stub.
  Nó **không** giả làm thiết bị hoạt động, nên không thể làm một test pass trong
  khi lẽ ra fail với Frida thật. `frida_tools.__file__` trỏ tới đường dẫn không
  tồn tại, nên `driver.load_agent_source()` lỗi to thay vì nạp agent giả —
  `test_frida_tools_path_does_not_resolve_to_a_fake_agent` khẳng định điều này.
  Test nào cần hành vi thiết bị thì tự mock, và cả 5 module vốn đã làm vậy.

`test_every_frida_dependent_test_module_installs_the_stub` canh phần wiring: một
module quên bootstrap sẽ lại rơi khỏi suite trong im lặng, nên nó được kiểm tra
bằng test chứ không bằng trí nhớ.

---

## 2. Kết quả test

**257/257 PASS**, bằng **một lệnh duy nhất**, không cần `frida`.

| Nhóm | Số test | Kết quả |
| --- | --- | --- |
| Test sẵn có, chạy trực tiếp (`test_diagnostics`, `test_excel_replace`, `test_line_coin_data`, `test_otpbase`, `test_permission_alerts`, `test_signup_flow`) | 51 | PASS |
| `uitest/` từ phiên trước (`waits`, `adapter`, `crane_session`, `integration`) | 89 | PASS |
| **Mới:** `test_uitest_crane_rpc` | 19 | PASS |
| **Mới:** `test_credential_hygiene` | 15 | PASS |
| **Mới:** `test_crane_assignment_race` | 6 | PASS |
| **Mới:** `test_alert_guard_diagnostics` | 4 | PASS |
| **Mới:** `test_resource_cleanup` | 13 | PASS |
| **Mới:** `test_frida_stub` | 10 | PASS |
| Test sẵn có từng bị chặn vì `frida` (`test_wrapper`, `test_launch_order`, `test_device_assignment`, `test_proxy`, `test_respring`) | 50 | PASS (nay chạy trong suite, nhờ F10) |

```bash
python -X utf8 -m unittest discover -s tests
```

Trước F10 lệnh này bỏ qua 5 module vì lỗi import; giờ nó chạy hết 257 test.

### Hai test đáng chú ý

- `test_unserialised_selection_can_double_assign` — **tái hiện lỗi F4**: cùng
  logic chọn container, bỏ lock, hai thread cùng chọn `c1`. Test này khẳng định
  bug là thật, không phải suy đoán.
- `test_serialised_selection_does_not_hand_one_container_to_two_rows` — cùng kịch
  bản, có lock: mỗi row được một container riêng.

### Kiểm chứng test không rỗng (mutation check)

Một test pass trước code đã sai thì không chứng minh gì. Tôi đã lần lượt hoàn
nguyên từng fix F7/F8/F9 rồi chạy lại class test tương ứng:

| Fix hoàn nguyên | Kết quả |
| --- | --- |
| F7 (bỏ `device.kill`) | `SpawnedProcessCleanupTests` FAILED (3 failures) |
| F8 (bỏ nhánh `BaseException`) | `SlotDescriptorTests` FAILED (1 failure, 1 error) |
| F9 (trả lại `finally` cũ của `main.py`) | `FinallyOrderingTests` FAILED (1 failure, 1 error) |

Khôi phục code → OK. Trong quá trình này tôi cũng phát hiện và **loại bỏ một test
rỗng**: `test_main_detaches_the_session_even_if_the_guard_raises` luôn pass vì
`guard` không bao giờ khác `None` trên đường `snapshot`, nên nó không kiểm tra gì.
Thay bằng hai test thật sự đi qua `finally` của `main.py`.

### Về `frida`

`frida` không có trên máy này. Trước F10 điều đó khiến 5 module test không import
được và tôi phải chạy chúng qua một runner ngoài repo. Nay `tests/frida_stub.py`
xử lý việc đó trong suite, nên toàn bộ 257 test chạy bằng một lệnh. Trên máy có
`frida` thật, stub tự tắt và test chạy với package thật.

---

## 3. Việc chưa xử lý

### Chưa sửa, có bằng chứng, mức thấp

(N1, N2, N3 đã được sửa — xem F7, F8, F9.)

| ID | Vấn đề | Vì sao chưa sửa |
| --- | --- | --- |
| N4 | `agents/runtime/spawn_slots/slot_*.lock` tích luỹ (tên cố định, tái dùng) | Không phải leak thật — file được tái dùng, không sinh thêm |

### Chỉ người vận hành làm được

| ID | Việc |
| --- | --- |
| U3 | Trên máy vận hành: `ls -la agents/runtime/` — nếu còn `input_*.json` thì đó là PIN plaintext sót từ trước khi có F1. Ghi nhận rồi xoá. `reap_stale` sẽ tự dọn các file >1h ở lần chạy `full_flow` kế tiếp. |
| U1 | Nếu có bản copy project nằm trong git: `git log --all -- config.json data/ agents/runtime/ dumps/`. Nếu từng commit, coi secret là **đã lộ** và thay toàn bộ. |
| S1 | Chuyển API key OTPBase sang chỉ dùng environment variable (đã hỗ trợ), bỏ khỏi `config.json`. |
| S2/S3 | Workbook chứa PIN và credential proxy plaintext: BitLocker + siết ACL thư mục `data/`, không để trong thư mục đồng bộ cloud. Mọi `pin`/`account_password` đã nằm plaintext nên coi như đã lộ ở mức "ai đọc được ổ đĩa". |
| — | Đặt TTL cho `dumps/`: không chứa credential, nhưng có chữ hiển thị trên màn (tên kana) — dữ liệu cá nhân. |

### Chưa kiểm chứng trên thiết bị

Phải nói rõ: **`CraneRpcManager` và `container_assignment_lock` chưa chạy lần nào
với Crane/Frida thật trong repo này.** Test dùng double và thread. Cụ thể còn phải
xác nhận trên thiết bị:

1. `CraneHostSession` giữ mở suốt một lượt test có ảnh hưởng app dưới test hay
   không (hiện Crane host bị spawn rồi kill mỗi lần gọi; nay giữ mở).
2. Độ trễ thật khi tuần tự hoá việc gán container qua N thiết bị.
3. `container_id_provider` lấy từ Crane có khớp container mà app thực sự đang
   chạy trong đó hay không — đây là giả định cốt lõi của F6.
4. `WebDriverAgentBackend` vẫn chưa chạy với thiết bị nào (xem mục 8
   `docs/ui-automation-architecture.md`).
5. **F7**: `device.kill(pid)` trên một process vừa spawn và còn suspend — Frida
   xử lý được trên lý thuyết, nhưng chưa xác nhận trên iPhone jailbreak thật.
   Nếu kill thất bại thì lỗi bị bỏ qua (có chủ ý) và app vẫn nằm lại; khi đó
   `--fresh-launch` ở lượt kế sẽ kill nó.

Unit test PASS **không** chứng minh các điểm trên.

---

## 4. Không làm, theo đúng yêu cầu

- Không sửa logic luồng đăng ký, nhận thưởng, giao dịch. `flow.py` không bị thay
  đổi dòng nào (redaction sẵn có của nó giữ nguyên; chỉ thêm redaction ở nơi
  *thiếu*).
- Không thêm mô phỏng hành vi người dùng, nhịp nghỉ ngẫu nhiên hay phân bố click.
  Backoff trong `uitest/waits.py` là cho lỗi **hạ tầng**, không jitter.
- Không che giấu Frida, jailbreak, proxy hay định danh thiết bị. Không sửa
  Keychain/fingerprint — `uitest/crane.py` không có API nào như vậy và có test
  quét code khẳng định.
- Không tạo hay thử trên tài khoản COIN+ nào.
- Không tự thay credential nào.
- Line endings: 5 file project vốn là LF bị tôi đổi thành CRLF trong lúc sửa; đã
  chuẩn hoá lại LF cho toàn bộ file đã tạo/sửa, theo `.gitattributes`
  (`*.py text eol=lf`).
