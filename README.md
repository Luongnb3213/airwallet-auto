# AirWallet Auto

Đăng ký AirWallet trên iPhone qua Frida, dùng dữ liệu Excel, Crane và ProxySwitcher-NG.
Luồng iOS đã được kiểm chứng trên AirWallet 1.77.0 (`jp.coinplus.app`). Luồng dừng ở
trang chủ sau đăng ký cơ bản; ngân hàng, KYC và liên kết Recruit ID được để sau.

## Chuẩn bị

Cần Python, iPhone mở khóa với Frida hoạt động, AirWallet, Crane,
ProxySwitcher-NG và PowerSelector cho chức năng respring.

Từ thư mục project, chạy PowerShell:

```powershell
python -m pip install -r requirements.txt
Copy-Item config.example.json config.json
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
python -X utf8 scripts/create_template.py
$env:AIRWALLET_OTPBASE_API_KEY = 'YOUR_OTPBASE_KEY'
python -X utf8 main.py devices
```

Lệnh tạo template sinh file trống `data/airwallet_template.xlsx` với hai sheet
`Inputs` và `Proxies`, giữ nguyên file đã có. Đặt `xlsx_path` trong cấu hình hoặc
truyền `--xlsx` khi dùng workbook khác. Khóa OTP cũng có thể đặt cục bộ trong
`config.json` qua trường `otpbase_api_key`.

## Dữ liệu đầu vào

| Cột Inputs | Nội dung |
| --- | --- |
| `phone_number` | Số Nhật dạng text, giữ số 0 đầu; chấp nhận `+81` |
| `account_password` | 6–20 ký tự half-width, có chữ Latin và số |
| `kana_sei`, `kana_mei` | Họ và tên Kana |
| `birthday` | `YYYY-MM-DD` hoặc `YYYY/MM/DD` |
| `pin` | Bốn chữ số dạng text, giữ số 0 đầu |
| `frida_device_id` | UDID đầy đủ của máy chạy dòng này; bắt buộc khi batch |
| `crane_container_id` | Để trống để tạo mới; ID có sẵn dùng để tiếp tục container đó |
| `crane_container_name` | Tên tùy chọn; container mới mặc định lấy số điện thoại |
| `status`, `message` | Kết quả và lỗi được chương trình ghi lại |
| `proxy_id`, `proxy_used` | Proxy đã gán; chương trình quản lý theo tài khoản |

`email`, `password`, `name` là các cột tương thích nguồn cũ. `password` khác
`account_password`. Các cột metadata Crane và thiết bị còn lại được tự ghi khi chạy.

Trong `Proxies`, nhập `proxy` theo dạng `host:port:user:password`, đặt `status`
là `ACTIVE`. Mỗi tài khoản dùng một proxy riêng. Chương trình ghi `proxy_id`, chủ sở
hữu và IP đã kiểm tra. Khi tiếp tục một tài khoản, proxy đã gán được giữ nguyên.
Hết proxy hoặc kiểm tra proxy thất bại thì dòng đó dừng.

## Chạy

Một dòng, tự chuẩn bị proxy và container:

```powershell
python -X utf8 scripts/airwallet_full_flow.py --xlsx data/airwallet_template.xlsx --row 2
```

Batch theo các thiết bị đã gán trong Excel:

```powershell
python -X utf8 scripts/airwallet_batch_flow.py --xlsx data/airwallet_template.xlsx --device-id all --limit 30
```

Batch chỉ chạy dòng gán đúng thiết bị đang kết nối, mặc định một lần thử mỗi dòng.
`--limit` được áp dụng trước bước định tuyến thiết bị, nên các dòng chưa gán máy
có thể chiếm hạn mức. Gán máy cho các dòng cần chạy; `--limit 0` chọn tất cả các dòng
đủ trạng thái. Các dòng `SUCCESS` và `FAIL_NO_RETRY` được bỏ qua.

Luồng từ tutorial đi qua SMS, mật khẩu, Kana, ngày sinh, PIN rồi đến trang chủ.
Quyền thông báo và theo dõi được từ chối tự động bằng các nhãn đã nhận diện ở
tiếng Việt, Anh và Nhật. Màn hoặc alert chưa biết được giữ nguyên để kiểm tra.
`already_home` không được tính là một tài khoản vừa tạo thành công.

Lệnh kiểm tra mặc định chỉ đọc màn hình:

```powershell
python -X utf8 main.py
python -X utf8 main.py snapshot --device-id DEVICE_ID
python -X utf8 main.py dump --device-id DEVICE_ID --label current-screen
```

`main.py signup` chạy trong container hiện tại. Full-flow và batch quản lý
container qua Crane. Thêm `--no-container` vào full-flow để tiếp tục container
đang mở. Container cũ giữ tên đã có.

## OTP và lỗi

OTPBase dùng `https://otpbase.site/api/v1/otp`. Trước khi gửi SMS, chương trình lấy
mốc `seq`; chỉ nhận OTP mới hơn mốc và đúng số điện thoại. Mặc định chờ 120 giây,
poll mỗi 2 giây. Hết thời gian trả `otp_timeout`, ghi thông báo `[OTP_TIMEOUT]`
vào Excel. Chương trình không tự gửi lại SMS.

Dump kết quả giữ cursor và dấu đối chiếu số điện thoại để tiếp tục. Lệnh sau
tiếp tục luồng trong container hiện tại:

```powershell
python -X utf8 main.py signup --xlsx data/airwallet_template.xlsx --row 2 --resume-dump dumps/result.json
```

Các dump, log, profile runtime, dữ liệu Excel và `config.json` chỉ lưu cục bộ,
được bỏ qua bởi Git. Dump có thể chứa chữ trên màn hình. Thư mục `reference/`
và ghi chú vận hành cục bộ cũng không được đưa lên repo.

## Kiểm chứng

```powershell
python -X utf8 -m unittest discover -s tests -v
```

Các test dùng dữ liệu giả và thư mục tạm, không cần workbook tài khoản thật.
Test chính sách modal dùng Node.js nếu có. Chi tiết các màn đã ghép luồng nằm
trong `docs/guided-mapping.md` và `docs/LINE_COIN-reference.md`.
