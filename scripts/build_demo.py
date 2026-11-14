"""GitHub Pages 용 웹 데모 만들기: examples/ 를 실행해 site/ 에 뷰어 HTML 과 첫 페이지를 만든다.

    python scripts/build_demo.py            # site/ 생성 (pandas, matplotlib, scikit-learn 필요)
"""
from __future__ import annotations

import html
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
REPO_URL = "https://github.com/etnflash/pyrewind"

DEMOS = [
    ("basics.py", "기본: 반복문 · 함수 · 별칭",
     "Loops, function calls and aliasing",
     "반복문을 돌 때 변수가 어떻게 바뀌는지, 함수에 무엇이 들어가고 나오는지, 두 변수가 같은 리스트를 가리키면 어떻게 되는지 봅니다."),
    ("bug_hunt.py", "버그 찾기 + 오류 도우미",
     "Find the bug, with the error helper",
     "평균이 이상하게 나오는 버그와 IndexError 가 들어 있어요. 마지막 단계의 오류 도우미가 원인과 그 값이 어디서 왔는지 알려줍니다."),
    ("pandas_tour.ipynb", "pandas 한 바퀴 (32셀)",
     "pandas tour: 32 notebook cells",
     "DataFrame 이 표로 보이고 새 열이 생기는 순간이 강조됩니다. apply(grade) 가 행마다 내 함수를 부르는 과정도 볼 수 있어요."),
    ("ml_tour.ipynb", "머신러닝 한 바퀴 (15셀)",
     "Machine learning tour with plots",
     "직접 짠 경사하강법에서 w, b, loss 가 바뀌는 과정, scikit-learn 모델, KMeans, 그래프까지 이어집니다."),
]


def build_viewer(example: str, out: Path) -> int:
    subprocess.run([sys.executable, "-m", "pyrewind", str(ROOT / "examples" / example),
                    "--no-open", "-q", "-o", str(out)], check=True, cwd=ROOT, capture_output=True)
    return out.stat().st_size


def index_html(cards: str) -> str:
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PyRewind 데모</title>
<meta name="description" content="파이썬 코드를 한 줄씩 앞뒤로 되감으며 변수가 바뀌는 과정을 보는 도구 - 설치 없이 체험하기">
<style>
:root{{--bg:#f6f7fa;--panel:#fff;--border:#e1e5eb;--text:#1d2330;--muted:#5f6876;--accent:#3b6fe0;--chip:#eef1f6}}
@media (prefers-color-scheme: dark){{:root{{--bg:#121419;--panel:#1b1e25;--border:#2c313b;--text:#e3e6ec;--muted:#98a0ad;--accent:#7aa2ff;--chip:#262a33}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 "Pretendard","Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:40px 16px 64px}}
h1{{font-size:clamp(30px,5vw,44px);margin:0 0 4px;letter-spacing:-.5px}}
h1 span{{color:var(--accent)}}
.tag{{color:var(--muted);margin:0 0 24px;font-size:18px}}
.hero{{width:100%;border-radius:12px;border:1px solid var(--border);display:block}}
h2{{font-size:20px;margin:40px 0 4px}}
.sub{{color:var(--muted);margin:0 0 16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}}
a.card{{display:flex;flex-direction:column;gap:6px;background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px;color:inherit;text-decoration:none;transition:border-color .15s,transform .15s}}
a.card:hover{{border-color:var(--accent);transform:translateY(-2px)}}
.card b{{font-size:17px}}
.card .en{{color:var(--muted);font-size:13px}}
.card p{{margin:4px 0 8px;font-size:14.5px;color:var(--muted)}}
.card .open{{margin-top:auto;color:var(--accent);font-weight:600;font-size:14px}}
.file{{font-family:"Cascadia Code",Consolas,monospace;font-size:12.5px;background:var(--chip);border-radius:6px;padding:1px 6px;align-self:flex-start}}
.keys{{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--muted);font-size:14px;margin:8px 0 0}}
kbd{{font-family:inherit;font-size:12px;border:1px solid var(--border);border-bottom-width:2px;border-radius:4px;padding:0 5px;background:var(--panel);color:var(--text)}}
pre{{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 14px;overflow:auto;font-size:14px}}
footer{{margin-top:40px;color:var(--muted);font-size:14px}}
footer a,.links a{{color:var(--accent)}}
.links{{display:flex;gap:16px;flex-wrap:wrap;margin:0 0 20px}}
</style>
</head>
<body>
<main>
  <h1>⏪ Py<span>Rewind</span></h1>
  <p class="tag">한줄씩 되감는 파이썬 · Rewind your Python, one line at a time</p>
  <div class="links"><a href="{REPO_URL}">GitHub 저장소</a><a href="{REPO_URL}#readme">README (English)</a><a href="{REPO_URL}/blob/main/README.ko.md">README (한국어)</a></div>
  <img class="hero" src="demo.gif" alt="반복문을 한 단계씩 넘기며 변수가 바뀌는 모습">

  <h2>설치 없이 바로 체험하기</h2>
  <p class="sub">예제를 실제로 실행한 기록입니다. 카드를 누르면 단계를 앞뒤로 넘겨 볼 수 있어요.</p>
  <div class="grid">{cards}</div>
  <div class="keys"><span><kbd>←</kbd> <kbd>→</kbd> 한 단계씩</span><span><kbd>Space</kbd> 자동 재생</span><span>코드 줄 클릭 = 그 줄이 실행되는 순간으로</span><span>변수 이름 클릭 = 값이 바뀐 기록</span></div>

  <h2>내 코드로 해 보기</h2>
  <pre>pip install git+{REPO_URL}
python -m pyrewind 내코드.py        # 또는 노트북.ipynb</pre>
  <p class="sub">VS Code 확장으로 에디터 옆에서 볼 수도 있어요. 자세한 방법은 README 를 보세요.</p>

  <footer>MIT License · AI 코딩 도구 <a href="https://claude.com/claude-code">Claude Code</a>의 도움을 받아 만들었습니다.</footer>
</main>
</body>
</html>
"""


def main() -> int:
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "demos").mkdir(parents=True)
    shutil.copy(ROOT / "docs" / "images" / "demo.gif", SITE / "demo.gif")
    cards = []
    for example, title, en, desc in DEMOS:
        name = Path(example).stem + ".html"
        size = build_viewer(example, SITE / "demos" / name)
        print(f"만듦: site/demos/{name} ({size // 1024}KB)")
        cards.append(
            f'<a class="card" href="demos/{name}"><span class="file">{html.escape(example)}</span>'
            f"<b>{html.escape(title)}</b><span class=\"en\">{html.escape(en)}</span>"
            f"<p>{html.escape(desc)}</p><span class=\"open\">열어 보기 →</span></a>")
    (SITE / "index.html").write_text(index_html("".join(cards)), encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    print("만듦: site/index.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
