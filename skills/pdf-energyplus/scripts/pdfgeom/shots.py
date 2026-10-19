"""Headless Edge/Chrome screenshots of the 3D viewer (for preview PNGs)."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path


def _browser() -> str | None:
    cands = [shutil.which(n) for n in ("msedge", "chrome", "google-chrome", "chromium", "chromium-browser")]
    cands += [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return next((c for c in cands if c and os.path.exists(c)), None)


def screenshot(html_path: Path, png_path: Path, query: str = "", size=(1400, 900), wait_ms: int = 12000) -> str | None:
    """Render a page (e.g. the 3D viewer with ?view=top&level=L1) to PNG with a headless Chromium browser.
    Needs internet access for three.js (CDN)."""
    exe = _browser()
    if not exe:
        return None
    if png_path.exists():
        png_path.unlink()
    url = html_path.resolve().as_uri() + (f"?{query}" if query else "")
    subprocess.run([exe, "--headless=new", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                    "--hide-scrollbars", f"--window-size={size[0]},{size[1]}", f"--virtual-time-budget={wait_ms}",
                    f"--screenshot={png_path.resolve()}", url], capture_output=True, timeout=180)
    last = -1
    for _ in range(60):
        if png_path.exists():
            s = png_path.stat().st_size
            if s > 0 and s == last:
                return str(png_path)
            last = s
        time.sleep(0.5)
    return None
