# rosettex — giao thức dịch bài báo PDF

Bạn (agent) là người dịch. `rosettex` tách nội dung, chia lô, kiểm tra và dựng PDF. Quy tắc chung:
- Mỗi lần chỉ chạy **một** lệnh `rosettex …`, luôn thêm `--json`. Không nối lệnh, không dùng ống dẫn.
- Mọi kết quả đều có `next_action`: đó là lệnh nên chạy tiếp theo.
- Lệnh trả mã 2 là lỗi môi trường hoặc sử dụng: báo người dùng nội dung `error` và `next_action`, rồi dừng.
- `next_action` là null kèm `error` → báo lỗi cho người dùng và dừng.

## 0. Trước khi chạy
- Cần đủ ba thông tin: file PDF, ngôn ngữ đích, nơi lưu (thư mục hoặc file .pdf). Thiếu ngôn ngữ hoặc nơi lưu thì hỏi người dùng **một câu gộp** (ví dụ "Bạn muốn dịch sang ngôn ngữ nào, và lưu kết quả ở đâu?"; thiếu một thì chỉ hỏi cái thiếu), rồi dừng chờ trả lời.
- Không chạy được lệnh `rosettex` thì báo người dùng cài theo README của rosettex.

## 1. Tách nội dung
Chạy `rosettex extract "<pdf>" --lang <mã> --out "<nơi lưu>" --json`.
- `output_exists: true`: hỏi người dùng có ghi đè file kết quả không, và nhớ câu trả lời cho bước 5.
- `lang_tested: false`: báo người dùng "ngôn ngữ này chưa được kiểm thử".
- Ghi lại `work` (thư mục làm việc).

Sau đó cứ 30–60 giây chạy `rosettex status "<work>" --json` một lần, cho tới khi `state` không còn là `extract_running`. Báo người dùng tiến độ ngắn gọn theo `step`.

## 2. Thuật ngữ
Chạy `rosettex next "<work>" --outline --json` để đọc tiêu đề, tóm tắt và các tiêu đề mục. Ghi một file JSON:
`{"topic": "<một câu>", "terms": [{"src": "<thuật ngữ gốc>", "dst": "<bản dịch>"}], "keep": ["<giữ nguyên>"], "abbreviations": [{"abbr": "<viết tắt>", "expansion": "<dạng đầy đủ>"}]}`
rồi chạy `rosettex terms "<work>" --file "<file>" --json`.

## 3. Dịch từng lô
Lặp lại các bước sau:
1. Chạy `rosettex next "<work>" --json`. Nếu nhận `done: true` thì sang bước 4.
2. Dịch mọi `batch.items[].text` theo quy tắc ở cuối tài liệu. Dùng `terms`, `topic`, `section` và `previous` (đoạn cuối lô trước) để giữ mạch văn.
3. Ghi file JSON `{"<id>": "<bản dịch>"}` chứa đủ mọi `id` của lô.
4. Chạy đúng lệnh `rosettex accept …` trong `submit`, thay `<file.json>` bằng file vừa ghi.
5. Nếu nhận `ok: false`, sửa đúng các `id` có trong `errors` rồi nộp lại cả lô.

Thêm:
- Nếu bạn đọc được ảnh, xem `page_images` khi câu gốc khó hiểu.
- Có subagent thì giao mỗi subagent một lô qua `rosettex next "<work>" --batch <id> --json`. Mỗi subagent tự nộp lô của mình.

## 4. Kiểm tra toàn bài
Chạy `rosettex validate "<work>" --json`. Còn lỗi thì nộp lại lô chứa các `id` bị báo, cho tới khi `ok: true`.

## 5. Dựng PDF
Chạy `rosettex render "<work>" --json`. Thêm `--overwrite` nếu ở bước 1 người dùng đã đồng ý ghi đè.

Sau đó cứ 30–60 giây chạy `rosettex status "<work>" --json` một lần, cho tới khi `state` là `done`. Nếu bạn đọc được ảnh hoặc PDF, mở file kết quả và xem trang đầu, một trang có bảng, một trang có công thức.

## 6. Báo kết quả
Đọc file `report.json` (đường dẫn có trong `status`) rồi báo người dùng ngắn gọn:
- đường dẫn file kết quả;
- chủ đề và vài thuật ngữ chính;
- tier đã dùng;
- các mục trong `fallback`, kèm trang: công thức hoặc bảng đang hiển thị dạng mã LaTeX;
- các mục trong `flagged`, kèm trang: công thức đáng ngờ;
- câu "công thức và bảng chưa được đối chiếu với bản gốc";
- các vấn đề trong `postcheck.problems`, nếu có.

## Bị gián đoạn hoặc mất ngữ cảnh
Chạy `rosettex status "<work>" --json` và làm đúng `next_action`. Nếu không nhớ `<work>`, nó nằm ở `<thư mục chứa file kết quả>/.rosettex/<tên file PDF gốc>`.

## Quy tắc dịch
{{RULES}}
