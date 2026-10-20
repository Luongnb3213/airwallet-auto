# Ghép luồng AirWallet với Frida theo từng màn

Ngày 20/10/2026, người dùng đồng ý cùng thao tác và dump khi cần.
Các bước chưa biết sẽ dừng để đối chiếu. `flow.py` đã được nối thành luồng tự động
theo các màn bên dưới; xem README để cấu hình dữ liệu và OTPBase.

## Các màn đã quan sát trực tiếp

| Trạng thái | Controller iOS | Nút chính |
| --- | --- | --- |
| Hướng dẫn | `Sgc.TutorialViewController` | `次へ進む` / `はじめる` |
| Giới thiệu thông báo | `Sgc.PushNotificationAppealViewController` | `次へ進む` |
| Tạo tài khoản mới | `Sgc.AccountCreateViewController` | `アカウントの新規作成` |
| Nhập điện thoại | `Sgc.InputPhoneViewController` | `SMSでの認証へ進む` |
| Xác thực SMS khi đăng ký | `Sgc.SMSValidationRegistrationViewController` | `次へ進む` |
| Đặt mật khẩu đăng nhập | `Sgc.InputPasswordViewController` | `次へ進む` |
| Nhập ngày sinh | `Sgc.InputBirthdayViewController` | `上記に同意して次へ進む` |
| PIN / xác nhận PIN | `Sgc.PasscodeSetupViewController` | Bàn phím số tự chuyển màn |
| Tạo tài khoản thành công | `Sgc.RegistrationCompleteViewController` | `次へ進む` |
| Mã giới thiệu/gift code | `Sgc.RegistrationInputInvitationCodeRouter` | `次へ進む` để bỏ qua |
| Ngân hàng và xác minh danh tính | `Sgc.BankAccountEKYCPromotionViewController` | `あとで設定する` |
| Trang chính | `Sgc.MasterTopViewController` | Dừng luồng đăng ký cơ bản |

Đã kiểm chứng Frida kích hoạt nút tạo tài khoản trên AccountCreateViewController
và app chuyển sang InputPhoneViewController. Form có một UITextField và nút yêu cầu SMS.
Đã nhập số điện thoại theo chỉ định người dùng bằng Frida và đối chiếu khớp chính xác;
người dùng tự yêu cầu SMS. Đã dump màn OTP thực tế: một ô nhập mã, nút gửi lại,
liên kết trợ giúp SMS và nút `次へ進む`. Bộ chẩn đoán nhận diện `SMS_CODE_SCREEN`.
Đã nhập OTP do người dùng cung cấp, đối chiếu khớp ngay trong bộ nhớ và bấm
`次へ進む`; app chuyển thành công sang `Sgc.InputPasswordViewController`.
Không lưu giá trị OTP vào file. Màn mật khẩu có một ô secure, nút hiện mật khẩu
và nút tiếp tục. Nội dung màn hình yêu cầu 6–20 ký tự half-width, kết hợp chữ Latin
và số; mật khẩu này dùng cho đăng nhập.

Sau khi người dùng cung cấp ảnh dữ liệu và yêu cầu tự điền/bấm tiếp:
- Mật khẩu và hai trường Kana được nhập bằng UIKeyInput và đối chiếu khớp trong bộ nhớ.
- Ngày sinh dùng `UIDatePicker`, tạo NSDate theo calendar của picker, gửi
  UIControlEventValueChanged, rồi đối chiếu năm/tháng/ngày và field đã cập nhật.
- PIN được nhập qua các nút số, target `Sgc.PasscodeView`, action `numpadButtonTapped:`.
  Cùng controller dùng title `暗証番号の入力` và `暗証番号の再入力` để phân biệt hai lượt.
- Modal Touch ID xuất hiện; người dùng xác nhận chọn `利用しない`.
  Agent chọn đúng UIAlertAction sau khi đối chiếu nguyên message và nhãn nút,
  dùng UIKit `- _dismissWithAction:` để thực thi action và đóng modal.
- App chuyển sang RegistrationCompleteViewController, hiển thị rõ tài khoản đã tạo
  và số thẻ Visa ảo đã phát hành.
- Theo yêu cầu tiếp tục của người dùng, đã bấm Next, bỏ qua mã giới thiệu,
  chọn thiết lập ngân hàng/xác minh sau và chọn `今は連携しない` ở panel Recruit ID.
- Đã đến `Sgc.MasterTopViewController`: đăng ký cơ bản kết thúc. Phần liên kết
  ngân hàng và xác minh danh tính vẫn để sau, đúng lựa chọn đã thực hiện.

Không lưu mật khẩu, OTP hay PIN vào mã nguồn/dump. Không thực hiện nạp/rút/chuyển tiền.

Panel Recruit ID thực tế nằm sau màn thiết lập ngân hàng trong bản iPhone này,
khác vị trí nhánh tùy chọn trong code Android. Button thiết lập sau dùng
`あとで設定する`, khác nhãn `後で設定する` ở nguồn tham chiếu.
Không nhận diện trang chính bằng riêng chữ `設定` hay `生年月日`: thông báo trên
trang chính có cả các từ này. Ưu tiên controller đã quan sát trực tiếp.

## Dump

```powershell
python -X utf8 main.py dump --label 02-phone-input
```

Dump là thao tác đọc, lưu JSON và TXT vào `dumps/`, gồm controller, cây view,
placeholder và trạng thái có dữ liệu của field, nút và target/selector của nút,
alert, nhận diện màn và các điều kiện lỗi đã biết.
Không đọc nội dung bên trong UITextField. Snapshot vẫn giữ chữ hiển thị và thông báo lỗi
trên màn hình để đối chiếu; không đưa dump lên kho công khai.

## Lỗi lấy từ LINE_COIN

- `phone_already_used`: có chuỗi `この電話番号はすでに他のアカウントで`.
- `service_unavailable`: có chuỗi `現在サービスをご利用いただけません`.
  Nguồn gợi ý kiểm tra proxy Nhật; riêng chuỗi này chưa chứng minh nguyên nhân.
- `account_created_card_notice`: có thông báo `アカウントが作成されました`
  kèm marker bảo trì hoặc không cấp được số thẻ. Không suy ra đã tạo tài khoản chỉ từ chữ bảo trì.
  Thông báo cấp thẻ thành công không bị xếp vào điều kiện này, dù có chữ Visa.
- Alert lạ giữ nguyên dưới dạng `unmapped_alert`. Mã có nhãn rõ như `エラーコード`
  hoặc `error code` được ghi lại nguyên dạng, chưa tự gán ý nghĩa.

Đây là các tên phân loại nội bộ của project, không phải mã lỗi chính thức của AirWallet.
`10000` và `10001` trong nguồn là mã phản hồi API Xiaowei; không dùng chúng làm mã lỗi ví.
Chẩn đoán không tự đóng alert, đổi proxy, gửi lại OTP hay chạy bước tiếp theo.
