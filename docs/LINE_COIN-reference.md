# Nguồn tham chiếu LINE_COIN

Đã dùng mã nguồn và ảnh của `LINE_COIN` để tham chiếu luồng đăng ký.
Bản nguồn và manifest được giữ cục bộ trong `reference/LINE_COIN`; không đưa lên GitHub.

Code có 7 trạng thái điều hướng ban đầu và 12 màn đăng ký AirWallet:
tạo tài khoản, số điện thoại, OTP, mật khẩu, họ/tên Kana, ngày sinh, PIN,
nhập lại PIN, Face ID, hoàn tất, mã giới thiệu và thiết lập tài khoản.
Liên kết Recruit ID là nhánh tùy chọn. Cấu trúc, marker và cạnh chuyển trạng thái
được trích từ code vào `agents/registration_reference.json`.

Nguồn dùng Android/Xiaowei. Project mới tiếp tục dùng Frida trên iPhone.
Bản tham chiếu Android không được thực thi; luồng iPhone trong `flow.py` đã được nối riêng.
Một số docstring nguồn vẫn nói chỉ có 3 màn; đặc tả được lấy từ danh sách và
code chạy hiện tại gồm 12 màn. Phần campaign/gift-code Phase 2 không được bật.

## Template dựng lại từ bộ đọc

Người dùng xác nhận file gốc đã mất. Template mới là `data/airwallet_template.xlsx`,
sheet `Inputs`, header ở dòng 1:

| Cột | Cách sử dụng |
| --- | --- |
| email | Trường đầu vào `email` |
| password | Giữ nguyên trường `password`, không gộp với `account_password` |
| name | Giữ nguyên trường `name` |
| account_password | Trường mật khẩu riêng biệt của nguồn |
| phone_number | Số điện thoại Nhật (text, giữ số 0 đầu) |
| kana_sei | Họ Kana (セイ) |
| kana_mei | Tên Kana (メイ) |
| birthday | Ngày sinh YYYY-MM-DD hoặc ô ngày Excel |
| pin | PIN bốn chữ số (text, giữ số 0 đầu) |
| status | Trạng thái xử lý |
| message | Thông báo/kết quả xử lý |

Bốn cột dữ liệu lấy từ `flows/xlsx_data.py:DATA_COLUMNS`, hai cột trạng thái lấy
từ `STATUS_COLUMNS`. Mẫu để trống, không chứa tài khoản ví dụ. 20 dòng được định dạng
để nhập liệu; có thể nhập thêm dòng bên dưới. Số điện thoại, PIN và mật khẩu dùng định dạng text.

Bộ nạp Excel gốc chỉ có bốn cột đầu vào. Khi nối full auto iPhone, đã mở rộng
template và bộ nạp cùng lúc với năm cột phone_number, kana_sei, kana_mei, birthday, pin.
OTP được lấy từ API trong lúc chạy, không lưu vào workbook. account_password được dùng
cho mật khẩu AirWallet theo trường đã kiểm chứng; password vẫn là cột riêng.

Wrapper đã sửa alias để giữ riêng hai mật khẩu, giữ cột `name`, dùng `message`
để ghi kết quả và vẫn đọc được workbook cũ có `error_details`.

Sau khi điền dữ liệu, xuất profile bằng lệnh sau từ thư mục project:

```powershell
python -X utf8 scripts\airwallet_profile_from_excel.py --xlsx data\airwallet_template.xlsx --sheet Inputs --row 2
```

Lệnh chỉ xuất dữ liệu, không điều khiển điện thoại. Template hiện chưa có dòng
dữ liệu nên lệnh chưa có gì để xuất cho đến khi người dùng nhập tài khoản.

## OTP tự động

`src/core/airwallet_otpbase.py` chuyển từ `integrations/otpbase.py`, thay ngoại lệ
Android bằng ngoại lệ cục bộ và bỏ key mặc định trong nguồn. Dùng x-api-key, chốt seq
trước gửi SMS và lọc đúng số điện thoại/mã mới. Người dùng sẽ cung cấp key sau.
Xem README để cấu hình và chạy; không cần gọi Xiaowei trong luồng iPhone.

## Metadata thiết bị và container

Template đã được mở rộng thêm các cột mà wrapper Crane/batch dùng:
`frida_device_id`, `frida_device_name`, `crane_container_id`, `crane_container_name`,
`crane_status`, `crane_assigned_at`, `crane_last_used_at`. Tổng cộng 18 cột, 11 cột đầu
giữ nguyên vị trí. Điền device ID đầy đủ để gán dòng cho máy; các trường Crane còn lại
có thể để trống khi tạo mới. Wrapper tự ghi ID/tên/trạng thái/thời gian của container.
Tên container có thể nhập trước nếu muốn. Xem README để phân biệt chạy trong container
hiện tại với chạy full-flow có quản lý Crane.
