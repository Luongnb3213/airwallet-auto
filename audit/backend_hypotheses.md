# Giả thuyết nguyên nhân hạn chế tài khoản COIN+

Ngày: 20/10/2026. Đi kèm [`backend_evidence_report.md`](backend_evidence_report.md)
và [`missing_evidence.md`](missing_evidence.md).

Mỗi giả thuyết gắn một mức: **CONFIRMED** (bằng chứng trực tiếp) /
**SUPPORTED HYPOTHESIS** (dữ liệu phù hợp nhưng chưa chứng minh) / **UNKNOWN**
(chưa đủ dữ liệu). Với tình trạng bằng chứng hiện tại (không có dữ liệu server
trong repo), gần như mọi mục là UNKNOWN.

**Nguyên tắc xuyên suốt:** không mục nào dưới đây mô tả, suy đoán hay xếp hạng
thuật toán chống gian lận nội bộ của COIN+. Mục tiêu là biết *cần bằng chứng gì
để xác nhận hoặc loại trừ*, không phải *đoán cơ chế rồi né nó*.

---

## H-A · Server-side restriction (có xảy ra) — **SUPPORTED HYPOTHESIS**

- **Phù hợp:** app hiển thị chuỗi thông báo khóa do server phát ra → trạng thái
  hạn chế đến từ server, không phải lỗi render phía client.
- **Chưa chứng minh:** không có HTTP response/business error code kèm theo trong
  repo; chỉ có quan sát UI.
- **Nâng lên CONFIRMED nếu:** có M2 (dump hiển thị chuỗi khóa + mã lỗi) hoặc M7
  (response đã sanitize do người vận hành xuất hợp lệ), hoặc M5 (Support xác nhận).
- **Lưu ý:** "có hạn chế" ≠ "biết vì sao bị hạn chế". Xem H-cause.

## H-cause · Lý do server đưa ra quyết định — **UNKNOWN**

- Không có dữ liệu nào ở phía project cho biết quy tắc nào kích hoạt.
- **Chỉ xác nhận được qua:** COIN+ Support (M5). Không suy ra được từ code,
  dump, hay tương quan — và việc cố suy ra "ngưỡng" nằm ngoài phạm vi.

## H-D · Campaign eligibility review — **UNKNOWN**

- **Cần để xét:** M6 (campaign terms) + M3 (giờ cấp thưởng) + M5.
- **Phù hợp một phần với bối cảnh:** hạn chế xảy ra *sau khi nhận thưởng, vài giờ
  đến vài ngày* — khớp về mặt thời gian với một đợt rà soát hậu-thưởng, nhưng
  "khớp thời gian" không phải bằng chứng nhân quả. Nhiều quá trình khác cũng tạo
  độ trễ tương tự.
- **Loại trừ nếu:** terms cho thấy điều kiện đã được đáp ứng và Support nói lý do
  khác.

## H-B · Client-side application / session error — **UNKNOWN (ít phù hợp)**

- **Ít phù hợp:** bối cảnh mô tả nhiều tài khoản bị hạn chế **đồng loạt** và
  trạng thái do server phát ra; một lỗi session/app phía client thường không gây
  khóa đồng loạt từ server.
- **Kiểm tra bằng:** M2 (dump quanh thời điểm lỗi) — nếu thấy lỗi client (crash,
  session rớt) *trước* khi có thông báo khóa thì mới cân nhắc.
- Ghi chú: các defect client đã tìm thấy và sửa trong session này (xem
  `FIX_REPORT.md` F1–F10) **không** tạo được hạn chế đồng loạt phía server.

## H-C · Network / authentication error — **UNKNOWN (ít phù hợp)**

- Lỗi mạng/xác thực thường biểu hiện tức thời, không phải "khóa sau vài giờ/ngày".
- **Kiểm tra bằng:** log có lỗi mạng/HTTP 401/403/timeout quanh thời điểm —
  nhưng repo không ghi HTTP của COIN+, nên không có dữ liệu.
- Nhắc: HTTP 403/429 (nếu từng thấy) **không** mặc nhiên là "tài khoản bị khóa".

## H-E · Insufficient evidence — **CONFIRMED (về tình trạng dữ liệu)**

- Đây là mô tả đúng nhất hiện tại: không đủ dữ liệu để phân biệt H-A/B/C/D ở mức
  nguyên nhân. Chỉ H-A (có hạn chế, nguồn server) là SUPPORTED.

---

## Bước tiếp theo hợp lệ (xếp theo giá trị)

1. **M5 — Hỏi COIN+ Support** bằng danh tính chủ tài khoản. Là nguồn duy nhất
   cho H-cause và H-D. (Cân nhắc phần phạm vi pháp lý/điều khoản trong
   `docs/ui-automation-audit.md` §6.1 trước khi làm.)
2. **M2 + M4** — Lấy dump và log từ máy vận hành, điền `account_timeline.csv` với
   kỷ luật event-vs-observed-vs-estimated, quy về JST.
3. **M6** — Đọc campaign terms, đối chiếu điều kiện.
4. **M1/M3** — Screenshot khóa + giờ cấp thưởng, để đo khoảng "thưởng → hạn chế"
   (mô tả hiện tượng, **không** để suy ngưỡng phát hiện).

Không có bước nào yêu cầu đăng ký tài khoản mới, gọi endpoint chưa cấp phép,
replay request, hay dựng MITM/hook pinning.
