# Các lớp tương tác giao diện trên iOS — bảng so sánh

Tài liệu này tách rõ những cách "bấm một nút" trên iOS. **Chúng không tương đương
nhau.** Mỗi lớp đi vào chuỗi xử lý sự kiện ở một độ sâu khác nhau, nên cái mà app
quan sát được cũng khác nhau.

## Chuỗi xử lý sự kiện đầy đủ khi người dùng chạm thật

1. Màn hình cảm ứng → bộ điều khiển cảm ứng → `IOHIDEvent` trong kernel.
2. `backboardd` nhận HID event, xác định app nào đang nhận touch.
3. `UIApplication.sendEvent(_:)` trong tiến trình app.
4. **Hit-testing**: `UIWindow` → `hitTest(_:with:)` đệ quy xuống view tree, có xét
   `isUserInteractionEnabled`, `alpha`, `isHidden`, `clipsToBounds`, `frame`.
5. **Gesture recognizer arbitration**: các `UIGestureRecognizer` trên đường responder
   tranh quyền; `delaysTouchesBegan`, `require(toFail:)`, `cancelsTouchesInView`.
6. `touchesBegan/Moved/Ended/Cancelled` trên view và responder chain.
7. Với `UIControl`: `beginTracking`/`continueTracking`/`endTracking`, cập nhật
   `isHighlighted`, rồi mới `sendActions(for: .touchUpInside)`.
8. Target/action được gọi → code nghiệp vụ của app chạy.
9. Hệ quả phụ: haptic, accessibility notification, analytics gắn vào touch,
   `UIKeyboard*` notification nếu đổi first responder.

Mỗi lớp dưới đây "vào" chuỗi này ở một bước khác nhau. Càng vào sâu (bước lớn)
thì càng nhiều tầng bị bỏ qua.

## Bảng so sánh

| | Chạm vật lý | XCUITest / WebDriverAgent | Inject HID (`IOHIDEvent`, `simulateTouch`) | UIKit target/action (`sendActionsForControlEvents:`) | API văn bản trực tiếp (`insertText:`, `setText:`) | Gọi selector riêng tư (`_dismissWithAction:`, block `_handler`) |
|---|---|---|---|---|---|---|
| **Nguồn sự kiện** | Phần cứng cảm ứng | Tiến trình test ngoài app, qua `XCUIApplication` | Tiến trình có quyền cao, bơm event vào hệ thống | Code chạy **trong** tiến trình app (Frida) | Code trong tiến trình app | Code trong tiến trình app hoặc SpringBoard |
| **Vào chuỗi ở bước** | 1 | 2–3 (hệ thống tổng hợp HID event thật cho app) | 2 | **7–8** (bỏ qua 4,5,6 và phần tracking) | **8+** (bỏ qua toàn bộ tầng touch *và* tầng keyboard) | **9** (gọi thẳng handler, bỏ qua cả cơ chế dismiss) |
| **Hit-testing chạy?** | Có | Có | Có | **Không** | **Không** | **Không** |
| **Gesture recognizer chạy?** | Có | Có | Có | **Không** | **Không** | **Không** |
| **`UIControl` tracking + `isHighlighted`?** | Có | Có | Có | **Không** | Không áp dụng | Không áp dụng |
| **View bị che/`alpha=0`/ngoài màn vẫn "bấm" được?** | Không | Không (báo lỗi not hittable) | Không | **Có** — đây là nguồn lỗi âm thầm | **Có** | **Có** |
| **Bàn phím thật tham gia?** | Có | Có (`typeText` qua keyboard) | Có | Không áp dụng | **Không** | Không áp dụng |
| **Delegate `textField(_:shouldChangeCharactersIn:)`** | Có | Có | Có | Không áp dụng | **Không chắc chắn** — phụ thuộc phiên bản UIKit và cách field implement `UITextInput`; không nằm trong hợp đồng công khai | Không áp dụng |
| **`UIControlEventEditingChanged` tự phát sinh?** | Có | Có | Có | Không áp dụng | **Không** — phải tự bắn tay (xem `agent.js:202`) | Không áp dụng |
| **Cần jailbreak?** | Không | Không (cần build test được sign, hoặc WDA) | Thường có | Có (Frida inject) | Có | Có |
| **Dùng được trên app không sở hữu?** | — | Chỉ khi có quyền/được cấp phép | Về mặt kỹ thuật có | Về mặt kỹ thuật có | Về mặt kỹ thuật có | Về mặt kỹ thuật có |
| **Độ ổn định qua các bản app** | — | Cao (dựa accessibility, là hợp đồng công khai) | Trung bình | Thấp (phụ thuộc cấu trúc view, index, class name) | Thấp | **Rất thấp** (ivar/ABI của block có thể đổi bất kỳ lúc nào) |
| **App phân biệt được với chạm thật?** | — | Có: `XCUITest` để lại dấu hiệu (process test, `UIAccessibility` được bật đầy đủ); không nhằm che giấu | Khó hơn | Có: không có touch nào trong chuỗi | Có | Có |
| **Dùng đúng chỗ** | Người dùng thật | **Kiểm thử UI app mình sở hữu/được cấp phép** | Kiểm thử hệ thống, nghiên cứu nội bộ có phép | Unit/integration test **bên trong** app, hoặc nghiên cứu/debug | Nghiên cứu/debug, dựng state nhanh cho test | Gần như không bao giờ trong code vận hành |
| **Rủi ro chính** | — | Cần hạ tầng; không chạy được trên app bên thứ ba không có phép | Quyền cao, dễ gây hỏng state hệ thống | **Kích hoạt code nghiệp vụ mà UI chưa thực sự ở trạng thái cho phép** | Ghi dữ liệu mà app chưa validate | Crash, hành vi không xác định, mất hiệu lực sau mỗi bản iOS |

## Ba kết luận cần nhớ

1. **Gọi trực tiếp Objective-C method không phải là một cú chạm.** Nó là lời gọi
   hàm vào tầng xử lý *sau* khi app lẽ ra đã quyết định "cú chạm này hợp lệ".
   Một nút đang bị overlay che, đang ngoài vùng scroll, hoặc đang bị gesture
   recognizer khác giữ, vẫn chạy action nếu ta gọi `sendActionsForControlEvents:`.
   Vì vậy nó không chứng minh được rằng người dùng thật bấm được nút đó.

2. **Sự kiện do framework kiểm thử tạo ra gần với chạm thật, nhưng không phải chạm
   thật.** XCUITest đi qua hit-testing, gesture và `UIControl` tracking, nên nó
   *kiểm chứng* được những điều mà target/action không kiểm chứng được. Đổi lại:
   nó chạy từ một tiến trình test, cần accessibility được bật, và không hề cố che
   giấu mình. Đó là một đặc tính tốt của công cụ kiểm thử, không phải nhược điểm.

3. **Tương tác vật lý của người dùng là chuẩn tham chiếu, không phải thứ để mô
   phỏng.** Mục tiêu của một harness kiểm thử là *phát hiện* được những lỗi mà
   người dùng thật sẽ gặp (nút bị bàn phím che, phần tử chưa dừng animation,
   màn hình đổi giữa lúc bấm). Mục tiêu đó khác hẳn với việc làm cho automation
   *trông giống* người dùng. Adapter trong `uitest/` theo mục tiêu thứ nhất và
   cố tình không hỗ trợ mục tiêu thứ hai.

## Vị trí của hệ thống hiện tại trên bảng này

| Thao tác trong repo | Lớp tương tác | Tham chiếu |
|---|---|---|
| Bấm nút thường, bấm nút số PIN | UIKit target/action | `agent.js:178`, `agent.js:190` |
| Nhập text vào `UITextField` | API văn bản trực tiếp + tự bắn control event | `agent.js:199–204` |
| Chọn ngày sinh | Set thuộc tính `UIDatePicker` + tự bắn `.valueChanged` | `agent.js:137–138` |
| Chọn nút trên `UIAlertController` của app | Selector riêng tư | `agent.js:120` |
| Từ chối modal xin quyền của iOS | Đọc ivar `_handler` rồi gọi thẳng block | `scripts/airwallet_dom_runner.py:321–326` |

Không có thao tác nào trong repo hiện tại đi qua framework kiểm thử giao diện.
