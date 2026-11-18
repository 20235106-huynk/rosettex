"""Download the prebuilt Tectonic 0.17.0, verify SHA-256, install into ~/.rosettex/bin (or the directory in argument 1)."""
import hashlib
import io
import os
import platform
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

VER = "0.17.0"
ASSETS = {
    ("Linux", "x86_64"): ("x86_64-unknown-linux-musl.tar.gz",
                          "8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7"),
    ("Darwin", "x86_64"): ("x86_64-apple-darwin.tar.gz",
                           "7c90ef5b6ddb1eb1937e4337add5237b79338e4b9676459fa91187d24d6cdf80"),
    ("Darwin", "arm64"): ("aarch64-apple-darwin.tar.gz",
                          "a3f1cac7c5678f01661a92212f58480ae3b0634115d880dbc59e2953ded45667"),
    ("Windows", "AMD64"): ("x86_64-pc-windows-msvc.zip",
                           "f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f"),
}


def main() -> int:
    home = Path(os.environ.get("ROSETTEX_HOME") or Path.home() / ".rosettex")
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else home / "bin"
    key = (platform.system(), platform.machine())
    if key not in ASSETS:
        print(f"no prebuilt Tectonic for {key}; install TeX Live/MiKTeX (xelatex)", file=sys.stderr)
        return 2
    name, sha = ASSETS[key]
    url = f"https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{VER}/tectonic-{VER}-{name}"
    data = urllib.request.urlopen(url, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != sha:
        print("checksum mismatch, not installing", file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    exe = "tectonic.exe" if name.endswith(".zip") else "tectonic"
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            (dest / exe).write_bytes(z.read(exe))
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            (dest / exe).write_bytes(t.extractfile(exe).read())
    (dest / exe).chmod(0o755)
    print(dest / exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
