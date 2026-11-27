import codecs
import re
from pathlib import Path

# a byte the codec leaves undefined (cp1252 0x81…) decodes as Latin-1: one char per byte, nothing lost
codecs.register_error("rosettex-latin1", lambda e: (e.object[e.start:e.end].decode("latin-1"), e.end))

# inputenc option → Python codec (the 8-bit encodings arXiv sources use)
_INPUTENC = {
    "latin1": "latin-1", "latin2": "iso8859-2", "latin3": "iso8859-3", "latin4": "iso8859-4",
    "latin5": "iso8859-9", "latin9": "iso8859-15", "latin10": "iso8859-16",
    "ansinew": "cp1252", "cp1252": "cp1252", "cp1250": "cp1250", "cp1251": "cp1251", "cp1257": "cp1257",
    "cp437": "cp437", "cp437de": "cp437", "cp850": "cp850", "cp852": "cp852", "cp858": "cp858",
    "cp865": "cp865", "cp866": "cp866", "cp855": "cp855", "koi8-r": "koi8-r", "koi8-u": "koi8-u",
    "koi8-ru": "koi8-u", "applemac": "mac-roman", "macce": "mac-latin2", "x-mac-cyrillic": "mac-cyrillic",
    "decmulti": "latin-1",
}
_DECL = re.compile(r"^[^%\n]*?\\(?:usepackage\s*\[([^\]]*)\]\s*\{inputenc\}|inputencoding\s*\{([^}]*)\})", re.M)


def declared_encoding(text: str) -> str | None:
    """The codec named by the last \\usepackage[...]{inputenc} / \\inputencoding{...} outside comments."""
    enc = None
    for m in _DECL.finditer(text):
        for opt in (m.group(1) or m.group(2)).split(","):
            enc = _INPUTENC.get(opt.strip().lower(), enc)
    return enc


def read_source(path: Path, fallback: str | None = None) -> tuple[str, str]:
    """(text, encoding): UTF-8 (BOM kept as utf-8-sig) when valid, else the encoding the file's own inputenc
    names, else fallback (the main file's encoding, for \\input files), else Latin-1. An ASCII file reports the
    encoding it declares (its text is the same either way)."""
    data = Path(path).read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-sig"
    if data.isascii():  # same text in any encoding; record the declared one (B rewrites inputenc by it)
        text = data.decode("ascii")
        return text, declared_encoding(text) or "utf-8"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    enc = declared_encoding(data.decode("latin-1"))
    if enc is None and fallback not in (None, "utf-8", "utf-8-sig"):
        enc = fallback
    enc = enc or "latin-1"
    return data.decode(enc, errors="rosettex-latin1"), enc


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
