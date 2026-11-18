# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Trạng thái

Dự án đang ở giai đoạn **đã có spec và kế hoạch, chưa có mã**. Đọc theo thứ tự này trước khi làm gì:

1. `docs/superpowers/specs/2026-11-18-rosettex-design.md`: spec gốc (gọi tắt **SG**). Mục 4.1 là các sự thật đã kiểm chứng bằng spike, mục 19 là các quyết định đã chốt. Muốn đổi điều gì đã chốt thì hỏi người dùng trước.
2. `docs/superpowers/specs/2026-11-18-rosettex-slice1-design.md`: spec lát 1, đang triển khai.
3. `docs/superpowers/plans/2026-11-18-rosettex-slice1.md`: kế hoạch 15 task, có mã mẫu và test cho từng task. Làm đúng thứ tự task; các khối **Interfaces** của mỗi task quy định tên hàm và kiểu dữ liệu mà các task khác dùng.

Giai đoạn 1 được làm theo **lát dọc**, mỗi lát có spec và kế hoạch riêng:
- Lát 1: luồng đầu-cuối cho PDF có lớp chữ.
- Lát 2: `review`.
- Lát 3: `setup`, `doctor`, tự tải, GPU.
- Lát 4: scan/OCR.

Đừng làm trước phần của lát sau.

## Lệnh (theo kế hoạch lát 1; có hiệu lực từ khi xong Task 1)

```bash
uv sync                                         # cài phụ thuộc (Python 3.12 theo .python-version)
uv run pytest -q                                # toàn bộ test
uv run pytest tests/test_checks.py::test_changed_number_rejected -v   # một test
ROSETTEX_REAL=1 uv run pytest tests/test_cli.py -k real -v            # đầu-cuối với MinerU thật (chậm)
uv run python scripts/get_tectonic.py           # Tectonic 0.17.0 → ~/.rosettex/bin (test dựng PDF cần nó)
uv run python -m rosettex.protocol.build        # sinh lại skill/SKILL.md và skill/AGENTS.md sau khi sửa protocol.md
```

Test cần TeX tự skip khi không tìm thấy Tectonic hoặc XeLaTeX. Test đầu-cuối mặc định dùng `tests/fake_mineru.py` (đặt qua `ROSETTEX_MINERU`), nên không cần MinerU thật.

## Kiến trúc tổng thể

`rosettex` **không tự dịch**. Agent trong harness là người dịch; CLI chỉ làm phần tất định:

```
PDF → [MinerU, subprocess, --format zip, theo nhóm trang]
    → mineru_adapter: docvortex.middle 2.0 → document.json
    → segment: gộp đoạn, vai trò giữ nguyên, placeholder ⟦m1⟧/⟦c1⟧/⟦r1⟧/⟦u1⟧, lô ≤ 3000 ký tự
    → agent: next → dịch → accept (kiểm tra ngay) … → validate
    → render: probe.tex (cô lập từng công thức/bảng) → main.tex → Tectonic/XeLaTeX → postcheck → report.json
```

Các ý xuyên suốt nhiều file:

- **Giao tiếp với agent:** mọi lệnh có `--json`, và mọi kết quả có `next_action` (lệnh `rosettex …` agent nên chạy tiếp). `rosettex status <work>` là điểm vào sau mỗi lần gián đoạn. Mã thoát: `0` thành công, `1` lỗi kiểm tra, `2` lỗi sử dụng hoặc môi trường.
- **Chạy nền và chạy tiếp:**
  - `extract` và `render` tự sinh tiến trình con tách rời (`… --foreground`), rồi trả về ngay.
  - Tiến trình con ghi nhịp tim vào `progress.json`. Nhịp tim cũ hơn 60 giây thì coi như chết, và `status` trả lại lệnh `resume`.
  - Mỗi nhóm trang của MinerU có file đánh dấu `.done`.
  - `document.json` được ghi **sau cùng**: có file này nghĩa là extract đã xong trọn vẹn.
- **Mỗi file trạng thái chỉ có một lệnh ghi:**
  - `manifest.json` do `extract` và `render` ghi.
  - Bản dịch lưu thành file riêng cho từng lô (`batches/bNNN.<lang>.json`), để nhiều subagent `accept` song song không tranh nhau.
  - `validate.json` do `validate` ghi.
- **Phương án cuối:** công thức hoặc bảng không biên dịch được thì hiển thị mã LaTeX dạng chữ trong khung, không làm hỏng cả bản dựng. Mục gây lỗi được xác định bằng dòng `\typeout{RX:<id>}` đặt trước mỗi khối, đọc lại từ log. Khóa phương án cuối có hai dạng: id khối (`p1b10`), hoặc `<unit id>:<placeholder>` (`p1b9:m1`).
- **Kiểm tra bản dịch** (`checks.py`, dùng chung cho `accept` và `validate`):
  - so trên NFC;
  - placeholder và số so theo **đa tập**: được đổi thứ tự, được thêm số, không được thiếu hay đổi giá trị; `0.05` → `0,05` là lỗi;
  - phát hiện "chưa dịch" theo tỉ lệ chữ có dấu và tỉ lệ từ chức năng của gói ngôn ngữ, không dùng tỉ lệ chữ Hán.
- **Gói ngôn ngữ** là JSON (`rosettex/langpacks/<mã>.json`); thêm ngôn ngữ không cần viết mã.
- **Giao thức cho agent có một nguồn duy nhất:** `rosettex/protocol/protocol.md`. Nguồn này được in bằng `rosettex guide`, và sinh ra `skill/SKILL.md` cùng `skill/AGENTS.md`. Có test bắt hai file sinh ra phải khớp nguồn. Đừng sửa tay hai file đó.

## Ràng buộc cứng (SG 3.1)

- Phải chạy trên Linux, macOS và Windows, chỉ cần CPU, ở **mọi harness** chỉ có shell và quyền đọc/ghi file. Giao thức không dùng ống dẫn, `&&`, hay cú pháp riêng của bash hoặc PowerShell.
- Phụ thuộc runtime **chỉ có `pypdfium2`**. **Cấm PyMuPDF** (AGPL); cấm nhúng mã AGPL hay mã của dự án không có giấy phép (SG 18).
- Không gọi lệnh shell của hệ thống từ mã; không dùng `shell=True`. Đọc và ghi file luôn UTF-8 (đọc JSON bằng `utf-8-sig`), xuống dòng `\n`. Ghi JSON qua file tạm rồi `os.replace`.
- Cấu hình MinerU chỉ truyền qua biến môi trường của tiến trình con. Không đọc hay ghi `~/.mineru/config.yaml`, không bao giờ dùng `--remote`.

## Sự thật đã kiểm chứng, dễ làm sai (chi tiết ở SG 4.1)

- Schema MinerU là khối → span; **chỉ khối có bbox** (chuẩn hóa 0–1). `page_idx` là số tuyệt đối, đếm từ 0, kể cả khi chạy với `--pages`. Bảng ra dạng chuỗi HTML.
- Tier `basic` thêm `\dot{}` giả vào công thức. Tier `standard` thì không, nên là mặc định kể cả khi chỉ có CPU. Biên dịch được không có nghĩa là đúng.
- Ép thiết bị cần **hai** biến: `MINERU_MODEL_ONNX_DEVICE ∈ {auto,cpu,gpu}` và `MINERU_LLAMA_CPP_BACKEND ∈ {auto,cpu,vulkan,metal}`. Muốn đọc phần cứng thì dùng `mineru-kit hardware detect --json`; bản không có `--json` in tiếng Trung.
- Với Tectonic, font phải nạp **theo tên file**: `texgyretermes` với `Extension=.otf`, và `latinmodern-math.otf`. Lần đầu chạy Tectonic cần mạng; client Rust của nó lỗi chập chờn khi IPv6 hỏng, nên cứ chạy lại.
- Trên máy dev này, thư viện Python tải từ Hugging Face bị treo vì IPv6 hỏng. Dùng `--source modelscope`, hoặc `curl -4`.
- `tests/fixtures/` chứa đầu ra **thật** của MinerU 4.2.3, là sự thật để đối chiếu. Test sai thì sửa test cho khớp fixture, không sửa fixture.
