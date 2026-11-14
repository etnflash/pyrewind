"""README 용 스크린샷 / GIF 만들기 (Windows + Microsoft Edge 필요).

    python scripts/make_screenshots.py

examples/ 를 실행해 HTML 을 만들고, Edge 헤드리스로 특정 단계를 캡처한다.
GIF 는 Pillow 로 만든다 (pip install pillow).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]
SIZE = (1440, 900)


def build(example: str, tmp: Path) -> Path:
    html = tmp / (Path(example).stem + ".html")
    subprocess.run([sys.executable, "-m", "visual_code_runner", str(ROOT / "examples" / example),
                    "--no-open", "-q", "-o", str(html)], check=True, cwd=ROOT, capture_output=True)
    return html


def shot(edge: Path, html: Path, step: int, png: Path, size=SIZE):
    url = html.resolve().as_uri() + f"#step={step}"
    subprocess.run([str(edge), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    f"--window-size={size[0]},{size[1]}", "--virtual-time-budget=2000",
                    "--force-color-profile=srgb", f"--screenshot={png}", url],
                   check=True, capture_output=True)
    for _ in range(50):
        if png.exists() and png.stat().st_size > 0:
            return
        time.sleep(0.1)
    raise RuntimeError(f"스크린샷 실패: {png}")


def main() -> int:
    from PIL import Image

    edge = next((p for p in EDGE_CANDIDATES if p.exists()), None)
    if edge is None:
        print("Microsoft Edge 를 찾을 수 없습니다.")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        basics = build("basics.py", tmp)
        bug = build("bug_hunt.py", tmp)
        pandas_nb = build("pandas_tour.ipynb", tmp)
        ml = build("ml_tour.ipynb", tmp)

        # 정지 화면
        for html, step, name in [(pandas_nb, 31, "pandas.png"), (bug, 59, "error-helper.png"), (ml, 9999, "ml-plot.png")]:
            png = OUT / name
            png.unlink(missing_ok=True)
            shot(edge, html, step, png)
            print("만듦:", png.relative_to(ROOT))

        # GIF: basics.py 반복문을 한 단계씩
        frames = []
        for step in range(14, 32):
            png = tmp / f"f{step}.png"
            shot(edge, basics, step, png)
            img = Image.open(png).convert("RGB")
            img = img.resize((1080, int(img.height * 1080 / img.width)), Image.LANCZOS)
            frames.append(img.quantize(colors=128, method=Image.Quantize.MEDIANCUT))
        gif = OUT / "demo.gif"
        frames[0].save(gif, save_all=True, append_images=frames[1:], duration=900, loop=0, optimize=True)
        print("만듦:", gif.relative_to(ROOT), f"({gif.stat().st_size // 1024}KB, {len(frames)}프레임)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
