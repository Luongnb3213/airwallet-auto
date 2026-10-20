# Kiến trúc UI automation đề xuất

Đi kèm: `docs/ui-automation-audit.md` (hiện trạng),
`docs/ios-interaction-methods.md` (so sánh các lớp tương tác).
Code: `uitest/`. Test: `tests/test_uitest_*.py`.

---

## 1. Nguyên tắc

1. **Mọi thao tác đi qua framework kiểm thử giao diện.** Không target/action,
   không `insertText:`, không selector riêng tư, không toạ độ.
2. **Chỉ chạy trên target được khai báo.** Không có target mặc định.
3. **Thành công = trạng thái được kiểm chứng.** Không có đường "đã bấm thì coi
   như xong".
4. **Chọn phần tử bằng accessibility identifier.** Chuỗi hiển thị là fallback có
   ghi log; toạ độ không tồn tại trong API.
5. **Chờ theo điều kiện, luôn có trần.** Không sleep cố định. Backoff chỉ cho lỗi
   hạ tầng, chỉ cho lời gọi đọc, không có jitter.
6. **Gặp cái chưa nhận dạng thì dừng**, để nguyên trên màn hình cho người đọc.

---

## 2. Các lớp

```
 Test case  (pytest / unittest)
     │   Selector.by_id(...), ScreenSpec, Expectation
     ▼
 UITestAdapter                         uitest/adapter.py
     │   6 cổng kiểm tra trước mỗi thao tác (mục 3)
     ├── policy.py        AuthorizedTarget · AlertPolicy
     ├── waits.py         Budget · Deadline · Waiter · retry_read
     ├── selectors.py     Selector (identifier-first)
     ├── expectations.py  ScreenIs · ElementValue · ElementEnabled · BusinessResult …
     ├── crane.py         prepare_container() · CraneContainerSession
     ▼
 Backend  (protocol)                   uitest/backends/base.py
     ├── WebDriverAgentBackend         uitest/backends/wda.py    ← XCUITest
     └── MockBackend + FakeApp         uitest/backends/mock.py   ← test
     ▼
 model.py   Snapshot · Element · Rect · Alert · ScreenToken   (bất biến)
```

Backend protocol **không có** `tap_coordinate`, `send_action`, `invoke_selector`,
`set_value`. Thiếu các method đó là một quyết định thiết kế, có test canh
(`NoForbiddenSurfaceTests`).

---

## 3. Sáu cổng trước mỗi thao tác ghi

`tap_element` và `type_text` đều đi qua đúng trình tự sau:

| # | Cổng | Lỗi khi không đạt |
|---|---|---|
| 1 | Target được cấp phép; app đang foreground đúng bundle | `TargetNotAuthorizedError` |
| 2 | Container Crane bound lúc mở session vẫn đang active | `ContainerChangedError` |
| 3 | Không có alert nào ngoài danh sách đã khai báo | `UnexpectedAlertError` |
| 4 | Phần tử đã sẵn sàng (có, visible) — chờ theo điều kiện | `ElementNotFoundError` / `AmbiguousSelectorError` / `ElementNotInteractableError` |
| 5 | Màn hình đã dừng đổi (hết animation) | `NotSettledError` |
| 6 | Phần tử chạm được: enabled, visible, hittable, trong vùng tương tác, không bị bàn phím che | `ElementNotInteractableError` / `KeyboardObstructionError` |
| 7 | Định danh màn hình không đổi giữa cổng 6 và lúc dispatch | `ScreenChangedError` |
| 8 | Không lặp lại đúng thao tác đó trên đúng màn đó | `RepeatedActionError` |
| 9 | *Sau* khi dispatch: post-condition quan sát được, và vẫn đúng khi màn đã dừng | `ExpectationFailedError` |

Cổng 7 là thứ `agent.js` hiện không có: `requireScreen()` kiểm tra **một lần** rồi
mới đi tìm nút; ở đây việc đọc lại ngay trước khi dispatch biến "màn hình đổi giữa
lúc kiểm tra và lúc bấm" từ một lỗi âm thầm thành một lỗi có tên.

Cổng 8 trả lời yêu cầu "thao tác lặp ngoài dự kiến": nếu cùng một
`(verb, selector, định-danh-màn)` xuất hiện lần thứ hai, mặc định là lỗi — vì
nghĩa của nó là bước trước **không** có tác dụng, và bấm lại là nguy cơ
double-submit. Control thật sự lặp được (bàn phím số, stepper) thì khai báo
`allow_repeat=True`.

---

## 4. Sáu API theo yêu cầu

| API | Hợp đồng |
|---|---|
| `snapshot(settled=False)` | Đọc trạng thái. **Thuần quan sát**: alert lạ được *báo trong snapshot*, không raise — để còn chẩn đoán được từ dump. `settled=True` thì chờ UI dừng trước khi đọc. |
| `find_element(selector, ...)` | Đúng một phần tử. 0 → `ElementNotFoundError` (kèm danh sách identifier đã thấy, để debug). ≥2 → `AmbiguousSelectorError`, trừ khi selector có `index`. |
| `tap_element(selector, *, expect, ...)` | `expect` **bắt buộc**. Qua 9 cổng ở mục 3. Trả về snapshot đã dừng, là bằng chứng. |
| `type_text(selector, text, *, expect=None, secret=False)` | Mặc định kiểm chứng bằng cách đọc lại field. Field secure không đọc lại được → **bắt buộc** `expect` khác (ví dụ nút Tiếp tục bật lên), nếu không thì `UnverifiableInputError`. `secret=True` thì giá trị bị redact khỏi log và message lỗi. |
| `wait_until_visible(selector, *, timeout=None, interactable=False)` | Chờ phần tử sẵn sàng, có trần. Khi hết thời gian thì **suy ra nguyên nhân thật** (không có / mơ hồ / bị che / disable) thay vì trả timeout trống. |
| `verify_screen(spec)` | `ScreenSpec(screen_id, required, forbidden)`. Lỗi thì báo đúng diff: thiếu gì, có gì không được phép. |

Bổ sung: `dismiss_alert(expect=...)` — chỉ cho alert đã khai báo trong
`AlertPolicy`, và chỉ bấm đúng nút mà rule đặt tên. Hai rule cùng khớp cũng tính
là "chưa nhận dạng".

### Ví dụ

```python
from uitest import Budget, Selector, UITestAdapter, TargetRegistry
from uitest.crane import CraneContainerSession, prepare_container
from uitest.expectations import ElementEnabled, ScreenIs, ScreenSpec
from uitest.policy import AlertPolicy, AlertRule

LOGIN = ScreenSpec('screen.login', name='Login',
                   required=(Selector.by_id('login.user'), Selector.by_id('login.submit')))

target = TargetRegistry.from_file('uitest/authorized_targets.json').require('com.example.demo')
policy = AlertPolicy((
    AlertRule(name='ios_notifications', system=True,
              title='"Demo" Would Like to Send You Notifications', dismiss_with="Don't Allow"),
))

prepare_container(crane_rpc, target.bundle_id, 'profile-qa-1')   # switch: TRƯỚC session
with CraneContainerSession(crane_rpc, target.bundle_id, 'profile-qa-1') as container:
    with UITestAdapter(backend, target=target, budget=Budget(timeout=10),
                       alert_policy=policy, container=container) as ui:
        ui.verify_screen(LOGIN)
        ui.type_text(Selector.by_id('login.user'), 'qa-tester')
        ui.type_text(Selector.by_id('login.password'), fixture_password, secret=True,
                     expect=ElementEnabled(Selector.by_id('login.submit'), True))
        ui.tap_element(Selector.by_id('login.submit'), expect=ScreenIs('screen.landing'))
```

---

## 5. Quản lý thời gian

`Budget` là tập trần, không phải tập mục tiêu:

```python
Budget(timeout=10.0,          # trần cho một lần chờ điều kiện
       poll_interval=0.25,
       settle_for=0.3,        # UI phải không đổi liên tục bấy nhiêu giây
       settle_samples=2,      # ít nhất 2 mẫu giống nhau
       settle_timeout=5.0,    # trần cho cổng animation
       infra_retries=3,       # chỉ cho lỗi hạ tầng
       infra_backoff=0.5, infra_backoff_max=4.0)
```

Bốn cơ chế, cố ý không thay thế được cho nhau:

- **`Waiter.poll`** — chờ một *điều kiện UI*. `inspect` chạy trên từng mẫu và có
  thể raise để dừng sớm; đó là cách alert lạ cắt ngang một lần chờ thay vì đốt hết
  timeout.
- **`Waiter.settle`** — cổng animation. So `Snapshot.fingerprint()` (hash của
  frame đã lượng tử hoá nửa pixel, value, enabled, visible, focused, keyboard,
  alert) giữa các lần đọc liên tiếp. Transition, keyboard trượt lên, spinner đều
  làm fingerprint đổi.
- **`Deadline`** — mọi lần chờ đều có trần; hết hạn thì raise, không bao giờ "chờ
  không được thì cứ làm".
- **`retry_read`** — backoff luỹ thừa có cap cho `InfrastructureError`, **chỉ cho
  lời gọi đọc**. `tap`/`type_text`/`clear_text` không bao giờ được retry: một cú
  tap bị retry có thể submit nghiệp vụ hai lần. Có test canh điều này bằng cách
  soi chính source của `adapter.py`
  (`test_backoff_is_never_applied_to_a_mutating_call`).

Backoff **không có jitter**. Đó là lịch retry cho transport hay lỗi, không phải
phân bố thời gian để che giấu automation.

### Thay thế các sleep cố định trong repo hiện tại

| Hiện tại | Thay bằng |
|---|---|
| `driver.py:43` sleep 3 s sau khi load script | `Waiter.poll` đến khi `snapshot()` đầu tiên thành công (`retry_read` đã lo transport chưa sẵn sàng) |
| `agent.js:180` sleep 200 ms giữa các chữ số PIN | `tap_element(..., allow_repeat=True, expect=ElementValue(dots, '*' * n))` — chờ chính indicator PIN |
| `dom_runner:674` `load_wait` | như trên |
| `dom_runner:554` sleep 1 s sau resume | chờ app có pid và attach được (đã có `wait_app_stopped`, làm đối xứng) |
| Sleep sau respring / cooldown batch | **Giữ.** Đây là tầng hạ tầng thiết bị, ngoài phạm vi adapter. |

---

## 6. Tích hợp Crane

Quy tắc: **switch trước session, không bao giờ trong session.**

| Yêu cầu | Thực hiện |
|---|---|
| Xác định container đã được lựa chọn | `prepare_container()` — kiểm tra app được Crane quản lý, container tồn tại, rồi switch và **đọc lại để xác nhận**. Từ chối nếu không truyền container id. |
| Kiểm tra trạng thái trước khi chạy test | `CraneContainerSession.open()` — **không switch**. Container active khác mong đợi là lỗi (`ContainerNotReadyError`), vì nếu không thì "test này thuộc account nào" sẽ sai âm thầm. |
| Không chuyển container trong khi test chạy | `assert_stable()` chạy ở cổng 2 trước mỗi thao tác ghi; lệch → `ContainerChangedError`. Thêm nữa, `prepare_container()` **bị từ chối** khi có session đang giữ app đó (registry `_HELD`). |
| Dọn tài nguyên, detach sau khi xong | `close()` idempotent: nhả hold, detach RPC; vẫn nhả hold kể cả khi detach lỗi. `UITestAdapter.close()` đóng cả backend lẫn container. |

`recheck_interval=0` (mặc định) kiểm tra mỗi thao tác; đặt dương thì cache bản đọc
cuối trong bấy nhiêu giây, cho transport chậm — và vẫn bắt được thay đổi sau đó
(có test).

Giải quyết C1/C5 trong audit. **Không** giải quyết C2 (snapshot mang container id)
ở tầng adapter: `Snapshot.container_id` đã có chỗ trong model và mock điền được,
nhưng WDA không biết container, nên muốn làm thật thì cần một bước đọc từ Crane
rồi ghi vào snapshot — chưa làm, ghi lại ở mục 9.

Không can thiệp Keychain, fingerprint hay định danh thiết bị. `uitest/crane.py`
không có API nào như vậy và có test khẳng định (quét code đã bỏ comment/docstring).

---

## 7. Kiểm thử

`tests/test_uitest_waits.py` (12) · `tests/test_uitest_crane_session.py` (16) ·
`tests/test_uitest_adapter.py` (40) · `tests/test_uitest_integration.py` (15) —
**83 test, chạy không cần thiết bị, không cần Frida, không cần mạng.**

Các tình huống bắt buộc theo yêu cầu:

| Tình huống | Test |
|---|---|
| Element không tồn tại | `test_find_element_reports_absence_with_the_identifiers_it_did_see` |
| Element nằm ngoài vùng tương tác | `test_tap_refuses_an_element_outside_the_interactable_area`, và `test_tap_scrolls_an_off_screen_element_into_view_once_then_proceeds` cho nhánh khôi phục |
| Màn hình thay đổi | `test_tap_stops_when_the_screen_changes_before_dispatch` |
| Animation chưa kết thúc | `test_tap_waits_for_an_animation_to_finish`, `test_tap_refuses_to_act_while_the_screen_keeps_moving`, `test_settle_*` |
| Keyboard che khuất phần tử | `test_tap_refuses_an_element_under_the_keyboard`, `test_tap_dismisses_the_keyboard_as_a_bounded_recovery` |
| Container không sẵn sàng | `test_container_not_ready_stops_before_any_action`, `test_open_fails_when_crane_is_unavailable`, `test_open_requires_a_selected_container` |
| Timeout | `test_poll_is_bounded_and_reports_elapsed`, `test_wait_until_visible_times_out_with_the_precise_reason` |
| Thao tác lặp ngoài dự kiến | `test_tap_refuses_an_unexpected_repeat_on_the_same_screen`, `test_typing_the_same_value_twice_is_refused`, `test_allow_repeat_permits_a_genuinely_repeatable_control` |

Thêm: container bị switch giữa test, alert chưa khai báo (và alert đã khai báo),
hai rule cùng khớp, selector mơ hồ, field secure, redact secret, backoff cho lỗi
transport, cổng cấp phép target, cổng `business_write`, và mapping của backend WDA.

Chạy:

```bash
python -m unittest tests.test_uitest_waits tests.test_uitest_adapter tests.test_uitest_crane_session tests.test_uitest_integration
```

---

## 8. Trạng thái kiểm chứng

Cần nói rõ, vì nó quyết định mức tin cậy của từng phần:

| Thành phần | Trạng thái |
|---|---|
| `adapter.py`, `waits.py`, `selectors.py`, `expectations.py`, `policy.py`, `model.py`, `crane.py` | **Đã test**, 83 test xanh với mock. Logic cổng, timing, lifecycle đều có test. |
| `backends/mock.py` | Đã test (là công cụ test). |
| `backends/wda.py` | **Chỉ test với HTTP double.** Viết theo các route WebDriverAgent (`/source?format=json`, `/elements`, `/element/{id}/click`, `/element/{id}/value`, `/element/{id}/clear`, `/wda/element/{id}/scroll`, `/wda/keyboard/dismiss`, `/alert/accept`, `/wda/activeAppInfo`). **Chưa chạy lần nào với thiết bị thật trong repo này.** Phải đối chiếu lại với đúng phiên bản WDA đang dùng trước khi tin. |
| `_screen_id()` trong WDA backend | **Heuristic.** XCUITest không expose view controller. Hiện lấy identifier của `XCUIElementTypeNavigationBar`, rồi đến element có identifier bắt đầu bằng `screen.`. App dưới test nên gắn một identifier định danh màn rõ ràng; nếu không thì override bằng subclass. |
| `CraneContainerSession` với RPC Crane thật | **Chưa.** Hợp đồng `ContainerManager` (`info`/`switch`/`detach`) khớp với khả năng của `CraneHostSession` hiện có, nhưng cần một adapter mỏng và một lần chạy thật. |

---

## 9. Việc chưa làm

1. **Adapter nối `CraneHostSession` → `ContainerManager`.** Chỉ cần một class mỏng
   map `call('info', app_id)` / `call('switch', ...)` / `detach()`.
2. **Ghi container id vào snapshot** (audit C2) để dump tự chứng minh nó thuộc
   container nào. Chỗ trong model đã có (`Snapshot.container_id`).
3. **Chạy thật `WebDriverAgentBackend`** trên một build demo, và sửa lại theo
   thực tế.
4. **Chuẩn hoá `screen_id` ở app dưới test**: thêm một accessibility identifier
   `screen.<tên>` cho mỗi màn, để bỏ hẳn heuristic.
5. **Snapshot artifact khi lỗi**: hiện `ActionRecord` giữ lịch sử trong RAM; nên
   ghi ra file kèm diff khi một cổng chặn.
