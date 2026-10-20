# Báo cáo rò rỉ dữ liệu nhạy cảm

Phạm vi: `C:\Users\admin\Desktop\HuyGT\airwallet-auto-main`
Ngày: 20/10/2026 · Loại kiểm tra: **chỉ đọc**, không sửa, không xoá, không gọi
ra ngoài mạng.

Báo cáo này **không chứa giá trị secret**. Mỗi phát hiện chỉ ghi đường dẫn, loại
secret, và khi cần thì dấu nhận dạng dạng `len=<độ dài> sha256=<8 ký tự đầu>`.

---

## 0. Tóm tắt

| Phân loại | Số lượng |
| --- | --- |
| CONFIRMED EXFILTRATION | **0** |
| SECRET EXPOSED | **2** (E1, E2) |
| SECRET STORED INSECURELY | **4** (S1–S4) |
| UNKNOWN | **3** (U1–U3) |

Kết luận ngắn: **bản checkout này không chứa secret thật nào.** Không có
`config.json`, không có workbook `.xlsx`, không có `dumps/`, không có
`agents/runtime/`, và **không phải một git repository** — nên không có lịch sử
commit nào để secret có thể nằm trong đó. Các phát hiện dưới đây là về **cơ chế
trong code**: những chỗ code *sẽ* ghi secret ra đĩa, và hai chỗ secret có thể
tồn tại lâu hơn dự kiến hoặc lọt vào log.

---

## 1. Hiện trạng checkout

Đã kiểm tra, không tìm thấy file nào:

```
git repository          : KHÔNG (fatal: not a git repository; không có .git/)
config.json             : KHÔNG (chỉ có config.example.json)
*.xlsx / *.xls / *.csv  : KHÔNG có file nào trong cây
dumps/                  : KHÔNG tồn tại
agents/runtime/         : KHÔNG tồn tại
logs/ · *.log           : KHÔNG tồn tại
*.env · *.pem · *.key   : KHÔNG có file nào
data/                   : chỉ data/README.md (298 bytes)
```

Toàn bộ file không phải source code trong cây: `.gitattributes`, `.gitignore`,
`agents/registration_reference.json`, `config.example.json`, `requirements.txt`,
`run.ps1`, `uitest/authorized_targets.example.json`. Đã quét cả 7 file — không
có giá trị secret nào, chỉ có placeholder.

### Quét tự động

Scanner (chỉ in giá trị đã che) tìm thấy 141 vị trí "giống assignment secret".
Sau khi loại trừ:

- **Toàn bộ hit trong `src/config.py`** là false positive: regex bắt phải chuỗi
  `str(_get(` / `os.getenv`. Các biến `SMS_API_KEY`, `CATCHALL_PASSWORD`,
  `OTP_WEB_API_KEY`, `DEFAULT_PASSWORD`… đều đọc từ config/env với default rỗng
  (`src/config.py:85–137`). **Đây là cách làm đúng.**
- Hit trong `tests/` là fixture (`tests/test_wrapper.py`,
  `tests/test_uitest_*.py`): dữ liệu giả, đúng chỗ.
- Hit trong `uitest/` là tên biến (`self._secrets`, `token`), không phải giá trị.
- Hit trong `flow.py`, `diagnostics.py` là chuỗi tiếng Nhật trên giao diện
  (`PIN_TITLE`) và tên class — không phải secret.

**Không có secret hard-code nào trong source code.**

---

## 2. SECRET EXPOSED

### E1 — PIN và mật khẩu tài khoản sót lại trên đĩa sau khi watchdog SIGKILL

| | |
| --- | --- |
| **Loại secret** | `pin` (4 chữ số), `account_password`, `phone_number` |
| **Đường dẫn** | `agents/runtime/input_<device>_<row>_<pid>.json` |
| **Mức rủi ro** | **CAO** |
| **Trạng thái hiện tại** | Không có file nào tồn tại trong checkout này (cơ chế chưa từng chạy ở đây hoặc đã dọn sạch) |

Cơ chế:

1. `scripts/airwallet_full_flow.py:226` ghi **toàn bộ** profile — gồm `pin` và
   `account_password` dạng plaintext — ra `agents/runtime/input_*.json`.
2. Việc dọn file chỉ nằm ở `finally` (`scripts/airwallet_full_flow.py:262–264`).
3. `scripts/airwallet_batch_flow.py:528–537` dùng watchdog: khi một row im lặng
   quá `--row-timeout-sec`, nó gọi `os.killpg(os.getpgid(p.pid), signal.SIGKILL)`
   để diệt cả cây tiến trình.

**SIGKILL không thể bắt được**, nên `finally` ở bước 2 **không bao giờ chạy**.
Mỗi row bị watchdog diệt sẽ để lại một file PIN plaintext tồn tại vô thời hạn.
Vì watchdog chỉ kích hoạt khi row treo, các file sót lại tương ứng với đúng những
lần chạy lỗi — tức là tích luỹ dần mà không ai để ý.

Phát hiện phụ: file được tạo bằng `Path.write_text()`, tức dùng quyền mặc định
(0644 trên POSIX; trên Windows kế thừa ACL của thư mục) — đọc được bởi mọi user
trên máy.

**Phát hiện thêm khi đọc kỹ:** file này là **không cần thiết**.
`scripts/airwallet_full_flow.py:235` gọi `run_with_frida(parsed_dom_args)`
**trong cùng tiến trình**, và `run_with_frida` lại `load_profile(args.profile_json)`
đọc chính file vừa ghi. Secret được ghi ra đĩa chỉ để tiến trình đó đọc lại ngay.

### E2 — Thiếu redaction ở hai exception handler ngoài cùng

| | |
| --- | --- |
| **Loại secret** | Bất kỳ giá trị nào xuất hiện trong message của exception |
| **Đường dẫn** | `main.py:110–112`, `scripts/airwallet_dom_runner.py:742–744` |
| **Mức rủi ro** | **TRUNG BÌNH** |

`flow.py:299–302` và `scripts/airwallet_full_flow.py:253–257` đều có redaction
cho `account_password`, `password`, `pin`, `otp`. Nhưng:

- `main.py:110–112`: `print('[airwallet] ' + (str(error) or 'Đã dừng.'))` —
  **không redact**. Handler này bắt mọi exception phát sinh *ngoài* `run_signup`:
  `prepare_profile_proxy`, `build_otp_client`, `select_device_id`, đọc Excel.
- `scripts/airwallet_dom_runner.py:742–744`: `print(f"[airwallet-dom] {exc}")` —
  **không redact**, và đây là đường vào mà batch dùng.

Rủi ro thực tế: exception của thư viện bên thứ ba thường nhắc lại input. Ví dụ
một lỗi HTTP có thể in URL kèm API key trong query string, hoặc openpyxl in nội
dung ô. Những dòng này đi vào stdout/stderr, rồi được `airwallet_batch_flow.py`
thu lại và `log()` ra file.

---

## 3. SECRET STORED INSECURELY

Đây là các cơ chế *theo thiết kế*, không phải lỗi, nhưng là lưu trữ không an toàn.

| ID | Secret | Vị trí | Rủi ro | Ghi chú |
| --- | --- | --- | --- | --- |
| **S1** | API key OTPBase | `config.json` → `otpbase_api_key` (`flow.py:33–38`); hoặc env `AIRWALLET_OTPBASE_API_KEY` | TRUNG BÌNH | Plaintext. Đã có trong `.gitignore`. Đường env là lựa chọn tốt hơn và đã được hỗ trợ. |
| **S2** | `pin`, `account_password`, `phone_number` | Workbook Excel, sheet `Inputs` (`scripts/airwallet_profile_from_excel.py`) | **CAO** | Plaintext, không mã hoá, mở bằng Excel là đọc được. PIN tài chính 4 số nằm cùng số điện thoại. |
| **S3** | Proxy `user:password` | Workbook Excel, sheet `Proxies`, cột `proxy` dạng `host:port:user:pass` (`src/core/airwallet_proxy.py:16–17`) | TRUNG BÌNH | Plaintext. |
| **S4** | `pin`, `account_password` | `agents/runtime/current_profile.json` (`scripts/airwallet_profile_from_excel.py:197–199`, mặc định `--out`) | **CAO** | File **thường trú**, không phải tạm: không có cơ chế dọn nào. Cũng là default `--profile-json` của `airwallet_dom_runner.py`. |

### Điểm làm đúng, cần ghi nhận

- `src/core/airwallet_proxy.py:51` — comment *"Never include credentials in
  validation errors"*, và `parse_proxy()` được gọi chỉ để validate, không đưa
  vào message lỗi.
- `src/core/airwallet_proxy.py:156` — giá trị trả về chỉ gồm `proxy_id` và
  `proxy_used = host:port`, **đã loại bỏ user/password**. Nên dump và log không
  chứa credential proxy.
- `agent.js:78–83` — snapshot chỉ trả `filled: bool` và `secure: bool`, **không
  trả nội dung field**. Đây là lý do `dumps/` không chứa PIN/password/OTP.
- `.gitignore` phủ khá đủ: `config.json`, `agents/runtime/`, `dumps/`, `logs/`,
  `*.xlsx`, `*.csv`, `*.pem`, `*.key`, `/data/*`, `*.sqlite*`.
- `scripts/airwallet_full_flow.py:262–264` *có* chủ ý dọn file tạm — chỉ là
  không chịu được SIGKILL (E1).

---

## 4. CONFIRMED EXFILTRATION

**Không có.**

- Không có git repository → không có commit, không có remote, không có push nào
  để kiểm tra hay để lộ.
- Không tìm thấy file secret nào trong cây để có thể đã bị upload.
- Không có code nào gửi secret tới một endpoint ngoài phạm vi chức năng.

Phân biệt rõ: API key OTPBase **được gửi tới OTPBase** và thông tin proxy **được
gửi tới ProxySwitcher-NG trên thiết bị** — đó là mục đích của chúng, là egress
theo thiết kế, **không phải exfiltration**.

---

## 5. UNKNOWN

| ID | Chưa xác định được | Vì sao |
| --- | --- | --- |
| **U1** | Secret có từng bị commit/push từ **một bản copy khác** của project này hay không | Bản này không phải git repo, nhưng sự tồn tại của `.gitignore` + `.gitattributes` cho thấy project có (hoặc từng có) version control ở nơi khác. Chỉ kiểm tra được trên chính bản copy đó: `git log --all -- config.json data/ agents/runtime/ dumps/` và lịch sử remote. |
| **U2** | Workbook Excel, `dumps/`, `logs/` trên máy vận hành thật đang chứa gì và đã được chia sẻ cho ai | Các file đó không có trong checkout này. |
| **U3** | File `agents/runtime/input_*.json` sót lại từ E1 có từng tồn tại trên máy vận hành hay không | Cần kiểm tra trực tiếp trên máy đó: `ls -la agents/runtime/`. |

---

## 6. Hành động khắc phục

Theo đúng yêu cầu: báo cáo này được lập **trước** khi sửa; không có credential
nào bị tự động thay đổi.

### Sửa trong code (sẽ thực hiện ngay sau báo cáo này)

| ID | Hành động |
| --- | --- |
| E1 | Bỏ hẳn việc ghi profile ra đĩa ở đường `full_flow` — truyền profile trong bộ nhớ, vì cả hai bước nằm trong **cùng một tiến trình**. Thêm bộ dọn file `agents/runtime/input_*.json` cũ khi khởi động, để thu hồi phần đã sót. |
| E2 | Thêm redaction ở `main.py` và `airwallet_dom_runner.py`, dùng một module dùng chung. |
| S4 | Ghi file profile với quyền hạn chế (`0o600` ở POSIX, best-effort ở Windows) và in cảnh báo rằng file chứa PIN plaintext. |

### Cần người vận hành làm (không tự động hoá được)

| ID | Hành động |
| --- | --- |
| U3 | Trên máy vận hành: `ls -la agents/runtime/` — nếu có `input_*.json`, đó là PIN plaintext sót lại; xoá sau khi ghi nhận. |
| U1 | Trên bản copy có git: kiểm tra lịch sử theo lệnh ở U1. Nếu từng commit, phải coi secret đó là **đã lộ** và thay toàn bộ. |
| S1 | Chuyển API key OTPBase sang **chỉ dùng environment variable** (đã hỗ trợ), bỏ khỏi `config.json`. Dài hạn: secret manager (Windows Credential Manager / DPAPI, hoặc 1Password/Vault CLI) thay vì file plaintext. |
| S2, S3 | Workbook chứa PIN tài chính và credential proxy: đặt trên volume có mã hoá (BitLocker), giới hạn ACL về đúng một user (`icacls data /inheritance:r /grant:r "%USERNAME%":(F)`), không đưa vào thư mục đồng bộ cloud. Dài hạn: bỏ cột `pin` khỏi Excel, lấy từ secret manager lúc chạy. |
| S2 | **Thay credential**: mọi `account_password` và `pin` đã nằm plaintext trong Excel nên được coi là đã lộ ở mức "bất kỳ ai đọc được máy/ổ đĩa". |
| — | Đặt thời hạn lưu cho `dumps/`: snapshot không chứa credential, nhưng **có chứa chữ hiển thị trên màn hình** (tên kana, số điện thoại dạng che) — là dữ liệu cá nhân. Nên tự xoá sau 7–14 ngày. |
| — | `.gitignore` nên thêm `/audit/` nếu báo cáo sau này có chứa dữ liệu vận hành. |

---

## 7. Ghi chú về phương pháp

- Scanner: `scan_secrets.py` (trong thư mục tạm của session, không nằm trong repo).
  Chỉ in `path:line`, tên key, và `len`/`sha256[:8]`. Không in giá trị.
- Không chạy lệnh git nào có tác dụng thay đổi; chỉ `git rev-parse` để xác định
  có phải repo.
- Không gửi request nào tới COIN+ hay bất kỳ dịch vụ bên ngoài.
- Không có file nào bị sửa hay xoá trong quá trình kiểm tra. Bản gốc của các file
  sẽ được sửa đã được sao lưu trước khi thay đổi, để xuất diff.
