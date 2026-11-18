# Spec: `rosettex` — công cụ dịch báo khoa học PDF cho agent harness

Ngày: 2026-11-18 · Trạng thái: đã duyệt; cập nhật theo kết quả spike mục 16 (mục 4.1) · Triển khai theo lát, lát 1: `2026-11-18-rosettex-slice1-design.md` (cùng thư mục)

> **Gửi agent triển khai:** đọc hết tài liệu này trước khi viết mã. Mục 4 là các sự thật đã kiểm chứng; đừng giả định khác. Mục 16 liệt kê những điểm phải kiểm tra **trước** khi xây phần phụ thuộc vào chúng. Những gì ghi "do người xây quyết định" (mục 17) là quyền của bạn. Mọi thứ khác đã được người dùng chốt; muốn đổi thì hỏi người dùng.

---

## 1. Mục đích

Người dùng là nhà nghiên cứu, cần **dịch trọn cả bài** báo khoa học tiếng Anh (PDF) sang ngôn ngữ khác (trước hết là tiếng Việt) để đọc và làm việc.

Các công cụ hiện có (PDFMathTranslate, BabelDOC…) gọi engine dịch hoặc API bên ngoài. Người dùng muốn **chính agent trong harness** (Claude Code; sau này Codex, Antigravity, Hermes) làm người dịch, để không cần API key riêng và tận dụng gói đăng ký harness đang có.

Vì vậy `rosettex` **không tự dịch**. Nó làm phần agent không tự làm được với PDF: tách nội dung, chia lô, kiểm tra, dựng lại tài liệu. Agent dịch, và đọc ảnh để đối chiếu, sửa chỗ nhận dạng sai.

Công cụ được **cài vào harness**. Người dùng chỉ nói một câu, ví dụ: "Dịch bài báo `~/papers/attention.pdf` sang tiếng Việt, đặt kết quả ở `~/dich/`". Agent tự làm từ đầu đến cuối.

## 2. Mục tiêu, không-mục-tiêu, điều phải tránh

**Mục tiêu**
- Đầu vào: PDF báo khoa học tiếng Anh, có lớp chữ hoặc bản scan (scan là chế độ chất lượng thấp hơn).
- Đầu ra: **PDF** ở ngôn ngữ đích, bố cục gần bản gốc (hai cột nếu gốc hai cột), sai khác ở mức chấp nhận được và giảm tối đa. HTML song ngữ ở giai đoạn 2.
- **Công thức và bảng luôn là ký tự thật** (chọn, sao chép, tìm kiếm được), dựng lại bằng LaTeX. Không bao giờ là ảnh hay vùng cắt từ bản gốc, kể cả khi đầu vào là scan. Chỉ hình vẽ (figure) là ảnh cắt từ bản gốc.
- Giữ nguyên: số liệu (kể cả dấu chấm thập phân), trích dẫn, công thức, thứ tự mục, liên kết nội bộ bấm được (trích dẫn → tài liệu tham khảo; tham chiếu hình, bảng, mục, phương trình).
- Dấu tiếng Việt đúng, kể cả dấu kép (ệ, ữ, ẫ).
- Thuật ngữ nhất quán trong một bài.
- Tự dùng GPU khi có và phù hợp; tự quay về CPU khi không có hoặc GPU lỗi.
- Tận dụng tài nguyên có sẵn trên máy; chỉ tải phần thiếu; tải sẵn ngay sau khi cài.
- Chịu được giới hạn thời gian mỗi lệnh shell của harness; bị ngắt thì chạy tiếp, không làm lại.
- Dùng được ở mọi harness chỉ với quyền chạy shell và đọc/ghi file.

**Không phải mục tiêu**
- Giống bản gốc từng pixel.
- Dịch từ mã nguồn LaTeX (arXiv).
- Dịch chữ bên trong hình vẽ.
- Bảng thuật ngữ lưu giữa các bài.
- Gọi bất kỳ dịch vụ dịch, LLM hay phân tích tài liệu từ xa nào (kể cả `mineru-kit --remote`).

**Điều phải tránh**
- Bản dịch làm hỏng công thức, số liệu, trích dẫn mà không ai phát hiện.
- Một công thức hoặc bảng lỗi làm hỏng cả lần dựng PDF.
- Phụ thuộc vào một harness cụ thể.
- Lỗi GPU, mạng hay lệnh bị ngắt làm mất tiến độ.
- Động vào môi trường Python hoặc cài đặt khác của người dùng.

## 3. Ràng buộc
- Linux, macOS, Windows; chạy được khi chỉ có CPU.
- Python 3.12 khuyến nghị (MinerU yêu cầu 3.11–3.14).
- Không nhúng mã AGPL (BabelDOC, pdf2zh).
- Không chép mã từ dự án không có giấy phép (mục 18).
- Mức tối thiểu mọi harness có: chạy shell, đọc/ghi file. Đọc ảnh và subagent là tăng cường.
- Harness thường giới hạn thời gian mỗi lệnh shell (vài phút): mọi bước dài phải chạy nền và chạy tiếp được.

### 3.1 Chạy ở mọi máy, mọi hệ điều hành, mọi harness (ràng buộc cứng, mọi lát)
**Hệ điều hành** (Linux, macOS Intel/Apple Silicon, Windows x64):
- Chỉ Python thuần + thư viện có wheel dựng sẵn cho cả ba HĐH. Không gọi lệnh shell của hệ thống (`cp`, `rm`, `fc-list`, `nohup`…) từ mã; dùng `pathlib`, `shutil`, `subprocess` với danh sách đối số (không `shell=True`).
- **Không dùng PyMuPDF** (AGPL, trái mục 3). Đọc/kết xuất PDF bằng `pypdfium2` (Apache-2.0/BSD; MinerU đã kéo theo).
- Chạy nền: `subprocess.Popen` tách tiến trình (`start_new_session=True` trên POSIX; `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` trên Windows). Phát hiện tác vụ chết bằng **nhịp tim** (thời điểm cập nhật trong `progress.json`), không dựa vào tín hiệu hay PID của riêng một HĐH.
- Ghi file trạng thái bằng file tạm + `os.replace`, thử lại ngắn khi Windows báo file đang bị mở.
- Mọi đầu ra `--json` là UTF-8 bất kể bảng mã console (đặt lại `sys.stdout` sang UTF-8); đọc/ghi file luôn `encoding="utf-8"`, xuống dòng `\n`.
- Đường dẫn có dấu cách, ký tự không phải ASCII (ví dụ `~/Tài liệu/bài báo.pdf`): chạy LaTeX và MinerU với thư mục làm việc là `cwd` và tên file tương đối, tên file trung gian chỉ dùng ASCII.
- Font nạp theo **tên file** từ bộ gói TeX hoặc `~/.rosettex/fonts/` (không phụ thuộc fontconfig).

**Máy:** chạy được khi chỉ có CPU và RAM ~8 GB (tier `basic` khi thiếu RAM/đĩa); GPU chỉ là tăng tốc.

**Harness:** mọi tương tác đi qua lệnh `rosettex …` đơn lẻ và file — không ống dẫn (`|`), không `&`, không biến môi trường do agent đặt, không cú pháp riêng của bash/PowerShell trong giao thức. Giao thức có **một nguồn duy nhất**, in được bằng `rosettex guide` để mọi harness có shell đều tự học được; `SKILL.md` (Claude Code) và `AGENTS.md` (Codex và harness đọc AGENTS.md) được sinh từ cùng nguồn đó.

**Kiểm thử:** kiểm thử đơn vị và kiểm thử dựng PDF chạy trên ma trận Linux/macOS/Windows (CI). Kiểm thử đầu-cuối với MinerU thật chạy trên ít nhất một máy mỗi HĐH trước khi đóng giai đoạn 1.

## 4. Sự thật đã kiểm chứng (bản thử ngày 2026-11-18)

**MinerU 4.2.3** (cài từ PyPI `mineru>=4,<5`, khoảng 666 MB chưa tính mô hình):
- CLI chính là `mineru-kit`:
  `mineru-kit parse <inputs> -o <out> [--format markdown|middle_json|zip] [--tier flash|basic|standard|advanced] [--pages '1-5,8'] [--ocr-mode auto|txt|ocr] [--disable-image-analysis] [--remote …]`.
- `mineru-kit hardware detect` đề xuất mức tier tối đa phù hợp với máy; `mineru-kit hardware info` báo GPU, runtime và thiết bị được chọn kèm lý do.
- `mineru-kit models show|download|verify` quản lý mô hình. Thư mục mặc định `~/.mineru/models`, đổi bằng `model.base_dir` trong `~/.mineru/config.yaml`; biến `MINERU_MODEL_SOURCE` chọn nguồn tải.
  - Tier `basic` dùng `MinerU-4_models_onnx`.
  - Tier `standard` dùng thêm `MinerU2.5-Pro-2605-1.2B-GGUF` (chạy bằng llama.cpp, kể cả trên CPU).
  - Tier `advanced` và `flash`: chưa rõ, xem `models show` trên máy thật.
- **Runtime mặc định là ONNX Runtime + llama.cpp; PyTorch không được cài mặc định.** GPU dùng qua provider của ONNX Runtime và llama.cpp (Vulkan/CUDA). PyTorch chỉ cần cho một số nhánh (torch, vLLM, lmdeploy).
- Không có cờ CLI chọn thiết bị. Ép qua cấu hình `model.onnx_device` hoặc biến môi trường `MINERU_MODEL_ONNX_DEVICE` (nhánh ONNX); `MINERU_DEVICE_MODE` (nhánh torch).
- Schema có loại span `inline_equation`, được chuyển thành `{"type": "equation_inline", "content": "<LaTeX>"}`. ~~bbox ở mức dòng~~ → đã kiểm chứng: **chỉ có bbox mức khối** (mục 4.1).
- Có cả định dạng content list V1 và V2, khác schema nhau; không đổi tên file để coi cái này là cái kia.

**LaTeX tiếng Việt** (XeLaTeX của TeX Live 2023):
- `fontspec` hiển thị đủ **134/134** chữ cái có dấu, không cảnh báo thiếu ký tự, trích lại chữ từ PDF đầy đủ, với: TeX Gyre Termes, Noto Serif, Latin Modern Roman, DejaVu Serif, FreeSerif, Noto Serif CJK SC.
- Bản dịch của một bài 1 trang hai cột tràn sang trang 2. Phần tràn chiếm: Termes khoảng 3% chiều cao cột, Latin Modern 10%, Noto Serif 19%, Noto CJK 32%.
- `polyglossia` + `\setdefaultlanguage{vietnamese}` đổi nhãn sang tiếng Việt ("Tóm tắt nội dung", "Hình", "Bảng"). `unicode-math` + Latin Modern Math, `multirow`, `booktabs`, `hyperref` đều hoạt động.

### 4.1 Kết quả spike mục 16 (2026-11-18, Linux, 20 nhân CPU, 31 GB RAM, NVIDIA RTX A1000 4 GB)
Dữ liệu: `tests/fixtures/papers/` (bài mẫu `.tex` và PDF đã biên dịch bằng Tectonic 0.17.0), `tests/fixtures/mineru-4.2.3-{basic,standard}/` (đầu ra thật).

**Schema `middle_json`** — `schema: "docvortex.middle"`, `schema_version: "2.0"`:
- `pages[].{page_idx, blocks[]}`; kích thước trang ở `extensions.docvortex_layout.pages[].{width_pt,height_pt}`; tier ở `extensions.mineru.tier`.
- Khối: `{type, index, bbox:[x0,y0,x1,y1] chuẩn hóa 0–1, content, level?}`. Loại khối gặp: `doc_title`, `paragraph_title` (có `level`), `text`, `equation`, `table`, `chart`, `page_footnote`, `page_number`, `ref_text`.
- `content` của khối chữ là danh sách span `{type: "text"|"equation_inline", content, styles?: ["superscript",…]}`. **Span không có bbox; không có mức dòng.**
- `equation.content` là chuỗi LaTeX, kèm `\tag{n}` khi có số phương trình.
- `table.content` = [`table_caption` (span), `table_body` (**chuỗi HTML** với `rowspan`/`colspan`)]. Mất định dạng đậm; `--` thành `-`.
- `chart.content` = [`chart_body` (rỗng), `chart_caption`].
- `--format zip` = `middle_json.json` y hệt + `image_path` cho `equation`/`table_body`/`chart_body` + `images/*.jpg` cắt sẵn + `markdown.md` + `structured_content.json`. **Dùng zip.**

**Chất lượng (bài 2 cột, 20 trang, chỉ CPU):**

| | `basic` | `standard` |
|---|---|---|
| Thời gian | 231 s (11,5 s/trang) | 351 s (17,6 s/trang) |
| RAM tối đa | 4,3 GB | 4,4 GB |
| Công thức biên dịch được | 56/56 hiển thị, 579/580 giữa dòng | 56/56, 564/564 |
| Lỗi nhận dạng | `\dot{}` giả 37 lần; cùng công thức lúc đúng lúc sai; "Eq. (1)" bị nhận thành công thức; `e.g. $…$` lọt chữ vào công thức | không thấy |

Biên dịch được ≠ đúng: lỗi `\dot{V}` vẫn biên dịch được.

**Thiết bị:** `hardware detect --json` / `hardware info --json` có sẵn (bản không `--json` in tiếng Trung). ONNX: `MINERU_MODEL_ONNX_DEVICE ∈ {auto,cpu,gpu}`; MinerU tự từ chối GPU < 6 GB. llama.cpp: `MINERU_LLAMA_CPP_BACKEND ∈ {auto,cpu,vulkan,metal}`. Ép Vulkan trên GPU 4 GB chạy được nhưng chậm hơn CPU 20 nhân (37,5 s so với 30 s, bài 2 trang).

**Mô hình:** `basic` = `MinerU-4_models_onnx` 819 MB; `standard` thêm `MinerU2.5-Pro-2605-1.2B-GGUF` 1,24 GB; `flash` không cần mô hình; `advanced` không có trên runtime ONNX. `models verify` cần file đánh dấu `.mineru_complete`; file tải ngoài được chấp nhận nếu chạy lại `models download` (chỉ ghi đánh dấu, không tải lại).

**Mạng:** IPv6 hỏng làm thư viện Python treo vô hạn ở `SYN-SENT`; ModelScope có lúc giữ kết nối mà không gửi dữ liệu; client Rust của Tectonic lỗi chập chờn. `curl -4` tới HF đạt ~9 MB/s. MinerU **tự tạo `~/.mineru/config.yaml`** ở lần chạy đầu.

**Tectonic 0.17.0:** đạt 134/134 ký tự, polyglossia, unicode-math. Font trong bộ gói phải nạp **theo tên file** (`texgyretermes` + `Extension=.otf`, `latinmodern-math.otf`). Sau làm nóng chạy được với `--only-cached` (~1 s/lần) — nhưng mỗi cỡ font (ví dụ `lmroman5` cho chỉ số) phải được nạp ít nhất một lần lúc làm nóng. Cache ở `~/.cache/tectonic` (~45 MB sau làm nóng).

**Ba dự án tương tự** (chi tiết ở mục 18): bộ kiểm tra "chưa dịch" của chúng dựa vào chữ Hán, nên báo lỗi một bản dịch tiếng Việt đúng. Không dự án nào so khớp số liệu ngoài công thức.

## 5. Các hướng đã cân nhắc (đã chốt)

| Câu hỏi | Chọn | Đã loại và lý do |
|---|---|---|
| Cách dựng lại | Dựng mới từ dữ liệu trung gian → LaTeX → PDF | Ghi đè chữ lên trang gốc (công thức giữa dòng dễ hỏng); fork BabelDOC (API nội bộ, AGPL) |
| Bộ tách | MinerU 4.x | Docling (công thức giữa dòng ra văn bản thường); Marker (cần LLM ngoài, GPL); PaddleOCR-VL để sau, qua cùng định dạng trung gian |
| Bộ dựng | LaTeX (XeLaTeX qua Tectonic) | Typst (phải chuyển LaTeX → Typst); trình duyệt headless (nặng, dàn cột kém); cắt công thức/bảng từ bản gốc (không phải ký tự có cấu trúc) |
| Cung cấp LaTeX | Dùng bản có sẵn trên máy; thiếu thì tự tải Tectonic | Nhúng vào gói cài; bắt người dùng tự cài |
| Fork dự án có sẵn | Tự xây, mượn ý tưởng | Hai dự án gần nhất không có giấy phép; dự án MIT khác xa kiến trúc |

## 6. Trải nghiệm người dùng

**Câu lệnh** gồm ba thông tin, nói tự nhiên bằng tiếng Việt hay tiếng Anh:
- File PDF (một hoặc nhiều).
- Ngôn ngữ đích — **bắt buộc**.
- Nơi lưu — **bắt buộc**; thư mục hoặc đường dẫn file. Chỉ cho thư mục thì tên file là `<tên-gốc>.<mã-ngôn-ngữ>.pdf` (ví dụ `attention.vi.pdf`).

Thiếu ngôn ngữ hoặc nơi lưu: agent hỏi **một lần, gộp trong một câu** ("Bạn muốn dịch sang ngôn ngữ nào, và lưu kết quả ở đâu?"; thiếu một thì chỉ hỏi cái thiếu), trước khi chạy bất kỳ lệnh nào.

**Agent chạy hết không hỏi lại**, trừ: thiếu ngôn ngữ hoặc nơi lưu; không tìm thấy file; file kết quả đã tồn tại (hỏi trước khi ghi đè); công cụ chưa cài (hỏi một lần, gộp cả cài và tải; mục 8.2). Thư mục kết quả chưa có thì tạo. Thiết bị, tier, thuật ngữ do agent tự quyết.

**PDF scan không làm agent dừng:** agent báo "file là bản scan, nhận dạng kém chính xác hơn và chạy lâu hơn" rồi vẫn dịch; dưới khoảng 150 dpi thì cảnh báo rõ hơn.

**Trong lúc chạy:** báo ngắn theo bước (đang tách nội dung trang 12/30; đang dịch lô 3/12; đang dựng PDF).

**Khi xong**, báo gọn: đường dẫn file kết quả; chủ đề và vài thuật ngữ chính; thiết bị và tier đã dùng (có quay về CPU không); trang nào là scan; công thức/bảng chưa được đối chiếu hoặc đang hiển thị dạng mã LaTeX (mục 10), kèm số trang.

**Ngôn ngữ đích** là tham số `--lang <mã>`. Phần riêng của từng ngôn ngữ gom vào một **gói ngôn ngữ** (mục 11.4). Giai đoạn 1 chỉ có gói `vi` được tối ưu và kiểm thử; ngôn ngữ khác chạy với gói mặc định và agent báo "chưa kiểm thử".

## 7. Kiến trúc

**Hai lớp:**
1. **CLI `rosettex`** (Python). Tất định, idempotent, không gọi LLM. Mọi đầu ra cho agent có dạng `--json`.
2. **Lớp hướng dẫn cho harness** (`SKILL.md`, `AGENTS.md`): giao thức mục 12, để agent biết gọi lệnh nào, theo thứ tự nào, và quy tắc dịch.

**Môi trường:**
- `rosettex` là gói Python nhỏ, cài bằng `uv tool install rosettex`.
- MinerU được cài vào **môi trường riêng do `rosettex` quản lý** (ví dụ `~/.rosettex/mineru-venv`, tạo bằng uv, phiên bản cố định trong khoảng `>=4,<5`), gọi bằng subprocess. Lý do: MinerU nặng (≈666 MB + mô hình), cách ly để không xung đột phụ thuộc và nâng cấp độc lập. Nếu máy đã có `mineru-kit` 4.x dùng được thì dùng lại.
- Thư mục dữ liệu: `~/.rosettex/` (`bin/`, `fonts/`, `mineru-venv/`, `device.json`, `resources.json`).

**Lệnh:**

| Lệnh | Việc làm |
|---|---|
| `rosettex setup [--dry-run] [--yes] [--json]` | Tìm tài nguyên có sẵn, tải phần thiếu, làm nóng bộ nhớ đệm, kết thúc bằng `doctor` (mục 8). Chạy nền. `--dry-run` chỉ báo dùng lại được gì và phải tải bao nhiêu |
| `rosettex doctor [--json] [--refresh]` | Kiểm tra môi trường, chọn thiết bị và tier, kiểm tra LaTeX (mục 8.3) |
| `rosettex extract <pdf> --lang <mã> -o <work>` | Tách nội dung (mục 9). Chạy nền, trả về ngay |
| `rosettex status [<work>] [--setup] [--json]` | Tiến độ và **bước tiếp theo** agent cần làm |
| `rosettex terms <work> --file <terms.json>` | Ghi và kiểm tra cấu trúc bảng thuật ngữ |
| `rosettex review <work> [--next] [--accept <id> --file <f>]` | Đối chiếu công thức/bảng (mục 10) |
| `rosettex next <work> [--json]` | Trả về lô dịch tiếp theo kèm ngữ cảnh (mục 11.1) |
| `rosettex accept <work> --batch <id> --file <f>` | Nhận bản dịch một lô, **kiểm tra ngay** (mục 11.2); lỗi thì từ chối và in lỗi theo `id` |
| `rosettex validate <work>` | Kiểm tra toàn bài trước khi dựng |
| `rosettex render <work> --out <đường-dẫn> [--html]` | Dựng LaTeX → PDF (mục 11.3). Chạy nền nếu lâu |
| `rosettex clean <work>` | Xóa thư mục làm việc |
| `rosettex install-skill --harness <tên>` | Chép lớp hướng dẫn vào đúng chỗ của harness |

**Thư mục làm việc:** mặc định `<thư-mục-kết-quả>/.rosettex/<tên-gốc>.<mã-ngôn-ngữ>/`; giữ lại sau khi xong để dựng lại nhanh.

**Trạng thái và chạy tiếp:**
- `manifest.json` ghi trạng thái từng bước, từng nhóm trang, từng mục đối chiếu, từng lô (chờ / đang chạy / xong / lỗi).
- Tác vụ nền ghi `progress.json` (kèm PID, thời điểm cập nhật cuối) và `logs/`.
- Mọi lệnh idempotent: chạy lại thì bỏ qua phần đã xong.
- `status` phát hiện tác vụ nền đã chết và chỉ agent chạy lại lệnh tương ứng.

## 8. Tài nguyên, cài đặt, thiết bị

### 8.1 Tận dụng trước, tải sau
`setup` tìm trên máy trước với mọi tài nguyên, chỉ dùng lại khi qua kiểm tra, chỉ tải phần thiếu. Ghi kết quả vào `~/.rosettex/resources.json`.

| Tài nguyên | Tìm ở đâu | Điều kiện dùng lại | Nếu thiếu |
|---|---|---|---|
| MinerU | `mineru-kit` trên `PATH`; `~/.rosettex/mineru-venv` | Phiên bản 4.x; `mineru-kit version` chạy được | Tạo venv riêng bằng uv, cài `mineru>=4,<5` (phiên bản cố định) |
| Mô hình MinerU | `mineru-kit models show` (đọc `model.base_dir` có sẵn); `~/.mineru/models` | `mineru-kit models verify` đạt cho tier cần dùng | `mineru-kit models download` chỉ cho tier đã chọn, **cộng tier `basic`** để quay về CPU |
| Trình biên dịch LaTeX | `PATH`: `tectonic`, `xelatex` (TeX Live, MiKTeX, TinyTeX) | Biên dịch được tài liệu mẫu và đạt bài kiểm tra dấu của ngôn ngữ đích | Tectonic dựng sẵn cho đúng HĐH/kiến trúc, **phiên bản cố định, kiểm tra checksum**, lưu `~/.rosettex/bin/` |
| Gói TeX | Bộ nhớ đệm Tectonic; gói đã cài trong TeX Live/MiKTeX | Tài liệu mẫu biên dịch được | **Làm nóng:** biên dịch tài liệu mẫu dùng đủ mọi gói của mẫu LaTeX, để các lần sau không cần mạng |
| Font | TeX Gyre Termes trong bộ gói TeX; font hệ thống (`fc-list`; thư mục Fonts trên Windows) | Đủ ký tự của ngôn ngữ đích (smoke test) | Font dự phòng (Noto Serif, OFL) tải vào `~/.rosettex/fonts/` |

- Không vào được máy chủ gói của Tectonic → thử TinyTeX + XeLaTeX; vẫn không được thì báo rõ cái gì còn thiếu.
- Tải có thể nối tiếp; file đã kiểm tra xong thì bỏ qua.
- **Mọi lần tải có timeout kết nối và bộ giám sát treo** (không có byte mới trong ~60 s → hủy, tải nối tiếp). Treo hoặc lỗi lặp lại → đổi nguồn (ModelScope ↔ Hugging Face) và ép IPv4. File mô hình tải ngoài MinerU được kiểm SHA-256 theo `lfs.oid` của Hugging Face, rồi chạy lại `mineru-kit models download --tier …` để MinerU ghi `.mineru_complete`. Tectonic lỗi mạng khi làm nóng → thử lại.
- **Làm nóng LaTeX** bằng tài liệu mẫu dùng đủ mọi gói **và mọi cỡ font** (chữ thường, chỉ số, chỉ số cấp hai, `\operatorname`, chú thích chân trang).
- Không bao giờ ghi vào môi trường Python khác của người dùng. **Không dựa vào `~/.mineru/config.yaml`** (MinerU tự tạo file này ở lần chạy đầu): mọi cấu hình MinerU truyền qua biến môi trường khi gọi subprocess (`MINERU_MODEL_SOURCE`, `MINERU_MODEL_ONNX_DEVICE`, `MINERU_LLAMA_CPP_BACKEND`).

### 8.2 Cài qua agent
1. Agent hỏi người dùng **một lần**, gộp cả cài và tải, dùng con số ước tính trong `SKILL.md` ("Cần cài rosettex và tải tối đa khoảng X GB; phần đã có trên máy sẽ được dùng lại. Đồng ý không?").
2. Đồng ý → cài `rosettex` → `rosettex setup --dry-run --json` để có con số chính xác và báo người dùng (không hỏi lại) → **chạy ngay `rosettex setup --yes`** (chạy nền) → theo dõi bằng `rosettex status --setup`.
3. `setup` kết thúc bằng `doctor` và smoke test.
4. Nếu cài trong lúc đang có yêu cầu dịch: xong `setup` thì dịch luôn.

Cài thủ công vẫn chạy `rosettex setup` được; nếu bỏ qua, lần dịch đầu tiên agent sẽ chạy nó.

### 8.3 `doctor`: chọn thiết bị và tier
Dựa trên công cụ có sẵn của MinerU, **không tự dò GPU qua PyTorch**:
1. Hệ điều hành, Python, RAM, đĩa trống.
2. `mineru-kit version`, `mineru-kit models show` → tier nào đã đủ mô hình.
3. `mineru-kit hardware info` → GPU, runtime, thiết bị MinerU sẽ dùng và lý do.
4. `mineru-kit hardware detect` → tier tối đa khuyến nghị.
5. **Chọn tier:** mặc định **`standard`** kể cả khi chỉ có CPU (spike 4.1: chỉ chậm hơn `basic` 1,5 lần, nhưng không có lỗi công thức hệ thống của `basic`). Hạ xuống `basic` khi: RAM khả dụng < ~5 GB, đĩa không đủ cho 1,24 GB mô hình GGUF, hoặc người dùng chọn `--tier basic`. Đọc `hardware detect --json`, không đọc bản in chữ.
6. **Smoke test MinerU:** phân tích một PDF mẫu 1 trang (đóng gói kèm công cụ) với tier và thiết bị đã chọn; đo thời gian; bắt lỗi thiếu bộ nhớ, lỗi driver. Lỗi thì hạ xuống CPU + `basic` và ghi lý do.
7. **Smoke test LaTeX:** biên dịch trang mẫu chứa đủ ký tự kiểm tra của gói ngôn ngữ (tiếng Việt: 134 chữ có dấu) và một công thức; trích lại chữ bằng `pypdfium2`, chuẩn hóa NFC, xác nhận đủ ký tự; log không có "Missing character".
8. Ghi `~/.rosettex/device.json` (kèm phiên bản MinerU, driver nếu có, LaTeX). Mất hiệu lực khi một phiên bản đổi hoặc khi `--refresh`.

**Quay về khi lỗi:** MinerU lỗi trên GPU giữa chừng thì chạy lại **các nhóm trang còn lại** trên CPU, cùng tier, bằng `MINERU_MODEL_ONNX_DEVICE=cpu` **và** `MINERU_LLAMA_CPP_BACKEND=cpu` (đã kiểm chứng); lỗi bộ nhớ thì hạ thêm xuống `basic`. Ghi lý do vào `manifest.json`, agent báo người dùng. Người dùng có thể ép bằng `--device cpu|gpu` và `--tier`. GPU < 6 GB: MinerU tự chọn CPU cho ONNX; không ép Vulkan cho llama.cpp trừ khi `doctor` đo thấy nhanh hơn CPU.

## 9. `extract`: tách nội dung
1. **Phân loại từng trang:** có lớp chữ tốt / không có lớp chữ (scan) / lớp chữ OCR cũ kém (tỉ lệ ký tự lạ cao, chữ không khớp hình). Trang scan hoặc OCR kém → chạy MinerU với `--ocr-mode ocr` cho các trang đó (dùng `--pages`). Chấp nhận PDF trộn hai loại. Ước tính dpi trang scan để cảnh báo.
2. Chạy `mineru-kit parse … --format zip` theo **nhóm trang** (`--pages`), lưu kết quả từng nhóm vào `mineru/` để chạy tiếp được. Không bao giờ truyền `--remote`. Lưu ý `page_idx` trong kết quả nhóm phải được ánh xạ về số trang thật.
3. **Chuyển về định dạng trung gian** `document.json`: khối → span (MinerU không có mức dòng). Khối có bbox (chuẩn hóa 0–1) và trang; span có loại `text` / `inline_math`; khối riêng cho `display_math` / `table` / `figure`; nguồn (lớp chữ hay OCR), LaTeX cho công thức, cấu trúc ô cho bảng (**phân tích từ HTML** `table_body`). Bộ chuyển đổi MinerU → định dạng trung gian là một module riêng; thêm bộ tách khác chỉ cần thêm bộ chuyển đổi.
4. **Gộp đoạn bị cắt** khi vắt qua cột hoặc trang; bỏ header, footer, số trang gốc.
5. **Nhận diện liên kết nội bộ:** trích dẫn → tài liệu tham khảo; "Figure 3", "Table 2", "Section 4", "Eq. (5)" → đối tượng tương ứng; ghi thành nhãn và tham chiếu để dựng lại bằng `\label`/`\ref`/`\cite`. Tìm cả **bên trong span công thức** (MinerU có thể nhận "Eq. (1)" thành `\mathrm{Eq.~(1)}`) và gỡ chúng ra thành chữ.
6. **Danh sách giữ nguyên tự động:** tên tác giả, dòng đơn vị công tác, email, danh mục tham khảo.
7. Cắt **hình vẽ** thành ảnh (`assets/`); kết xuất ảnh mỗi trang (`pages/`).
8. Tạo **lô dịch** `batches/NNN.json`. Mỗi đoạn có `id`, `type`, `section`, `page`, `text`, `placeholders`.
   - Công thức giữa dòng, trích dẫn, tham chiếu, số liệu có đơn vị, URL được thay bằng placeholder `⟦m1⟧`, `⟦c1⟧`, `⟦r1⟧`, `⟦n1⟧`…
   - **Chữ trong ô bảng cũng vào lô** và phải được dịch; số trong bảng giữ nguyên.
9. **Gắn cờ** vào `flagged.json` (kèm ảnh vùng): công thức/bảng có LaTeX không biên dịch được; đoạn có dấu hiệu toán (font toán, ký tự Hy Lạp trong lớp chữ) mà không tách được công thức nào; "công thức" dài bất thường (đoạn văn bị nhầm); công thức chứa `$` hoặc chữ thường (`e.g.`, `i.e.`) — chữ lọt vào công thức; công thức có dấu phụ (`\dot`, `\ddot`, `\hat`, `\tilde`, `\bar`) mà lớp chữ PDF của khối không có dấu tương ứng; dòng OCR có số liệu (dễ nhầm `0/O`, `1/l`).
10. **Chuẩn hóa LaTeX** của MinerU trước khi dùng: gộp chữ số bị tách (`0 . 0 1` → `0.01`, `4 0 0 0` → `4000`), gộp `\operatorname { l o g }` → `\log`/`\operatorname{log}`; chỉ đổi khoảng trắng, không đổi ký hiệu.

## 10. `review`: đối chiếu công thức và bảng
Công thức và bảng luôn được dựng lại từ nhận dạng, nên đây là **bước lõi** để giữ độ chính xác.
- `rosettex review` biên dịch từng công thức và bảng (theo lô, trong một file LaTeX có đánh dấu để tách lỗi theo `id`) và tạo **ảnh đối chiếu**: vùng gốc bên trái, bản dựng lại bên phải. MinerU chỉ cho bbox mức khối, nên ảnh đối chiếu của công thức giữa dòng là **cả khối** (đoạn văn), bản dựng là đoạn đó với mọi công thức giữa dòng của nó; agent sửa cả khối trong một lần. Công thức hiển thị và bảng dùng ảnh cắt sẵn trong zip của MinerU.
- Với trang có lớp chữ: so ký tự của lớp chữ PDF trong bbox khối với ký tự của bản dựng để ưu tiên mục đáng ngờ (lớp chữ là "đáp án" miễn phí; biên dịch được không có nghĩa là đúng).
- `review --next` trả về mục cần xem tiếp theo; `review --accept <id> --file <f>` nhận LaTeX hoặc bảng đã sửa (hoặc xác nhận không đổi) và biên dịch thử ngay.
- **Thứ tự ưu tiên:** mục bị gắn cờ → trang scan → còn lại. Với GPU + tier cao: xem mục bị gắn cờ và lấy mẫu phần còn lại; với CPU hoặc scan: xem toàn bộ.
- Harness không đọc được ảnh: bỏ bước xem; báo cáo cuối ghi rõ "công thức/bảng chưa được đối chiếu".
- **Phương án cuối:** công thức/bảng vẫn không biên dịch được sau khi sửa thì hiển thị **mã LaTeX gốc dạng chữ** trong khung có đánh dấu (vẫn là ký tự) và liệt kê trong báo cáo.
- Bảng: cấu trúc ô → `tabular` (`multirow`, `multicolumn`, `booktabs`); bảng rộng dùng `table*`, `\resizebox` hoặc xoay trang.

## 11. Dịch, kiểm tra, dựng

### 11.1 Thuật ngữ và ngữ cảnh
- Agent đọc tiêu đề, tóm tắt, tiêu đề mục; ghi `terms.json` qua `rosettex terms`: chủ đề (một câu), cặp thuật ngữ nguồn → đích, danh sách giữ nguyên, chữ viết tắt và ký hiệu đã định nghĩa trong bài. Chỉ dùng cho bài này.
- `rosettex next --json` trả về: nội dung lô, tóm tắt bài, `terms.json`, tên mục chứa lô, **đoạn cuối của lô trước** (bản gốc và bản dịch), và đường dẫn ảnh trang liên quan. Có subagent thì có thể dịch nhiều lô song song; mỗi subagent tự gọi `next` hoặc nhận đúng gói này.

### 11.2 `accept` (kiểm tra ngay khi nộp) và `validate` (kiểm tra toàn bài)
Mọi phép so sánh chạy trên văn bản đã **chuẩn hóa Unicode NFC**. Một lô bị từ chối nếu:
- thiếu `id` hoặc có đoạn rỗng;
- placeholder thiếu, thừa hoặc bị sửa (so theo **đa tập**; *đổi thứ tự được phép* — trật tự từ tiếng Việt khác tiếng Anh; sửa 2026-11-18, chờ duyệt cùng spec lát 1);
- **đa tập số liệu** (ngoài placeholder): mọi số của bản gốc phải có mặt với đúng giá trị; bản dịch được thêm số khi bản gốc viết bằng chữ ("two" → "2", "1990s" → "những năm 1990"), nhưng không được thiếu hay đổi giá trị (sửa 2026-11-18, chờ duyệt cùng spec lát 1);
- **nghi chưa dịch** theo gói ngôn ngữ (mục 11.4), trừ đoạn thuộc danh sách giữ nguyên;
- tỉ lệ độ dài bản dịch / bản gốc nằm ngoài ngưỡng của gói ngôn ngữ (cảnh báo, không từ chối);
- thuật ngữ trong `terms.json` không được dùng nhất quán (cảnh báo);
- có ký tự ngoài bảng chữ của ngôn ngữ đích và ngoài tập ký tự cho phép (dấu hiệu lỗi mã hóa).

`validate` chạy lại toàn bộ kiểm tra trên mọi lô, cộng thêm: mọi công thức và bảng biên dịch được riêng lẻ; số hình, bảng, phương trình đánh số và tài liệu tham khảo khớp bản gốc. Lỗi in theo `id`; mã thoát khác 0 khi còn lỗi.

Quy tắc dịch (ghi trong `SKILL.md`): giữ nguyên placeholder; giữ nguyên định dạng số như bản gốc (dấu chấm thập phân); không dịch tên riêng, tên phương pháp/mô hình, danh mục tham khảo, tên tác giả, đơn vị, email; giữ nguyên mức độ chắc chắn của câu gốc (may, suggest, significant…); kiểm tra kỹ phủ định và chiều so sánh.

### 11.3 `render`
- Sinh `out/main.tex`, biên dịch bằng trình biên dịch đã chọn ở mục 8 (Tectonic, hoặc XeLaTeX có sẵn), chạy đủ số lượt để tham chiếu ổn định. Chạy nền nếu lâu.
- Khổ giấy và lề gần bản gốc; `twocolumn` nếu bản gốc hai cột (phát hiện từ bbox). Hình và bảng là float đặt gần vị trí gốc (`figure*`/`table*` cho đối tượng rộng hai cột).
- **Font chữ mặc định: TeX Gyre Termes** (qua `fontspec`); dự phòng Noto Serif. **Font toán: Latin Modern Math** (`unicode-math`). Ngôn ngữ qua `polyglossia`.
- `hyperref` cho liên kết nội bộ; danh mục tham khảo giữ nguyên tiếng Anh.
- Lỗi biên dịch toàn bài: tìm `id` gây lỗi từ log, chuyển mục đó sang phương án cuối (mục 10), biên dịch lại, ghi vào báo cáo.
- **Kiểm tra sau dựng** (bắt buộc trước khi báo xong):
  - log không có lỗi, không có "Missing character", không có tham chiếu chưa định nghĩa;
  - lớp chữ PDF không chứa `[?]` hoặc `??`;
  - trích chữ từ PDF thấy được công thức và bảng (là ký tự);
  - số liên kết nội bộ khớp số trích dẫn và tham chiếu;
  - **tỉ lệ độ dài nội dung** (tổng chiều cao cột đã dùng của bản dịch / bản gốc) ≤ khoảng 1,3. Không đo bằng số trang nguyên.
- Ghi `report.json` cho agent dùng khi báo kết quả (mục 6).

### 11.4 Gói ngôn ngữ
Mỗi gói (`langpacks/<mã>/`) gồm:
- **Font:** font chữ, font dự phòng, cấu hình `polyglossia`.
- **Bộ ký tự kiểm tra hiển thị:** tiếng Việt là 134 chữ có dấu.
- **Bảng chữ cho phép** (cho kiểm tra lỗi mã hóa).
- **Bộ phát hiện "chưa dịch".** Không dùng tỉ lệ chữ Hán. Với tiếng Việt, một đoạn đủ dài (ngưỡng do người xây đặt) bị coi là nghi chưa dịch khi:
  - trùng nguyên văn bản gốc sau chuẩn hóa, hoặc
  - tỉ lệ ký tự có dấu riêng của tiếng Việt (ă â đ ê ô ơ ư và các chữ mang dấu thanh) thấp bất thường, **và** tỉ lệ từ chức năng tiếng Việt (của, và, là, các, những, được, trong, cho, với, không…) thấp.
  - Bỏ qua công thức, URL, mã, tên riêng trong `terms.json` khi tính.
- **Ngưỡng tỉ lệ độ dài.**
- **Nhãn hiển thị** cho báo cáo và HTML.

Gói mặc định (cho ngôn ngữ chưa có gói): Noto Serif, chỉ kiểm tra trùng nguyên văn.

## 12. Giao thức của agent (nội dung `SKILL.md`)
0. Xác định file, ngôn ngữ đích, nơi lưu. Thiếu ngôn ngữ hoặc nơi lưu → hỏi (một câu, gộp) và dừng chờ. File kết quả đã tồn tại → hỏi trước khi ghi đè. Chưa cài `rosettex` → luồng 8.2.
1. `rosettex status --setup`: nếu `setup` chưa xong thì chạy tiếp; nếu bộ nhớ đệm thiết bị hết hiệu lực thì `rosettex doctor`.
2. `rosettex extract …` (chạy nền); gọi `rosettex status` định kỳ đến khi xong; báo tiến độ ngắn. Là bản scan thì báo người dùng.
3. Đọc tiêu đề, tóm tắt, tiêu đề mục → `rosettex terms`.
4. Lặp `rosettex review --next` / `--accept` đến khi hết mục (nếu đọc được ảnh).
5. Lặp `rosettex next` → dịch → `rosettex accept` đến khi hết lô; lô bị từ chối thì sửa theo lỗi và nộp lại. Có subagent thì dịch song song.
6. `rosettex validate`; sửa theo `id` đến khi sạch.
7. `rosettex render --out …` (chạy nền nếu lâu). Nếu đọc được ảnh, xem trang đầu, một trang có bảng và trang có công thức của PDF kết quả.
8. Báo kết quả theo mẫu ở mục 6, dùng `report.json`.

Bị gián đoạn hoặc ngữ cảnh bị nén ở bất kỳ bước nào: chạy `rosettex status` và làm theo "bước tiếp theo".

**Mô tả của skill** phải kích hoạt được bằng câu nói tự nhiên tiếng Việt và tiếng Anh ("dịch bài báo", "dịch file PDF này sang…", "translate this paper/PDF to…"). Ghi trong `SKILL.md` con số dung lượng ước tính cho bước hỏi ở 8.2.

## 13. Giai đoạn
Giai đoạn 1 được làm theo **lát dọc**, mỗi lát có spec và kế hoạch riêng:
- **Lát 1:** PDF có lớp chữ → extract → terms/next/accept → validate → render; cài thủ công; công thức/bảng không biên dịch được dùng phương án cuối.
- **Lát 2:** `review` (ảnh đối chiếu, sửa công thức/bảng).
- **Lát 3:** `setup`, `doctor`, tải mô hình chịu lỗi, GPU và quay về CPU, `install-skill`.
- **Lát 4:** scan, OCR, PDF trộn.

- **Giai đoạn 1:** setup, doctor, extract (MinerU, chọn thiết bị/tier, quay về CPU, scan, gộp đoạn, liên kết nội bộ, chữ trong bảng), status, terms, review, next/accept, validate, render (LaTeX, kiểm tra sau dựng), gói ngôn ngữ `vi` và gói mặc định, `SKILL.md` + `install-skill` cho Claude Code.
- **Giai đoạn 2:** HTML song ngữ đọc offline (một file tự chứa, KaTeX nhúng sẵn, rê chuột xem câu gốc, mục lục, tìm kiếm); vòng agent tự xem và sửa bố cục; lớp hướng dẫn cho Codex, Antigravity, Hermes; MCP server; bộ chuyển đổi PaddleOCR-VL (tùy chọn).

## 14. Kiểm thử

**Bộ thử tự tạo (không cần mạng):** sinh các bài báo mẫu bằng LaTeX trong thư mục test và biên dịch thành PDF; biết trước nội dung nên kiểm được chính xác.
- Hai cột (tiêu đề, tác giả, tóm tắt, công thức giữa dòng, phương trình đánh số, bảng có `multirow`, hình, chú thích chân trang, trích dẫn, danh mục tham khảo).
- Một cột nặng toán.
- Bài nhiều trang có đoạn vắt qua trang và cột.
- Bản "scan" (rasterize PDF thành ảnh rồi ghép lại).
- PDF trộn trang scan và trang thường.

**Bộ thử thật:** 3–5 bài báo thật do người dùng cung cấp hoặc tải trên máy có mạng.

**Kiểm thử đơn vị** (giả lập, không cần MinerU hay GPU):
- chọn tier/thiết bị từ đầu ra `hardware info/detect` mẫu;
- quay về CPU khi giả lập lỗi;
- chạy tiếp sau khi tác vụ nền bị giết;
- dùng lại tài nguyên có sẵn (mô hình, LaTeX, font);
- tải nối tiếp sau lỗi mạng;
- `accept` từ chối: sửa số liệu (`0.05` thành `0,05`), hỏng/thiếu/đổi giá trị placeholder, nộp nguyên văn tiếng Anh;
- `accept` chấp nhận: bản dịch tiếng Việt đúng (kiểm thử hồi quy cho lỗi đã gặp ở dự án khác), kể cả bản dịch đổi thứ tự placeholder (so theo đa tập), bản dịch thêm số, và bản dịch ở dạng NFD (chuẩn hóa về NFC, bản lưu luôn là NFC);
- bộ chuyển đổi MinerU → định dạng trung gian, chạy trên các file JSON mẫu đã lưu từ lần chạy MinerU thật.

**Tiêu chí nghiệm thu giai đoạn 1:**
- `validate` sạch; PDF biên dịch thành công;
- công thức và bảng là ký tự; chữ trong ô bảng đã được dịch;
- tỉ lệ độ dài nội dung ≤ khoảng 1,3; không có chữ tràn hay chồng hình;
- đủ dấu tiếng Việt; liên kết trích dẫn bấm được; không có `[?]`/`??`;
- `doctor` chọn đúng trên máy chỉ CPU và máy có GPU (nếu có);
- `setup` không tải lại tài nguyên đã có;
- chạy trọn quy trình trong Claude Code chỉ với một câu lệnh của người dùng;
- người dùng đọc thử thấy chấp nhận được.

## 15. Cấu trúc mã đề xuất (người xây có thể đổi)
```
rosettex/
  cli.py                 # định nghĩa lệnh
  workspace.py           # manifest, progress, chạy nền, idempotent
  setup/                 # resources, tải, Tectonic, font, mineru venv
  device.py              # gọi mineru-kit hardware/models, chọn tier, quay về
  extract/
    classify.py          # phân loại trang (lớp chữ / scan / OCR kém)
    mineru_runner.py     # gọi mineru-kit theo nhóm trang
    adapters/mineru.py   # MinerU middle_json → document.json
    segment.py           # gộp đoạn, placeholder, lô, liên kết, giữ nguyên
  review.py              # ảnh đối chiếu, biên dịch từng công thức/bảng
  translate_io.py        # terms, next, accept
  checks/                # các bộ kiểm tra dùng chung cho accept/validate/render
  render/
    latex.py             # document.json + bản dịch → main.tex
    compile.py           # Tectonic/XeLaTeX, đọc log, ánh xạ lỗi về id
    postcheck.py         # kiểm tra sau dựng, report.json
  langpacks/vi/, langpacks/default/
  skill/SKILL.md, skill/AGENTS.md
tests/fixtures/          # bài báo mẫu .tex, JSON MinerU mẫu
```

## 16. Phải kiểm tra trước (nhiệm vụ đầu tiên của kế hoạch)
> **Trạng thái 2026-11-18:** mục 1–6 đã kiểm tra trên Linux, kết quả ở mục 4.1. Còn lại: mục 2 trên NVIDIA ≥ 6 GB và Apple Silicon; mục 4 trên macOS/Windows; mục 6 trên trang scan; mục 7 đo trong lát 1.

1. Đầu ra `mineru-kit parse --format middle_json` trên PDF thật: có bbox cho từng `inline_equation` hay chỉ mức dòng; schema chính xác của khối, dòng, span, bảng (cấu trúc ô hay HTML), hình.
2. Cách ép thiết bị đáng tin cậy cho `mineru-kit parse` (biến môi trường nào có hiệu lực với tier nào); `hardware detect` trả về gì trên máy có GPU NVIDIA và Apple Silicon.
3. Mô hình và dung lượng của từng tier (`models show`); `models verify` có phát hiện được mô hình đặt ở `model.base_dir` khác không.
4. Tectonic: tải được bản dựng sẵn trên ba HĐH; nạp được TeX Gyre Termes và Latin Modern Math qua `fontspec` từ bộ gói; `polyglossia` + `vietnamese` hoạt động; chế độ chạy không cần mạng sau khi làm nóng.
5. Tỉ lệ LaTeX của MinerU biên dịch được ngay ở tier `basic` và `standard`.
6. Thời gian MinerU trên CPU cho bài 20–30 trang ở tier `basic` và `standard` (để đặt kích thước nhóm trang và chu kỳ `status`).
7. Agent giữ placeholder và định dạng số qua nhiều lô (đo tỉ lệ lô bị `accept` từ chối).

Mục 1–3 và 6 cần máy có mạng tới Hugging Face/ModelScope. Nếu môi trường của bạn bị chặn, dừng lại và nhờ người dùng chạy các lệnh sau trên máy của họ:

```bash
uv venv --python 3.12 mineru-test && source mineru-test/bin/activate
uv pip install "mineru>=4,<5"
mineru-kit hardware detect && mineru-kit hardware info && mineru-kit models show
time mineru-kit parse paper.pdf -o out --format middle_json --tier basic
time mineru-kit parse paper.pdf -o out_std --format middle_json --tier standard
```

Lưu các file JSON thu được vào `tests/fixtures/` để làm dữ liệu cho bộ chuyển đổi.

## 17. Do người xây quyết định
Thư viện CLI; kích thước lô dịch và nhóm trang; ngưỡng của các bộ kiểm tra; thuật toán phát hiện hai cột, gộp đoạn, nhận diện liên kết; quy tắc lấy mẫu khi đối chiếu trên GPU; cách đặt float gần vị trí gốc; nội dung PDF mẫu cho smoke test; phiên bản cố định của MinerU và Tectonic; con số dung lượng ước tính trong `SKILL.md`; cấu trúc mã chi tiết.

## 18. Tham khảo: dự án tương tự
| Dự án | Giấy phép | Được phép | Ý tưởng nên mượn |
|---|---|---|---|
| craftsfool/PaperReader.skill | Không có (ghi rõ không chỉ định giấy phép mã nguồn mở) | Chỉ học ý tưởng, **không chép mã** | `next` trả về lô kèm ảnh trang và thuật ngữ; `accept` kiểm tra từng lô; bảng thuật ngữ và ghi chú làm bộ nhớ làm việc giữa các ngữ cảnh |
| NonTrivialLiu/english-paper-pdf-translation | Không có file LICENSE | Chỉ học ý tưởng, **không chép mã** | Tìm `[?]`/`??` trong PDF cuối; đếm hình/bảng/phương trình/tài liệu tham khảo so với gốc; kiểm tra log LaTeX nghiêm ngặt |
| Biomedical-Engineer-uestc/mineru-pdf-translate | MIT | Dùng lại mã được, kèm ghi công | Cách gọi `mineru-kit parse --tier`; tự động giữ nguyên dòng tác giả và đơn vị; tách tài liệu tham khảo bằng placeholder `REF` |

Cả ba nhắm tiếng Trung. Bộ kiểm tra "chưa dịch" của mineru-pdf-translate báo lỗi bản dịch tiếng Việt đúng; PaperReader chấp nhận bản dịch đã bị sửa số liệu. Đây là hai lỗi `rosettex` phải tránh (có kiểm thử hồi quy ở mục 14).

## 19. Quyết định đã chốt
**Người dùng quyết định:** agent là người dịch, không gọi dịch vụ ngoài · dịch trọn bài, đầu vào PDF · dựng lại từ đầu, chấp nhận bố cục khác gốc trong phạm vi nhỏ · MinerU làm bộ tách, có cơ chế dùng GPU · LaTeX làm bộ dựng, công thức và bảng luôn là ký tự · thuật ngữ chỉ theo từng bài · một câu lệnh; ngôn ngữ đích và nơi lưu bắt buộc, thiếu thì hỏi · tận dụng tài nguyên có sẵn, tải sẵn sau khi cài qua agent · tự xây thay vì fork.

**Người thiết kế đề xuất, người dùng đã duyệt:**
1. Giữ hai cột nếu bản gốc hai cột.
2. Danh mục tham khảo, tên tác giả, đơn vị, email không dịch.
3. Hình vẽ cắt từ trang gốc.
4. PDF ở giai đoạn 1, HTML ở giai đoạn 2.
5. Tỉ lệ độ dài nội dung ≤ khoảng 1,3 (đo theo chiều cao cột, không theo số trang).
6. ~~Chỉ CPU thì mặc định tier `basic`~~ → **sửa 2026-11-18 theo spike:** mặc định `standard` kể cả chỉ CPU; `basic` khi thiếu RAM/đĩa hoặc người dùng chọn.
7. Kết quả `doctor` được lưu lại, tự mất hiệu lực khi đổi phiên bản.
8. Định dạng trung gian riêng để có thể đổi bộ tách.
9. Agent chạy hết không hỏi lại, trừ các trường hợp ở mục 6.
10. Chỉ cho thư mục thì tên file là `<tên-gốc>.<mã>.pdf`; tạo thư mục nếu chưa có; hỏi trước khi ghi đè.
11. Ngôn ngữ đích là tham số; giai đoạn 1 chỉ tối ưu và kiểm thử tiếng Việt.
12. Giữ thư mục làm việc; xóa bằng `rosettex clean`.
13. Tectonic là trình biên dịch mặc định khi máy chưa có LaTeX dùng được; không nhúng vào gói cài; dự phòng TinyTeX.
14. Phương án cuối cho công thức/bảng không biên dịch được: mã LaTeX dạng chữ trong khung đánh dấu.
15. Giữ dấu chấm thập phân như bản gốc.
16. Font chữ TeX Gyre Termes (dự phòng Noto Serif); font toán Latin Modern Math.
17. PDF scan được hỗ trợ, chỉ cảnh báo; dưới khoảng 150 dpi cảnh báo rõ hơn.
18. ~~Giai đoạn 1 chỉ làm lớp hướng dẫn cho Claude Code~~ → **sửa 2026-11-18 theo người dùng** ("phải chạy ở mọi máy, mọi hệ điều hành và mọi harness"): ngay từ lát 1, giao thức có một nguồn, in bằng `rosettex guide`, sinh ra `SKILL.md` và `AGENTS.md`; lõi tuân thủ mục 3.1.
19. Chỉ tải mô hình MinerU cho tier đã chọn, cộng tier `basic` để quay về CPU.
20. Khi cài qua agent: hỏi một lần gộp cả cài và tải, rồi chạy `setup` ngay.
21. Dò phần cứng và quản lý mô hình qua `mineru-kit hardware` và `mineru-kit models`; MinerU cài trong môi trường riêng do `rosettex` quản lý.
22. Kiểm tra từng lô ngay khi nộp (`accept`), cộng kiểm tra toàn bài (`validate`).
23. Mọi so sánh chạy trên văn bản đã chuẩn hóa NFC.
