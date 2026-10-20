# Release readiness — bản kiểm thử

Ngày: 20/10/2026 · Môi trường chạy: Windows 10, Python 3.12.10, **không có `frida`**
Tài liệu liên quan: [`FIX_REPORT.md`](FIX_REPORT.md) (F1–F10),
[`SECRET_EXPOSURE_REPORT.md`](SECRET_EXPOSURE_REPORT.md), [`CHANGES.diff`](CHANGES.diff)

> **Chưa commit.** Repo hiện tại **không phải git repository** (`git rev-parse`
> báo `not a git repository`, không có `.git/`). Không có commit nào được tạo,
> theo đúng yêu cầu: chỉ commit sau khi người vận hành review và xác nhận.

---

## 1. Kết luận một dòng

Toàn bộ 270 test PASS ở chế độ **MOCK**, 11/11 mutation bị phát hiện, và các
kiểm tra release (whitespace, compile, line endings) sạch. **Chưa có một dòng
nào được kiểm chứng trên iPhone thật** — xem mục 6 và 8.

---

## 2. Danh sách file thay đổi

### 2.1 File project có sẵn, đã sửa

| File | Thay đổi | Fix |
| --- | --- | --- |
| `driver.py` | +18 −6 | F7 — kill app đã tự spawn khi attach/load lỗi; tách `spawned_here` khỏi `suspended`; cleanup không che exception gốc |
| `main.py` | +21 −6 | F2 — redaction ở handler ngoài cùng; F9 — bọc riêng từng bước dọn trong `finally` |
| `scripts/spawn_throttle.py` | +10 −2 | F8 — đóng descriptor khi lock raise ngoài `OSError` |
| `scripts/airwallet_dom_runner.py` | +64 −8 | F1 — nhận profile trong bộ nhớ; F2 — redaction; F5 — guard modal báo `degraded`, gắn `alert_guard` vào result; F9 |
| `scripts/airwallet_full_flow.py` | +14 −8 | F1 — bỏ hẳn việc ghi PIN ra đĩa; `reap_stale` dọn file sót |
| `scripts/airwallet_profile_from_excel.py` | +21 −2 | F3 — ghi profile với quyền chỉ chủ sở hữu + cảnh báo |
| `scripts/crane_container_manager.py` | +85 −0 | F4 — `container_assignment_lock`, `ensure_row` thành wrapper có lock |
| `README.md` | — | Ghi mục `uitest/` và mục không cần `frida` để test |
| `tests/test_wrapper.py` | +4 | F10 — bootstrap frida stub |
| `tests/test_launch_order.py` | +4 | F10 |
| `tests/test_device_assignment.py` | +4 | F10 |
| `tests/test_proxy.py` | +4 | F10 |
| `tests/test_respring.py` | +4 | F10 |

### 2.2 File tạo mới trong session này

| File | Dòng | Nội dung |
| --- | --- | --- |
| `src/utils/redaction.py` | 117 | `Redactor`/`redact()` — xoá secret khỏi text trước khi in |
| `src/utils/secret_files.py` | 158 | `write_private_json`, `restrict_to_owner`, `is_owner_only`, `reap_stale` |
| `uitest/crane_rpc.py` | 158 | `CraneRpcManager` — nối `ContainerManager` vào Crane RPC thật |
| `uitest/` (package) | ~1 530 | Adapter kiểm thử UI qua XCUITest/WDA (turn trước); `adapter.py` +26, `backends/wda.py` +34 trong turn này cho F6 |
| `uitest/authorized_targets.example.json` | 28 | Allow-list target được cấp phép |
| `tests/frida_stub.py` | 184 | Test helper: stub `frida`/`frida_tools` |
| `tests/test_frida_stub.py` | 167 | 10 test cho stub |
| `tests/test_suite_quality.py` | 186 | Guard: mọi test phải chạm được assertion; ranh giới mock/real |
| `tests/test_real_frida_integration.py` | 109 | Tầng REAL INTEGRATION (skip khi dùng stub) |
| `tests/test_credential_hygiene.py` | 279 | 20 test: redaction, quyền file, bỏ PIN-on-disk |
| `tests/test_uitest_crane_rpc.py` | 214 | 19 test cho `CraneRpcManager` |
| `tests/test_crane_assignment_race.py` | 191 | 6 test cho race gán container |
| `tests/test_resource_cleanup.py` | 255 | 13 test cho F7/F8/F9 |
| `tests/test_alert_guard_diagnostics.py` | 99 | 4 test cho F5 |
| `docs/ui-automation-audit.md` · `ui-automation-architecture.md` · `ios-interaction-methods.md` | ~550 | Audit + kiến trúc + bảng so sánh lớp tương tác |
| `audit/*.md`, `audit/CHANGES.diff` | ~1 400 | Báo cáo |

Tổng trong `CHANGES.diff`: **+419 −65** trên 17 file có baseline.

---

## 3. Kết quả unit test

```bash
python -X utf8 -m unittest discover -s tests
# Ran 270 tests — OK (skipped=4)
```

| Phân loại | Số lượng | Nghĩa |
| --- | --- | --- |
| **MOCK PASS** | **267** | Kiểm chứng bằng mock/double/fixture. Không chạm thiết bị, Frida, Crane, mạng hay COIN+. |
| **REAL INTEGRATION PASS** | **0** | Không có, vì máy này không cài `frida`. |
| **NOT TESTED** | **4 bản ghi skip** (phủ 5 test method) | Cần `frida` thật và/hoặc thiết bị. |
| FAIL / ERROR | **0** | |

### Phân bố theo module

| Module | MOCK | NOT TESTED |
| --- | --- | --- |
| `test_uitest_adapter` | 43 | |
| `test_credential_hygiene` | 20 | |
| `test_uitest_crane_rpc` | 19 | |
| `test_signup_flow`, `test_uitest_integration` | 18 mỗi module | |
| `test_uitest_crane_session` | 16 | |
| `test_proxy` | 15 | |
| `test_resource_cleanup`, `test_respring` | 13 mỗi module | |
| `test_diagnostics`, `test_uitest_waits` | 12 mỗi module | |
| `test_frida_stub`, `test_wrapper` | 10 mỗi module | |
| `test_line_coin_data` | 9 | |
| `test_device_assignment` | 8 | |
| `test_crane_assignment_race` | 6 | |
| `test_suite_quality` | 5 | |
| `test_alert_guard_diagnostics`, `test_launch_order` | 4 mỗi module | |
| `test_excel_replace` | 2 | |
| `test_permission_alerts` | 1 | |
| `test_real_frida_integration` | 0 | **4** |

### NOT TESTED — chi tiết

| Test | Lý do skip |
| --- | --- |
| `RealFridaPackageTests.test_agent_source_loads_the_real_objc_bridge` | cần `frida-tools` thật (đọc ObjC bridge) |
| `RealFridaPackageTests.test_enumerate_devices_returns_a_list` | cần `frida` thật |
| `RealFridaPackageTests.test_installed_frida_is_the_pinned_major_version` | cần `frida` thật |
| `AttachedDeviceTests` (2 method, skip ở `setUpClass`) | cần `frida` thật **và** iPhone đang kết nối |

### Kiểm tra test có assertion thật

Quét AST toàn bộ 270 test method: **mọi method đều chạm được assertion** (trực
tiếp, qua helper trên `self`, qua hàm module-level, hoặc qua `setUp`). Guard nằm
trong `tests/test_suite_quality.py` để không tái diễn.

**Giới hạn phải nói rõ:** "chạm được assertion" là **sàn**, không phải bằng chứng
test kiểm chứng hành vi. Một test có thể assert điều hiển nhiên đúng. Trong
session này đã gặp hai lần:

1. `test_main_detaches_the_session_even_if_the_guard_raises` — `guard` không bao
   giờ khác `None` trên đường `snapshot`, nên nó assert một mệnh đề luôn đúng.
   **Đã loại bỏ**, thay bằng hai test thật sự đi qua `finally` của `main.py`.
2. `test_outer_handlers_redact` — chỉ kiểm tra chữ `redact` có trong source;
   mutation cho thấy nó vẫn PASS khi bỏ lời gọi `redact(...)` vì dòng `import`
   giữ lại chữ đó. **Đã giữ làm sàn cấu trúc** và bổ sung
   `RedactingBoundaryTests` chạy thật hai entry point rồi đọc stderr.

Chỉ **mutation test** chứng minh được một test có khả năng fail.

### Test không gây pollution repo

Đã kiểm: chạy toàn bộ suite rồi so sánh danh sách file trong repo trước/sau —
**không file nào được tạo**. (Trước đó test rỗng ở điểm 1 từng ghi một file vào
`dumps/`; đã xoá cùng test đó.)

---

## 4. Kết quả mutation test

Chạy trên **bản copy** repo trong thư mục tạm; repo thật không bị sửa, nên một
lần chạy bị ngắt cũng không để lại code đã mutate.

Baseline trên bản copy: `OK (skipped=4)`. Sau khi khôi phục: `OK (skipped=4)`.

| Fix hoàn nguyên | Test target | Kết quả |
| --- | --- | --- |
| F1 — bỏ `parsed_dom_args.profile_data` | `test_credential_hygiene.InMemoryProfileTests` | detected |
| F2 — bỏ `redact(...)` ở `main.py` | `test_credential_hygiene.RedactingBoundaryTests` | detected |
| F3 — trả lại `write_text` thường | `test_credential_hygiene.ProfileExportTests` | detected |
| F4 — bỏ `container_assignment_lock` | `test_crane_assignment_race` | detected |
| F5 — bỏ cờ `degraded` | `test_alert_guard_diagnostics` | detected |
| F6 — bỏ `_verify_container_stamp` | `test_uitest_adapter.ContainerIntegrationTests` | detected |
| F7 — bỏ `device.kill(pid)` | `test_resource_cleanup.SpawnedProcessCleanupTests` | detected |
| F8 — bỏ nhánh `BaseException` | `test_resource_cleanup.SlotDescriptorTests` | detected |
| F9 — trả lại `finally` cũ | `test_resource_cleanup.FinallyOrderingTests` | detected |
| F10 — bỏ bootstrap stub ở 1 module | `test_frida_stub.WiringTests` | detected |
| guard — thêm 1 test rỗng | `test_suite_quality.AssertionCoverageTests` | detected |

**11/11 mutation bị phát hiện.**

Hai lần chạy đầu phát hiện đúng hai lỗ hổng, đã vá trước khi chốt:

- **F2 NOT DETECTED** — test chỉ kiểm tra chuỗi trong source (xem mục 3).
- **F3 NOT DETECTED** — `PrivateFileTests` test `write_private_json` trực tiếp,
  **không** test `write_profile_json`. Tức đường PIN-thường-trú (S4) không có
  test nào phủ. Đã thêm `ProfileExportTests`.

Script mutation **không** được đưa vào repo: nó sửa file tại chỗ, nên dù chạy
trên bản copy vẫn không nên để trong repo như một lệnh dễ gọi nhầm. Nó nằm trong
thư mục tạm của session; muốn giữ lâu dài thì nên viết lại thành tool riêng,
luôn tự copy repo và không nhận đường dẫn repo thật làm đích sửa.

---

## 5. Giới hạn của Frida stub

`tests/frida_stub.py`. Mục đích: cho suite chạy trên máy không cài `frida`, vì
`driver.py` và các script trong `scripts/` import `frida` ở module scope.

### Những gì stub bảo đảm

| Tính chất | Cách bảo đảm | Test |
| --- | --- | --- |
| Không che `frida` thật | `real_frida_available()` kiểm tra trước khi cài | `test_does_nothing_when_a_real_frida_is_present` |
| Không âm thầm thay thế khi cần bản thật | `AIRWALLET_REQUIRE_REAL_FRIDA=1` → raise `RealFridaRequired` | `test_stub_refuses_to_substitute_when_a_real_run_is_demanded` |
| Không giả làm thiết bị | `enumerate_devices()` trả `[]`; `get_device`/`spawn`/`attach`/`kill` raise | `test_every_device_entry_point_fails_loudly` |
| Không nạp agent giả | `frida_tools.__file__` trỏ đường dẫn không tồn tại | `test_frida_tools_path_does_not_resolve_to_a_fake_agent` |
| Mọi module cần nó đều gọi bootstrap | Quét source 8 module | `test_every_frida_dependent_test_module_installs_the_stub` |
| Không còn stub inline trùng lặp | Quét `tests/test_*.py` | `test_no_module_keeps_its_own_inline_stub` |
| Test real-integration **skip**, không pass | Chạy module đó và đếm | `test_real_integration_tests_are_skipped_not_passed_under_the_stub` |

### Những gì stub **không** làm được

1. **Không kiểm chứng hành vi Frida.** Mọi test dùng stub chỉ chứng minh logic
   Python quanh Frida, không chứng minh lời gọi Frida đúng. Ví dụ: `device.kill`
   trên process vừa spawn còn suspend (F7) chỉ được xác nhận là *được gọi*, chưa
   xác nhận *có tác dụng*.
2. **Không kiểm chứng RPC / agent.** `agent.js`, `CRANE_JS`, `SB_DECLINE_JS`
   không được nạp hay chạy. Các selector UIKit, `_dismissWithAction:`, layout ivar
   `_handler` hoàn toàn chưa chạy.
3. **Không kiểm chứng timing thật.** Test dùng `FakeClock`, nên các ngưỡng
   timeout/backoff đúng về logic, chưa đúng về thực tế thiết bị.
4. **Không bắt được lệch phiên bản API.** Stub có mọi attribute mà project dùng
   hiện tại; nếu `frida` thật đổi API, chỉ bản thật mới phát hiện.
5. Stub không mô phỏng `frida.RPCException` phát sinh từ agent, nên nhánh xử lý
   lỗi RPC chưa được chạy với lỗi thật.

### Chạy integration pass

```bash
AIRWALLET_REQUIRE_REAL_FRIDA=1 python -X utf8 -m unittest discover -s tests
```

Với biến đó, nếu thiếu `frida` thật thì **suite lỗi ngay** thay vì âm thầm
chuyển sang mock. Đây là cơ chế chống đúng cái bẫy "MOCK PASS bị đọc thành
REAL INTEGRATION PASS".

---

## 6. Chưa kiểm chứng bằng iPhone thật

Không một mục nào dưới đây đã chạy trên thiết bị. **Unit test PASS không chứng
minh chúng hoạt động.**

| # | Mục | Rủi ro nếu sai |
| --- | --- | --- |
| 1 | `device.kill(pid)` trên process vừa spawn, còn suspend (F7) | Nếu kill thất bại, lỗi bị bỏ qua có chủ ý và app vẫn nằm lại; `--fresh-launch` lượt sau sẽ kill nó |
| 2 | `CraneRpcManager` giữ một `CraneHostSession` mở suốt lượt test | Trước đây Crane host bị spawn rồi kill mỗi lần gọi; giữ mở có thể tranh RAM/CPU, đúng cái mà `crane_slots_default()=2` đang phòng |
| 3 | Độ trễ thật khi `container_assignment_lock` tuần tự hoá việc gán qua N thiết bị | Có thể làm batch chậm; nếu nặng thì chuyển sang claim-trước-RPC (đã ghi trong docstring) |
| 4 | `container_id` từ Crane có khớp container app thực sự đang chạy trong đó (F6) | Đây là **giả định cốt lõi** của F6; nếu sai, `_verify_container_stamp` có thể báo động sai hoặc bỏ sót |
| 5 | `WebDriverAgentBackend` — chưa chạy với thiết bị nào | Toàn bộ `uitest/` adapter mới chỉ chạy với mock |
| 6 | `_screen_id()` heuristic của WDA backend | Dựa vào navigation bar identifier; app dưới test nên gắn identifier `screen.<tên>` |
| 7 | `reap_stale` dọn đúng file trên máy vận hành | Pattern `input_*.json`, ngưỡng 1h; đã test với fixture, chưa với thư mục thật |
| 8 | `restrict_to_owner` qua `icacls` trên máy vận hành khác | Đã xác minh **trên máy này** (ACE từ 3 còn 1); máy khác có thể khác domain/quyền |
| 9 | Guard modal: cờ `degraded` khi phiên SpringBoard chết thật | Chỉ test với sweep giả raise |

---

## 7. Rủi ro bảo mật còn lại

Chi tiết trong [`SECRET_EXPOSURE_REPORT.md`](SECRET_EXPOSURE_REPORT.md).

### Đã khắc phục trong code

| ID | Rủi ro | Trạng thái |
| --- | --- | --- |
| E1 | PIN plaintext sót sau SIGKILL | **Đã loại bỏ nguyên nhân** — không còn ghi file; thêm sweeper cho file cũ |
| E2 | Secret lọt vào stderr/log qua exception | **Đã vá** ở `main.py` và `airwallet_dom_runner.py` |
| S4 | File profile thường trú không giới hạn quyền | **Đã vá** — owner-only + cảnh báo |

### Còn lại, cần người vận hành xử lý

| ID | Rủi ro | Mức | Việc cần làm |
| --- | --- | --- | --- |
| S1 | API key OTPBase plaintext trong `config.json` | TB | Chuyển sang chỉ dùng env var (đã hỗ trợ); dài hạn dùng secret manager |
| S2 | `pin`, `account_password` plaintext trong Excel | **CAO** | Coi như **đã lộ**; thay credential; BitLocker + siết ACL `data/`; bỏ cột `pin` khỏi Excel |
| S3 | Proxy `user:password` plaintext trong Excel | TB | Như trên |
| U1 | Không rõ secret có từng bị commit từ bản copy khác | **UNKNOWN** | `git log --all -- config.json data/ agents/runtime/ dumps/` trên bản copy đó |
| U3 | `agents/runtime/input_*.json` có thể còn sót trên máy vận hành | TB | `ls -la agents/runtime/` rồi xoá |
| — | `dumps/` không có TTL; chứa chữ trên màn hình (tên kana) | Thấp | Đặt TTL 7–14 ngày |
| — | `/audit/` chưa có trong `.gitignore` | Thấp | Hai báo cáo hiện **không chứa secret** (đã kiểm) nên commit được; thêm vào `.gitignore` nếu sau này báo cáo chứa dữ liệu vận hành |

### Rủi ro không thuộc phạm vi code

Giữ nguyên kết luận trong `docs/ui-automation-audit.md` §6.1: dây chuyền đăng ký
hiện tại tạo tài khoản hàng loạt trên một dịch vụ thanh toán đang hoạt động, bằng
số điện thoại thuê và một proxy cho mỗi tài khoản. Đó là **giới hạn chặn**, không
phải lỗi kỹ thuật; không bản sửa nào trong session này thay đổi điều đó, và
`uitest/` cố tình không nối vào luồng đó (`jp.coinplus.app` không nằm trong
allow-list, có test khẳng định).

---

## 8. Kiểm tra release

| Kiểm tra | Lệnh | Kết quả |
| --- | --- | --- |
| Trailing whitespace / conflict marker | `git -c core.autocrlf=false diff --no-index --check <baseline> <file>` | **sạch**, 0 phát hiện |
| Byte-compile | `python -m compileall -q .` | **OK** |
| Line endings | so byte `\r\n` trên mọi `*.py/*.js/*.json/*.md` | **100% LF**, khớp `.gitattributes` (`*.py text eol=lf`) |
| Suite | `python -X utf8 -m unittest discover -s tests` | **270 PASS, 4 skip, 0 fail** |
| Mutation | 11 mutation trên bản copy | **11/11 detected** |
| Repo pollution | so file trước/sau khi chạy suite | **không file mới** |

Ghi chú về git: repo chưa init. Khi init, `.gitattributes` đã có
`*.py text eol=lf`, nên `core.autocrlf` của máy sẽ không làm bẩn diff các file
Python. `git diff --check` thật chỉ chạy được sau khi có repo.

---

## 9. Chưa làm

| # | Việc | Vì sao |
| --- | --- | --- |
| N4 | `agents/runtime/spawn_slots/slot_*.lock` tích luỹ | Không phải leak — tên cố định, được tái dùng |
| — | Gộp redaction của `flow.py`/`airwallet_full_flow.py` vào `src/utils/redaction.py` | Hai chỗ đó **đã** redact đúng; sửa chúng là chạm vào luồng đăng ký, ngoài phạm vi được phép |
| — | `claim-trước-RPC` cho `container_assignment_lock` | Chỉ cần nếu độ trễ thật thành vấn đề (mục 6 #3) |
| — | Mục 3/4/5 trong §9 `docs/ui-automation-architecture.md` | Cần thiết bị thật |
| — | Commit | Chờ người vận hành review và xác nhận |
