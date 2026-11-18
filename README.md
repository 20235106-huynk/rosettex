# rosettex

Công cụ để **agent trong harness** (Claude Code, Codex, …) dịch trọn bài báo khoa học PDF. rosettex không tự dịch:
nó tách nội dung (MinerU), chia lô có placeholder, kiểm tra bản dịch và dựng lại PDF bằng LaTeX, với công thức và bảng là ký tự thật.
Lát 1 hỗ trợ PDF có lớp chữ.

## Cài đặt (lát 1: cài thủ công)

```bash
uv tool install <đường-dẫn-repo-rosettex>          # hoặc: pipx install <đường-dẫn>
uv venv --python 3.12 ~/.rosettex/mineru-venv
uv pip install --python ~/.rosettex/mineru-venv "mineru>=4,<5"
~/.rosettex/mineru-venv/bin/mineru-kit models download --tier standard --source modelscope
#   Windows: %USERPROFILE%\.rosettex\mineru-venv\Scripts\mineru-kit.exe models download --tier standard
#   Tải bị treo: dừng rồi chạy lại (tải nối tiếp được), hoặc đổi --source huggingface
uv run python scripts/get_tectonic.py               # Tectonic 0.17.0 → ~/.rosettex/bin (bỏ qua nếu đã có xelatex)
```

Biến môi trường tùy chọn (người dùng đặt một lần): `ROSETTEX_MINERU` (đường dẫn `mineru-kit`), `ROSETTEX_TEX` (đường dẫn `tectonic`/`xelatex`), `ROSETTEX_HOME` (mặc định `~/.rosettex`).

## Gắn vào harness

- **Claude Code:** chép `skill/SKILL.md` vào `~/.claude/skills/rosettex/SKILL.md`.
- **Codex và harness đọc AGENTS.md:** chép nội dung `skill/AGENTS.md` vào `AGENTS.md` của bạn.
- **Harness khác:** bảo agent "chạy `rosettex guide` rồi làm theo".

Sau đó chỉ cần nói: *"Dịch bài báo ~/papers/attention.pdf sang tiếng Việt, lưu ở ~/dich/"*.

## Phát triển

```bash
uv sync
uv run pytest -q                                   # ROSETTEX_REAL=1 để chạy thêm đầu-cuối với MinerU thật
uv run python -m rosettex.protocol.build           # sau khi sửa rosettex/protocol/protocol.md
```

Spec: `docs/superpowers/specs/`. Kế hoạch: `docs/superpowers/plans/`.
