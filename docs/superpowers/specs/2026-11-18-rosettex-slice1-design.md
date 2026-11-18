# Spec lát 1: `rosettex` — luồng đầu-cuối cho PDF có lớp chữ

Ngày: 2026-11-18 · Trạng thái: **chờ người dùng duyệt** · Spec gốc: `2026-11-18-rosettex-design.md` (gọi tắt **SG**)

> Lát này hiện thực một phần của SG. Chỗ nào lát 1 không nói thì SG quyết định. Chỗ nào lát 1 cố ý làm khác hoặc cụ thể hơn SG thì ghi rõ "≠ SG" hoặc "cụ thể hóa SG".

## 1. Mục tiêu của lát

Một agent trong **bất kỳ harness nào có shell và đọc/ghi file**, trên **Linux, macOS hoặc Windows**, nhận một câu lệnh "dịch bài báo X sang tiếng Việt, lưu ở Y" và trả về PDF tiếng Việt cho **bài báo có lớp chữ**. PDF đầu ra có công thức và bảng là ký tự thật, liên kết nội bộ bấm được, và qua được kiểm tra sau dựng (SG 11.3).

**Thành công khi:**
1. Hai bài mẫu `tests/fixtures/papers/short.pdf` và `long.pdf` (nguồn `.tex` nằm cùng thư mục) đi hết luồng `extract → terms → next/accept → validate → render` mà không cần sửa tay. Có thể chạy tự động với một "người dịch giả" trong kiểm thử, hoặc chạy bởi agent thật.
2. Agent thật (Claude Code) dịch trọn `short.pdf` chỉ từ một câu lệnh, chỉ dựa vào hướng dẫn in ra bởi `rosettex guide`.
3. Kiểm thử đơn vị và kiểm thử dựng PDF (không cần MinerU) chạy xanh trên Linux, macOS và Windows.
4. Đo được mục 7 của SG 16: tỉ lệ lô bị `accept` từ chối ở lần nộp đầu, trên `long.pdf`.

## 2. Phạm vi

**Trong lát 1:**
- `extract`, `status`, `terms`, `next`, `accept`, `validate`, `render`, `guide`, `clean`.
- Bộ chuyển đổi MinerU (`docvortex.middle 2.0`, đầu ra zip) → `document.json`.
- Gộp đoạn, placeholder, liên kết nội bộ, danh sách giữ nguyên tự động, chữ trong ô bảng.
- Phân tích bảng HTML → `tabular`. Phương án cuối (SG 10) cho công thức/bảng không biên dịch được.
- Gói ngôn ngữ `vi` và gói `default`.
- Giao thức một nguồn → `rosettex guide`, `SKILL.md`, `AGENTS.md`.

**Ngoài lát 1** (để các lát sau; lát 1 phải để sẵn chỗ nối nhưng không làm trước):
- `review` (lát 2).
- `setup`, `doctor`, tự tải mô hình/Tectonic/font, `install-skill`, tự quay về CPU khi GPU lỗi (lát 3).
- Trang scan và OCR (lát 4).
- HTML (giai đoạn 2).

**Điều kiện tiên quyết** (lát 1 không tự cài; thiếu thì báo lỗi rõ ràng và nói cách cài thủ công):

| Thứ cần | Tìm theo thứ tự |
|---|---|
| MinerU 4.x | biến `ROSETTEX_MINERU` (đường dẫn tới `mineru-kit`), `~/.rosettex/mineru-venv/{bin,Scripts}/mineru-kit`, `mineru-kit` trên `PATH` |
| Mô hình của tier đã chọn | `mineru-kit models verify --tier <tier>` đạt |
| LaTeX | biến `ROSETTEX_TEX`, `~/.rosettex/bin/tectonic[.exe]`, `tectonic` trên `PATH`, `xelatex` trên `PATH` |

Biến `ROSETTEX_*` là thứ **người dùng** đặt một lần khi cài, không phải agent đặt khi chạy, nên không vi phạm SG 3.1.

## 3. Ràng buộc đa nền tảng áp dụng ở lát này

Toàn bộ SG 3.1 áp dụng. Cụ thể cho lát 1:

- **Phụ thuộc runtime:** chỉ `pypdfium2`. Thư viện chuẩn lo phần còn lại: `argparse`, `json`, `html.parser`, `unicodedata`, `subprocess`, `zipfile`, `re`, `pathlib`. Không dùng PyMuPDF (AGPL). `pypdfium2` dùng để đếm trang, trích chữ từ PDF đầu ra cho kiểm tra sau dựng, và kết xuất ảnh trang cho `next`.
- **Python:** `>=3.11`. Cài bằng `uv tool install` hoặc `pipx install`, hoặc chạy `python -m rosettex` từ mã nguồn.
- **Chạy nền:** `rosettex extract` và `rosettex render` tự sinh tiến trình con tách rời (SG 3.1) rồi trả về ngay. Tiến trình con ghi nhịp tim mỗi ≤ 10 giây. `status` coi tác vụ là **chết** khi nhịp tim cũ hơn 60 giây và trạng thái chưa là `done`/`failed`. Cờ `--foreground` chạy đồng bộ, dùng cho kiểm thử và gỡ lỗi. `status` **chỉ đọc**, không ghi file nào. Bộ đếm `retries` trong `progress.json` tăng khi tiến trình chạy tiếp thật sự khởi động sau một lần chết; tác vụ chết lần hai, hoặc lỗi không lường trước trong tiến trình con, thì `status` trả `next_action: null` kèm `error` và đường dẫn log (agent báo người dùng rồi dừng). Mỗi lần gọi MinerU hoặc TeX có giới hạn tổng thời gian (MinerU: 900 giây cộng 120 giây mỗi trang của nhóm; TeX: 300 giây mỗi lần chạy; nhân với biến môi trường `ROSETTEX_TIMEOUT_SCALE`). Quá hạn thì tiến trình con bị giết và tác vụ chuyển sang `failed`.
- **Gọi MinerU và LaTeX:** chép PDF nguồn vào `work/src.pdf` (tên ASCII), chạy với `cwd=work/…` và tên file tương đối. Truyền cấu hình MinerU qua biến môi trường của tiến trình con (SG 8.1), không đọc hay ghi `~/.mineru/config.yaml`.

## 4. Thư mục làm việc

Mặc định `<thư-mục-kết-quả>/.rosettex/<tên-gốc>.<mã-ngôn-ngữ>/` (có mã ngôn ngữ để dịch cùng một PDF sang nhiều ngôn ngữ không dùng chung thư mục), đổi bằng `-o`. `manifest.json` mang dấu `"schema": "rosettex.work/1"`; `clean`, `extract`, `status` và `render` chỉ nhận thư mục có manifest hợp lệ. `extract` từ chối (mã 2) thư mục đã có nội dung mà không phải thư mục làm việc của rosettex, và từ chối thư mục làm việc có `--lang` khác với manifest. Thư mục rỗng hoặc chưa có thì tự tạo. Thư mục chưa có manifest chỉ được nhận lại (và `clean` chỉ dọn) khi có `src.pdf` và mọi mục đều thuộc rosettex hoặc là file tạm `.*.tmp`: đó là dấu vết của một lần `extract` bị ngắt. `extract` kiểm tra PDF (đọc được số trang) trước khi tạo thư mục, nên PDF hỏng hoặc có mật khẩu không để lại thư mục nào:

```
src.pdf                  bản sao PDF nguồn
manifest.json            trạng thái tổng (mục 5)
progress.json            tác vụ nền đang chạy: lệnh, pid, nhịp tim, bước, n/tổng
logs/                    stdout/stderr của MinerU, LaTeX
mineru/g001/…            kết quả zip đã giải nén của từng nhóm trang
pages/p001.png           ảnh từng trang (150 dpi) cho agent xem
document.json            định dạng trung gian (mục 6)
batches/b001.json        lô dịch (mục 7)
batches/b001.vi.json     bản dịch đã được accept
terms.json
out/main.tex, out/*.log, out/assets/
report.json
```

**Quy tắc ghi:** mỗi file chỉ do **một** lệnh ghi. Ghi luôn qua file tạm rồi `os.replace`. Lô đã dịch là file riêng (`bNNN.<lang>.json`), nên nhiều subagent `accept` song song không tranh nhau. `manifest.json` chỉ được ghi bởi `extract` và `render`. `status` tổng hợp tình trạng lô bằng cách liệt kê thư mục `batches/`, và không ghi file nào.

## 5. Lệnh

Mọi lệnh nhận `--json`. Mã thoát: `0` là thành công; `1` là lỗi kiểm tra (danh sách lỗi theo `id` nằm trong JSON); `2` là lỗi sử dụng hoặc môi trường (thiếu MinerU, thiếu LaTeX, sai đường dẫn).

| Lệnh | Việc làm ở lát 1 |
|---|---|
| `rosettex guide [--lang vi]` | In giao thức cho agent (mục 10) |
| `rosettex extract <pdf> --lang <mã> -o <work> [--tier standard\|basic] [--pages-per-group N] [--foreground]` | Mục 6, mục 7. Idempotent: bỏ qua nhóm trang đã có `mineru/gNNN/.done` |
| `rosettex status <work> [--json]` | Bước hiện tại, tiến độ, tác vụ chết hay không, và **`next_action`**: một lệnh `rosettex …` cụ thể agent cần chạy tiếp |
| `rosettex terms <work> --file <terms.json>` | Kiểm tra cấu trúc rồi ghi `terms.json` |
| `rosettex next <work> [--json]` | Gói dịch cho lô chưa có bản dịch, theo thứ tự lô (mục 7.3). Hết lô thì trả `{"done": true}` |
| `rosettex accept <work> --batch <id> --file <f>` | Mục 8. Đạt thì ghi `bNNN.<lang>.json`; không đạt thì mã thoát 1, kèm lỗi theo `id` đoạn |
| `rosettex validate <work>` | Mục 8, toàn bài |
| `rosettex render <work> --out <đường-dẫn> [--overwrite] [--foreground]` | Mục 9. Đích đã tồn tại mà không có `--overwrite` thì mã thoát 2, và giao thức yêu cầu agent hỏi người dùng |
| `rosettex clean <work>` | Chỉ xóa các mục do rosettex tạo (danh sách `OWNED` trong `workspace.py`, cộng file tạm `.*.tmp`); xóa `manifest.json` sau cùng; chỉ gỡ thư mục khi nó đã rỗng, còn file của người dùng thì giữ lại và liệt kê trong `kept` |

**Tier mặc định:** `standard` (SG 8.3 đã sửa). Lát 1 chưa có `doctor`, nên `extract` hạ xuống `basic` theo quy tắc của SG 8.3, đọc RAM khả dụng bằng thư viện chuẩn trên từng HĐH. Nếu không đọc được RAM thì giữ `standard`. Mô hình của tier chưa đạt `verify` thì mã thoát 2, kèm lệnh tải thủ công.

**Nhóm trang:** mặc định 5 trang (khoảng 90 giây/nhóm ở `standard` trên CPU, theo SG 4.1). Nhóm lỗi thì thử lại một lần. Lỗi lần hai thì đánh dấu `failed` trong `progress.json`, và `status` đề xuất `extract --tier basic` (đã ở `basic` thì dừng với `next_action: null`). Nhóm bị quá hạn (xem mục 3) không được thử lại. Nội dung MinerU trả về mà bộ chuyển đổi không hiểu (loại khối, span hoặc phần lạ) được ghi vào `document.json` ở `source.dropped` và đếm trong `report.json`, để agent báo người dùng; trang số, đầu trang và chân trang là nhiễu có chủ ý nên không ghi.

## 6. `extract`: MinerU → `document.json`

### 6.1 Gọi MinerU
`mineru-kit parse src.pdf -o mineru/gNNN --format zip --tier <tier> --pages <a-b>`, sau đó giải nén. Phải kiểm tra trong kế hoạch: `page_idx` trong kết quả của một nhóm là chỉ số tuyệt đối hay tương đối. Bộ chuyển đổi chuẩn hóa về **số trang tuyệt đối, đếm từ 1**.

### 6.2 Định dạng trung gian (`document.json`, `schema: "rosettex.document/1"`)
```json
{"source": {"pages": 20, "page_size_pt": [[612,792], …], "columns": 2, "parser": "mineru-4.2.3/standard"},
 "blocks": [
  {"id": "p3b12", "kind": "paragraph", "page": 3, "bbox": [0.088,0.505,0.493,0.567],
   "spans": [{"t": "text", "v": "Given an input sequence "}, {"t": "math", "v": "x = (x_1,\\dots,x_n)"}],
   "continues": "p3b13", "role": "body"},
  {"id": "p3b10", "kind": "heading", "level": 2, "number": "2", "label": "sec:2", "spans": […]},
  {"id": "p3b14", "kind": "equation", "tex": "…", "tag": "1", "label": "eq:1", "image": "mineru/g001/images/…"},
  {"id": "p4b17", "kind": "table", "caption": [spans], "label": "tab:1",
   "cells": [{"id": "p4b17r0c0", "r": 0, "c": 0, "rowspan": 2, "colspan": 1, "spans": […]}, …]},
  {"id": "p4b18", "kind": "figure", "image": "…", "caption": [spans], "label": "fig:1", "wide": false},
  {"id": "p1b22", "kind": "footnote", …}, {"id": "p9b40", "kind": "reference", "key": "1", …}
 ]}
```
- `kind` ánh xạ từ loại khối MinerU: `doc_title`/`paragraph_title` → `heading`; `text` → `paragraph`; `equation` → `equation`; `table` → `table`; `chart`/`image` → `figure`; `page_footnote` → `footnote`; `ref_text` → `reference`. Bỏ `page_number`, `header`, `footer`.
- **`role`** đánh dấu khối giữ nguyên tự động (SG 9.6): `authors` (khối chữ ngắn giữa tiêu đề và Abstract trên trang 1), `affiliation`, `email`, `reference`.
- **Số cột:** đếm khối `paragraph` có bbox nằm gọn ở nửa trái hoặc nửa phải trang. Nếu ≥ 60% khối như vậy thì `columns: 2`. Khối rộng quá 60% chiều ngang trang thì `wide: true`.
- **Chuẩn hóa LaTeX** (SG 9.10): chỉ gộp khoảng trắng giữa chữ số và giữa các chữ cái trong `\operatorname{…}`/`\mathrm{…}`. Không đổi ký hiệu.
- **Bảng:** `html.parser` đọc `<tr>/<td rowspan colspan>` thành `cells`. Giá trị ô thuần số (sau khi bỏ khoảng trắng) không vào lô dịch.

### 6.3 Gộp đoạn (SG 9.4)
Khối `paragraph` A nối với khối `paragraph` B kế tiếp theo thứ tự đọc (kể cả khi B ở cột hoặc trang sau, bỏ qua các khối float như hình, bảng, phương trình, chú thích chân trang nằm giữa) khi: A không kết thúc bằng `. ! ? :` (sau khi bỏ khoảng trắng), **và** B bắt đầu bằng chữ thường hoặc span `math`. Đoạn gộp lấy `id` của A. `continues` lưu danh sách khối gốc để ánh xạ lại vị trí.

### 6.4 Placeholder và liên kết (SG 9.5, 9.8)
Placeholder dạng `⟦<loại><số>⟧`, đánh số riêng trong từng đoạn:

| Loại | Nguồn | Khi dựng thành |
|---|---|---|
| `m` | span `math` | `$…$` |
| `c` | trích dẫn `[1]`, `[1, 2]`, `[3–5]` khớp khóa có thật trong `reference` | `\cite{ref1,ref2}` |
| `r` | "Figure 3", "Fig. 3", "Table 2", "Section 4", "Sec. 4", "Eq. (5)", "Equation 5", "(5)" ngay sau "Eq."; tìm cả trong span `math` (SG 9.5 đã sửa) | `\ref`/`\eqref` tới nhãn tương ứng; chữ "Figure/Hình" do agent dịch, chỉ con số là placeholder |
| `u` | URL, email | `\url{…}` |

**≠ SG 9.8:** không có placeholder `n` cho số. Số nằm nguyên trong văn bản để agent dịch tự nhiên, và được kiểm bằng đa tập (mục 8). Ngoài ra, một số có đơn vị như "4.5 million" thường phải đổi thứ tự hoặc đổi cách viết khi dịch, nên đóng khung nó thành placeholder sẽ làm câu dịch gượng.

### 6.5 Lô dịch
- Đơn vị dịch là **đoạn** (sau khi gộp), tiêu đề mục, chú thích hình/bảng, chú thích chân trang, và ô bảng có chữ. Không bao giờ cắt một đoạn ra hai lô.
- Mỗi lô khoảng ≤ 3000 ký tự nguồn. Ô của một bảng luôn nằm cùng lô.
- Khối có `role` giữ nguyên không vào lô (giữ nguyên văn khi dựng).
- `batches/bNNN.json`:
```json
{"batch": "b003", "section": "2 Method", "items": [
  {"id": "p3b9", "type": "paragraph", "page": 3, "text": "Given an input sequence ⟦m1⟧ with ⟦m2⟧, … following ⟦c1⟧ …",
   "placeholders": {"m1": "x = (x_1,\\dots,x_n)", "c1": "[1, 2]"}}]}
```

### 6.6 Gắn cờ (SG 9.9, phần làm được không cần `review`)
Ghi vào `flagged.json` để báo cáo cuối liệt kê:
- công thức chứa `$`, hoặc chứa chữ thường dạng `e.g.`/`i.e.` trong `\mathrm`;
- công thức có dấu phụ (`\dot`, `\ddot`, `\hat`, `\tilde`, `\bar`) mà lớp chữ PDF trong bbox của khối không có ký tự dấu tương ứng (đọc lớp chữ bằng `pypdfium2`);
- công thức không biên dịch được (phát hiện ở `render`).

Lát 1 chỉ **báo**, không sửa. Sửa là việc của lát 2.

## 7. `terms` và `next`

### 7.1 `terms.json`
```json
{"topic": "một câu", "terms": [{"src": "attention", "dst": "cơ chế chú ý"}],
 "keep": ["Transformer", "BLEU"], "abbreviations": [{"abbr": "FFN", "expansion": "feed-forward network"}]}
```
`rosettex terms` từ chối file sai cấu trúc, `src` trùng lặp, hoặc chuỗi rỗng.

### 7.2 Thứ tự
`next` yêu cầu `terms.json` đã có. Nếu chưa có, nó trả lỗi kèm `next_action` gợi ý agent đọc tiêu đề, tóm tắt và tiêu đề mục. Phần này lấy được qua `rosettex next <work> --outline`, lệnh in tiêu đề, tóm tắt và cây mục.

### 7.3 Gói trả về của `next --json`
Gồm: `batch` (nội dung lô), `topic`, `terms`, `section`, `previous` (đoạn cuối của lô trước, cả bản gốc và bản dịch, nếu lô trước đã có), `page_images` (đường dẫn tuyệt đối tới ảnh các trang có trong lô), `rules` (quy tắc dịch rút gọn từ gói ngôn ngữ), `submit` (lệnh `accept` chính xác để nộp).

Nhiều subagent có thể gọi `next --batch <id>` để lấy một lô cụ thể. `next` không có `--batch` trả về lô **đầu tiên chưa có bản dịch**. Lát 1 không "giữ chỗ" lô: điều phối song song là việc của agent chính.

### 7.4 File nộp cho `accept`
JSON `{"<id>": "<bản dịch>", …}`, chứa đủ mọi `id` của lô.

## 8. Kiểm tra (`accept` từng lô, `validate` toàn bài)

Mọi so sánh chạy trên văn bản đã chuẩn hóa NFC. **Lỗi** thì từ chối; **cảnh báo** thì vẫn ghi, kèm cảnh báo trong JSON.

| Kiểm tra | Mức | Ghi chú |
|---|---|---|
| Thiếu hoặc thừa `id`; đoạn rỗng | lỗi | |
| Placeholder thiếu, thừa hoặc bị sửa (so theo **đa tập**) | lỗi | **≠ SG cũ:** đổi thứ tự được phép (SG 11.2 đã sửa, chờ duyệt) |
| Số của bản gốc (ngoài placeholder) thiếu hoặc đổi giá trị (so theo **đa tập**) | lỗi | **≠ SG cũ:** được thêm số. Chuẩn hóa số trước khi so: `1,000` = `1000`; giữ dấu chấm thập phân, nên `0,05` thay cho `0.05` là **lỗi** (SG quyết định 15) |
| Nghi chưa dịch (gói ngôn ngữ) | lỗi | Bỏ qua đoạn ≤ 40 ký tự chữ, và bỏ qua `keep`/tên riêng khi tính |
| Ký tự ngoài bảng chữ cho phép | lỗi | Bảng chữ của `vi`: Latin cơ bản, 134 chữ có dấu, dấu câu Unicode thông dụng, Hy Lạp, ký hiệu toán và đơn vị |
| Tỉ lệ độ dài bản dịch / bản gốc ngoài [0,7; 2,0] | cảnh báo | Ngưỡng nằm trong gói ngôn ngữ |
| Thuật ngữ `src` xuất hiện trong bản gốc mà `dst` không có trong bản dịch | cảnh báo | |

`validate` chạy lại toàn bộ kiểm tra trên mọi lô. Ngoài ra nó kiểm tra: mọi lô đều đã có bản dịch; mọi nhãn mà `r`/`c` trỏ tới đều tồn tại; số hình, bảng, phương trình có số và tài liệu tham khảo trong `document.json` khớp với số đếm từ MinerU.

**Bộ phát hiện "chưa dịch" của `vi`** (SG 11.4): một đoạn bị coi là nghi chưa dịch khi trùng nguyên văn bản gốc, **hoặc** khi thỏa cả hai điều kiện sau:
- tỉ lệ ký tự có dấu riêng của tiếng Việt < 3% số chữ cái;
- tỉ lệ từ chức năng tiếng Việt < 5% số từ.

Ngưỡng ban đầu này được chỉnh trên bản dịch thật của `long.pdf` trong kế hoạch. Có kiểm thử hồi quy: bản dịch tiếng Việt đúng phải **đạt**.

## 9. `render`

1. **Kiểm tra trước từng mục:** gom mọi công thức và bảng vào một file `out/probe.tex`. Trước mỗi mục ghi `\message{RX:<id>}` để xác định mục nào gây lỗi. Biên dịch file này. Gặp lỗi thì đọc `id` cuối cùng trong log, chuyển mục đó sang **phương án cuối** (SG 10: mã LaTeX gốc dạng chữ, trong khung `\fbox` có ghi "công thức chưa dựng được"), rồi lặp lại cho tới khi sạch. Tối đa N lượt, với N là số mục.
2. **Sinh `out/main.tex`:**
   - Lớp `article`, `twocolumn` nếu `columns: 2`.
   - `geometry` theo khổ giấy gốc, lề lấy từ bbox nhỏ nhất và lớn nhất của các khối.
   - `fontspec` với Termes nạp theo tên file, dự phòng Noto Serif; `unicode-math` với `latinmodern-math.otf`; `polyglossia` với `vietnamese`; `hyperref`, `booktabs`, `multirow`, `graphicx`, `url`.
   - Escape `\ # $ % & _ { } ~ ^` trong **mọi văn bản dịch nằm ngoài placeholder**.
   - Hình và bảng dùng float `[tbp]` đặt ngay sau đoạn chứa tham chiếu đầu tiên tới nó; nếu không có tham chiếu thì đặt theo thứ tự trong tài liệu. Dùng `figure*`/`table*` khi `wide: true`.
   - Bảng: `tabular` với `\multirow`/`\multicolumn` theo `cells`. Bảng quá rộng thì bọc trong `\resizebox{\linewidth}{!}{…}`.
   - Tài liệu tham khảo: `thebibliography` giữ nguyên văn, `\bibitem{refN}`.
3. **Biên dịch:** Tectonic biên dịch một lần (nó tự chạy lại cho đủ lượt). Với `xelatex` thì chạy 2–3 lượt cho tới khi log hết "Rerun". Nếu biên dịch toàn bài vẫn lỗi, ánh xạ dòng lỗi về `id` qua chú thích `%RX:<id>` đặt trong `main.tex`, chuyển mục đó sang phương án cuối và biên dịch lại (SG 11.3).
4. **Kiểm tra sau dựng** (SG 11.3, dùng `pypdfium2`):
   - log không có lỗi, không có "Missing character", không có tham chiếu chưa định nghĩa;
   - lớp chữ của PDF không chứa `??` hay `[?]`;
   - trích chữ được và thấy ký tự của công thức;
   - số liên kết nội bộ khớp;
   - đủ 134 chữ có dấu nếu bài có dùng;
   - **tỉ lệ độ dài nội dung** ≤ 1,3, tính như sau: lấy tổng chiều cao (pt) của mọi dòng chữ trong PDF đầu ra chia cho tổng chiều cao tương ứng trong PDF gốc. Cả hai đều đo bằng `pypdfium2` qua hộp bao của từng ký tự, gom theo dòng.
5. **Ghi kết quả:** ghi `report.json` (SG 6: đường dẫn, chủ đề, thuật ngữ chính, tier, các mục ở phương án cuối kèm số trang, các mục bị gắn cờ, cảnh báo) rồi chép PDF tới `--out`.

## 10. Giao thức cho agent (một nguồn)

- Nguồn duy nhất là `rosettex/protocol/protocol.md`, có các chỗ thay thế như `{{lang_rules}}`.
- `rosettex guide` in nội dung này ra.
- `SKILL.md` (Claude Code) và `AGENTS.md` được **sinh lúc build** từ cùng nguồn. Bước build kiểm tra hai file sinh ra trùng với nguồn, để chúng không lệch nhau.
- `SKILL.md` gồm frontmatter (tên, và mô tả kích hoạt bằng câu tiếng Việt và tiếng Anh) cộng một dòng: "Chạy `rosettex guide` và làm theo." Toàn văn giao thức được chèn bên dưới để không mất thêm một lượt gọi lệnh.

**Nội dung giao thức:** các bước 0–8 của SG 12, **trừ bước 1** (`setup`/`doctor`, để lát 3) và **bước 4** (`review`, để lát 2). Thêm các quy tắc:
- chỉ chạy lệnh `rosettex …` đơn lẻ, không dùng ống dẫn;
- sau mỗi lần bị gián đoạn, chạy `rosettex status <work> --json` rồi làm đúng `next_action`;
- khi `status` báo tác vụ chạy nền đang chạy, gọi lại `status` sau khoảng 30–60 giây; cách chờ là việc của từng harness, giao thức không quy định.

**Cài thủ công ở lát 1:** README ghi cách chép `SKILL.md` vào thư mục skill của Claude Code và cách đặt `AGENTS.md`. Với harness khác, chỉ cần bảo agent "chạy `rosettex guide`".

## 11. Cấu trúc mã

```
rosettex/
  __main__.py, cli.py        argparse, đầu ra --json UTF-8, mã thoát
  workspace.py               đường dẫn, ghi nguyên tử, progress/nhịp tim, tách tiến trình (POSIX/Windows)
  tools.py                   tìm mineru-kit, tectonic/xelatex; đọc RAM khả dụng
  extract/
    run.py                   nhóm trang, gọi mineru-kit, idempotent
    mineru_adapter.py        docvortex.middle 2.0 → document.json
    tables.py                HTML → cells
    segment.py               gộp đoạn, role giữ nguyên, placeholder, liên kết, lô, gắn cờ
  translate.py               terms, next, accept
  checks.py                  các kiểm tra mục 8 (dùng chung cho accept/validate)
  render/
    latex.py                 document + bản dịch → main.tex, escape
    compile.py               probe, biên dịch, ánh xạ lỗi → id, phương án cuối
    postcheck.py             kiểm tra sau dựng, report.json
  langpacks/vi.json, langpacks/default.json
  protocol/protocol.md, build_protocol.py
tests/
  fixtures/papers/, fixtures/mineru-4.2.3-{basic,standard}/   (đã có từ spike)
  test_adapter.py, test_segment.py, test_checks.py, test_render.py, test_workspace.py, test_e2e_fake_translator.py
```

Gói ngôn ngữ là JSON (font, ký tự kiểm tra, bảng chữ, từ chức năng, ngưỡng, nhãn) chứ không phải mã. Thêm ngôn ngữ chỉ cần thêm một file.

## 12. Kiểm thử

- **Đơn vị**, chạy không cần MinerU, không cần LaTeX, trên 3 HĐH:
  - bộ chuyển đổi chạy trên fixture thật;
  - gộp đoạn vắt qua cột và trang của `long`;
  - placeholder và liên kết, kể cả "Eq. (1)" nằm trong `\mathrm`;
  - bảng HTML có `rowspan`/`colspan`;
  - `accept` từ chối: sửa số, `0.05` thành `0,05`, thiếu hoặc sửa placeholder, nộp nguyên văn tiếng Anh;
  - `accept` chấp nhận: bản dịch tiếng Việt đúng, bản dịch có placeholder đổi thứ tự, bản dịch thêm số, bản dịch ở dạng NFD (chuẩn hóa về NFC, bản lưu luôn là NFC);
  - escape LaTeX;
  - tách tiến trình và phát hiện tác vụ chết bằng nhịp tim (giả lập tiến trình bị giết).
- **Dựng PDF**, cần LaTeX, trên 3 HĐH: `render` chạy từ `document.json` của fixture cộng một "người dịch giả" (lấy bản gốc rồi thêm từ chức năng tiếng Việt và chữ có dấu). Kiểm tra: PDF ra đúng, sau dựng sạch, và một công thức hỏng có chủ ý được chuyển sang phương án cuối thay vì làm hỏng cả bản dựng.
- **Đầu-cuối**, cần MinerU, chạy trên máy dev: `short.pdf` và `long.pdf` với người dịch giả; sau đó dịch `short.pdf` bằng agent thật (tiêu chí thành công số 2); đo tỉ lệ bị từ chối (tiêu chí số 4).
- **CI:** ma trận GitHub Actions `ubuntu-latest`/`macos-latest`/`windows-latest`, Python 3.11 và 3.12. Bước dựng PDF tải Tectonic bản cố định có kiểm tra checksum. Thư mục dự án hiện **chưa là repo git**; khởi tạo repo là bước đầu của kế hoạch.

## 13. Quyết định đã có trong lát này (cần người dùng duyệt)

1. Phụ thuộc runtime chỉ có `pypdfium2`; không dùng PyMuPDF vì AGPL.
2. Placeholder so theo đa tập, cho phép đổi thứ tự.
3. Số so theo đa tập: được thêm số, không được thiếu hay đổi giá trị.
4. Số không dùng placeholder; `n` của SG bị bỏ.
5. Tier mặc định `standard`; nhóm 5 trang; lô ≤ 3000 ký tự.
6. Tiến trình nền phát hiện chết bằng nhịp tim 60 giây.
7. Giao thức một nguồn, in bằng `rosettex guide`; `SKILL.md` và `AGENTS.md` được sinh ra từ nguồn đó.
8. Lát 1 không giữ chỗ lô cho subagent; agent chính tự phân lô.
9. Gắn cờ công thức đáng ngờ chỉ để báo cáo; sửa là việc của lát 2.
