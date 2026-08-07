"""Regenerate the README screenshots from real captured CLI output.

Usage:
    python3 docs/media/_render.py <capture-dir>

The capture directory holds plain-text output from actual runs (hero.txt,
folder_before.txt, folder_after.txt, apply.txt). Keeping this script in the
repo means the screenshots can be regenerated rather than being undocumented
one-off images that quietly go stale as the output format changes.
"""

from __future__ import annotations

import html
import pathlib
import subprocess
import sys

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = pathlib.Path(__file__).resolve().parent

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#0d1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px}
.win{background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:hidden;
     box-shadow:0 12px 32px rgba(0,0,0,.5);margin-bottom:20px}
.bar{background:#21262d;padding:9px 13px;display:flex;align-items:center;gap:7px;border-bottom:1px solid #30363d}
.dot{width:11px;height:11px;border-radius:50%}
.t{color:#8b949e;font-size:12px;margin-left:9px;font-weight:500}
pre{margin:0;padding:15px 18px;color:#c9d1d9;font-family:'SF Mono',Menlo,Consolas,monospace;
    font-size:12.5px;line-height:1.5;white-space:pre}
.g{color:#3fb950;font-weight:600}.r{color:#f85149;font-weight:600}.y{color:#d29922}
.b{color:#58a6ff;font-weight:600}.d{color:#6e7681}.w{color:#e6edf3;font-weight:600}
.row{display:flex;gap:20px;align-items:flex-start}.row>div{flex:1;min-width:0}
h3{color:#e6edf3;font-size:13px;margin:0 0 8px 2px;letter-spacing:.4px;text-transform:uppercase}
h3 .tag{font-size:11px;padding:2px 8px;border-radius:10px;margin-left:8px;text-transform:none;letter-spacing:0}
.tb{background:#3d1d1d;color:#f85149}.ta{background:#12301c;color:#3fb950}
"""

REMOVED = ("Binaries", "Intermediate", "DerivedDataCache")
KEPT = ("Content", "Config", "Saved", "uproject", "README")
# Partly reclaimed: plugin source survives, only its build output goes.
MIXED = ("Plugins",)


def win(title: str, body: str) -> str:
    dots = "".join(
        f'<span class="dot" style="background:{c}"></span>'
        for c in ("#ff5f56", "#ffbd2e", "#27c93f")
    )
    return (
        f'<div class="win"><div class="bar">{dots}'
        f'<span class="t">{html.escape(title)}</span></div><pre>{body}</pre></div>'
    )


def color_report(text: str) -> str:
    out = []
    for line in text.splitlines():
        e = html.escape(line)
        if line.startswith("PROJECT") or set(line.strip()) == {"-"}:
            e = f'<span class="d">{e}</span>'
        elif "TOTAL RECLAIMABLE" in line:
            e = f'<span class="g">{e}</span>'
        elif "FREE ON THIS VOLUME" in line:
            e = f'<span class="y">{e}</span>'
        elif "Nothing was deleted" in line:
            e = f'<span class="d">{e}</span>'
        elif line.startswith(("Found", "  UE", "Measuring")):
            e = f'<span class="b">{e}</span>'
        elif any(k in line for k in ("too recent", "skipped", "Binaries kept")):
            sep = "BP  " if "BP  " in line else "C++  "
            head, s, tail = line.partition(sep)
            e = (
                f"{html.escape(head)}<span class='w'>{html.escape(s)}</span>"
                f"<span class='d'>{html.escape(tail)}</span>"
            )
        out.append(e)
    return "\n".join(out)


def color_du(text: str) -> str:
    out = []
    for line in text.splitlines():
        e = html.escape(line)
        if line.startswith("$"):
            e = f'<span class="b">{e}</span>'
        elif "TOTAL" in line:
            e = f'<span class="w">{e}</span>'
        elif any(k in line for k in REMOVED):
            e = f'<span class="r">{e}</span>'
        elif any(k in line for k in MIXED):
            e = f'<span class="y">{e}</span>'
        elif any(k in line for k in KEPT):
            e = f'<span class="g">{e}</span>'
        out.append(e)
    return "\n".join(out)


def color_apply(text: str) -> str:
    out = []
    for line in text.splitlines():
        e = html.escape(line)
        if "Reclaiming" in line:
            e = f'<span class="b">{e}</span>'
        elif "Reclaimed" in line:
            e = f'<span class="g">{e}</span>'
        elif line.startswith(("Note:", "emptied")):
            e = f'<span class="d">{e}</span>'
        elif line.strip()[:1].isdigit():
            e = f'<span class="r">{e}</span>'
        out.append(e)
    return "\n".join(out)


def shoot(name: str, body: str, width: int, height: int) -> None:
    page = f"<style>{CSS}</style><div style='width:{width - 60}px'>{body}</div>"
    (HERE / f"{name}.html").write_text(page, encoding="utf-8")
    subprocess.run(
        [
            CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=2", f"--window-size={width},{height}",
            f"--screenshot={HERE / f'{name}.png'}", f"file://{HERE / f'{name}.html'}",
        ],
        check=True, capture_output=True,
    )
    print(f"wrote {name}.png")


def main() -> None:
    cap = pathlib.Path(sys.argv[1])
    read = lambda n: (cap / n).read_text(encoding="utf-8")  # noqa: E731

    shoot("report", win("upj report", color_report(read("hero.txt"))), 1240, 1010)

    before_after = (
        '<div class="row">'
        f'<div><h3>Before<span class="tag tb">8.7 GB</span></h3>'
        f'{win("~/ue/ThirdPersonClass", color_du(read("folder_before.txt")))}</div>'
        f'<div><h3>After<span class="tag ta">359 MB</span></h3>'
        f'{win("~/ue/ThirdPersonClass", color_du(read("folder_after.txt")))}</div>'
        "</div>"
    ) + win("upj clean --apply", color_apply(read("apply.txt")))
    shoot("before-after", before_after, 1240, 700)


if __name__ == "__main__":
    main()
