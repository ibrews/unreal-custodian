"""Annotate a real Finder capture to show what a cleanup will remove.

Usage:
    python3 docs/media/_render_finder.py <capture-dir>

Takes an unretouched `screencapture` of a Finder window (finder_before_raw.png,
finder_after_raw.png) and overlays the tool's actual plan on top: which rows go,
which shrink, which are untouched, with the real byte counts from `upj clean`.

The underlying screenshots are genuine -- only the highlight bars and labels are
drawn -- so the reader is looking at their own Finder, not a mock-up of one.
"""

from __future__ import annotations

import base64
import pathlib
import subprocess
import sys

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = pathlib.Path(__file__).resolve().parent

# Row geometry of the captured Finder list view, in image pixels.
FIRST_ROW_Y = 95
ROW_H = 20
BAR_X, BAR_W = 172, 728
IMG_W = 900
GUTTER = 210  # labels live beside the window, never on top of its columns
LABEL_X = IMG_W + 16

GONE, PARTIAL, KEPT = "gone", "partial", "kept"

# Row order as Finder sorts it, with the verdict from `upj clean --only GASP57`.
BEFORE_ROWS = [
    ("Binaries", GONE, "894 KB"),
    ("Build", GONE, "707 B"),
    ("Config", KEPT, ""),
    ("Content", KEPT, "kept"),
    ("DerivedDataCache", GONE, "1.4 GB"),
    ("GASP57.uproject", KEPT, ""),
    ("Intermediate", GONE, "809.8 MB"),
    ("Plugins", PARTIAL, "7.7 MB of build output"),
    ("Saved", PARTIAL, "21.5 MB of autosaves"),
]

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#0d1117;padding:26px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.unit{position:relative;display:inline-block}
.wrap{position:relative;display:inline-block;border-radius:10px;overflow:hidden;
      box-shadow:0 14px 38px rgba(0,0,0,.55)}
.wrap img{display:block}
.bar{position:absolute;border-radius:5px;pointer-events:none}
.gone{background:rgba(248,81,73,.30);border:1.5px solid rgba(248,81,73,.95)}
.partial{background:rgba(210,153,34,.26);border:1.5px solid rgba(210,153,34,.95)}
.lab{position:absolute;font-size:12px;font-weight:700;font-family:-apple-system,sans-serif;
     white-space:nowrap;letter-spacing:.2px}
.lab::before{content:'';position:absolute;left:-14px;top:7px;width:10px;height:1.5px;
             background:currentColor;opacity:.55}
.lgone{color:#f85149}.lpartial{color:#d29922}.lkept{color:#2da44e}
h3{color:#e6edf3;font-size:13px;margin:0 0 10px 2px;letter-spacing:.5px;text-transform:uppercase}
h3 .tag{font-size:11px;padding:2px 9px;border-radius:10px;margin-left:8px;
        text-transform:none;letter-spacing:0;font-weight:600}
.tb{background:#3d1d1d;color:#f85149}.ta{background:#12301c;color:#3fb950}
.row{display:flex;gap:26px;align-items:flex-start}
.key{margin-top:14px;color:#8b949e;font-size:12px;display:flex;gap:20px;align-items:center}
.sw{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:-1px}
"""


def overlay(img_b64: str, rows: list[tuple[str, str, str]] | None) -> str:
    bars = labels = ""
    if rows:
        for i, (_name, verdict, label) in enumerate(rows):
            y = FIRST_ROW_Y + i * ROW_H - ROW_H // 2
            if verdict in (GONE, PARTIAL):
                bars += (
                    f'<div class="bar {verdict}" style="left:{BAR_X}px;top:{y}px;'
                    f'width:{BAR_W}px;height:{ROW_H}px"></div>'
                )
            if label:
                cls = {GONE: "lgone", PARTIAL: "lpartial", KEPT: "lkept"}[verdict]
                labels += (
                    f'<div class="lab {cls}" style="left:{LABEL_X}px;top:{y + 3}px">'
                    f"{label}</div>"
                )
    return (
        f'<div class="unit" style="width:{IMG_W + GUTTER}px">'
        f'<div class="wrap"><img src="data:image/png;base64,{img_b64}">{bars}</div>'
        f"{labels}</div>"
    )


def b64(path: pathlib.Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def shoot(name: str, body: str, width: int, height: int) -> None:
    page = f"<style>{CSS}</style>{body}"
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

    key = (
        '<div class="key">'
        '<span><span class="sw" style="background:rgba(248,81,73,.85)"></span>removed entirely</span>'
        '<span><span class="sw" style="background:rgba(210,153,34,.85)"></span>build output removed, rest kept</span>'
        '<span><span class="sw" style="background:#2da44e"></span>never touched</span>'
        "</div>"
    )

    body = (
        '<div class="row">'
        '<div><h3>Before<span class="tag tb">7.4 GB</span></h3>'
        + overlay(b64(cap / "finder_before_raw.png"), BEFORE_ROWS)
        + key
        + "</div>"
        '<div><h3>After<span class="tag ta">5.1 GB</span></h3>'
        + overlay(b64(cap / "finder_after_raw.png"), None)
        + "</div>"
        "</div>"
    )
    shoot("finder-before-after", body, 2360, 470)


if __name__ == "__main__":
    main()
