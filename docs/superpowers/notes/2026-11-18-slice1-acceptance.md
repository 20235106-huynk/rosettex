# Biên bản nghiệm thu lát 1

Ngày: 2026-11-18. Máy dev Linux (Fedora), CPU, RAM 31 GB, MinerU 4.2.3, Tectonic 0.17.0, tier `standard`.

**Giới hạn của lần nghiệm thu này:** "agent thật" là subagent của Claude Code (model Sonnet), không phải một phiên Claude Code mới do người dùng gõ một câu. Subagent chỉ được dùng skill `rosettex` và đầu ra của lệnh, không được hỏi lại người dùng. Chỉ chạy trên Linux; macOS, Windows và CI chưa được kiểm (chưa có remote).

## Kết quả

| Bước | Kết quả |
|---|---|
| 1. Cài như người dùng (`uv tool install --editable .`, venv MinerU 4.2.3 ở `~/.rosettex/mineru-venv`, 657 MB) | Đạt. `mineru-kit models verify --tier standard`: cả hai mô hình `ok`. Mô hình dùng chung `~/.mineru/models` (2 GB), venv không có bản riêng. |
| 2. Đầu-cuối MinerU thật, người dịch giả (`ROSETTEX_REAL=1`, `short` và `long`) | Đạt: 2 passed trong 614 s (10 phút 14 giây). |
| 3. Agent dịch `short.pdf` | PDF ra (2 trang), postcheck ok, không fallback, không flagged. Agent không hỏi lại câu nào, 12 lệnh Bash. Trang 1 và 2 đã được người kiểm tra bằng mắt: tiêu đề, bảng, hình, công thức đánh số, chú thích chân trang, số liệu (28.4, 2.0, 4.5, p < 0.05) đều giữ đúng. Một lỗi dịch nhỏ: thiếu dấu chấm trước "Theo [1, 2]". |
| 4. Agent dịch `long.pdf`, đo tỉ lệ từ chối ở lần nộp đầu | PDF ra (19 trang, gốc 20), postcheck ok, `length_ratio` 0,981, `fallback` và `flagged` rỗng. Agent không hỏi lại câu nào, khoảng 27 lệnh Bash. 20 lô, **0 lô bị từ chối ở lần nộp đầu** (lệnh đo trong kế hoạch, chạy trên `logs/accept.jsonl`). Nội dung trang 20 gốc (Bảng 28, Hình 28) có trong trang 19 của bản dịch. |

Số liệu cho SG 16 mục 7: tỉ lệ lô bị từ chối ở lần nộp đầu = 0/20 với `long.pdf` và 0/1 với `short.pdf`. Mẫu nhỏ, một agent, một lần chạy.

## Vấn đề gặp phải

| # | Vấn đề | Lát xử lý |
|---|---|---|
| 1 | Giao thức bảo "chờ 30–60 giây rồi chạy `status`" nhưng không nói cách chờ khi harness chặn `sleep` đứng trước lệnh; cả hai agent phải tự viết vòng chờ `until`. | Lát 1 (sửa `protocol.md`) |
| 2 | `status` ngay sau `extract` báo "0/0 nhóm trang", dễ hiểu nhầm. | Lát 1 |
| 3 | Agent bài `long` dùng script riêng và chuyển hướng `rosettex next ... > n.json` để tiết kiệm lệnh, trái quy tắc "một lệnh mỗi lần". Đầu ra của `next` lớn; cân nhắc `next --out <file>`. | Lát 2 |
| 4 | `report.json` ghi `unreviewed: true`: công thức và bảng chưa được đối chiếu với bản gốc. | Lát 2 (`review`) |
| 5 | Bài `long` đã chạy 12 phút liên tục (mỗi nhóm trang MinerU khoảng 3 phút) mà không thấy `*_dead` giả. Tuy nhiên `status` ghi bộ đếm `retries` vào `progress.json` khi báo tác vụ chết (xem sổ rulings), cần xác nhận trên CPU chậm hơn. | Lát 3 |
| 6 | Chưa kiểm: Windows (tiến trình tách rời có thể chết theo job object của harness), macOS, CI ba hệ điều hành. | Lát 3 |
| 7 | Dịch thuật: thiếu dấu chấm trước "Theo [1, 2]" ở `short`; "Long paragraph to cross columns" dịch nghĩa đen ở `long` (đoạn thử của fixture). Lỗi của người dịch, không phải của CLI. | Không cần xử lý |
| 8 | Các điểm nhỏ còn để lại từ review từng task (xem sổ tiến độ SDD): ví dụ tiêu đề tài liệu tham khảo in "Tài liệu" thay vì "Tài liệu tham khảo", `check_terms` chưa kiểm hết kiểu, `get_tectonic` chưa thử lại khi mạng lỗi, `.done` thiếu thư mục kết quả không được chạy lại. | Lát 1–3 tùy mục |

## Trạng thái tiêu chí nghiệm thu (SG 14) kiểm được ở lát 1

- Tiêu chí 1 (đầu-cuối cho PDF có lớp chữ, người dịch giả): **đạt** (bước 2).
- Tiêu chí 2 (agent thật dịch `short.pdf` không hỏi lại): **đạt một phần**, vì dùng subagent thay vì phiên mới (bước 3).
- Tiêu chí 4 (tỉ lệ lô bị từ chối ở lần nộp đầu): **đo được 0/21**, mẫu nhỏ (bước 4).
- Chạy trên 3 hệ điều hành: **chưa kiểm**; CI đã viết nhưng chưa chạy.
- Các tiêu chí về `review`, `setup`/`doctor`, GPU, OCR: thuộc lát 2–4.
