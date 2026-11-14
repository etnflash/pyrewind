"""CLI:  python -m visual_code_runner 파일.py   (또는 설치 후  vcr 파일.py)"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import webbrowser
from pathlib import Path

from .notebook import load_notebook
from .render import build_html
from .tracer import run_trace


def _parse_focus(lines: str | None, cell: int | None, cells: list | None):
    """--lines / --cell 을 (시작 줄, 끝 줄) 로 바꾼다. 노트북이면 합쳐진 소스 기준 줄 번호."""
    if lines is None and cell is None:
        return None
    rng = None
    if lines:
        parts = lines.replace(" ", "").split("-")
        if not all(p.isdigit() for p in parts) or len(parts) not in (1, 2):
            raise ValueError(f"--lines 형식이 잘못됐습니다: {lines} (예: 12-15)")
        a, b = int(parts[0]), int(parts[-1])
        rng = (min(a, b), max(a, b))
    if cell is None:
        return rng
    if not cells:
        raise ValueError("--cell 은 .ipynb 파일에서만 쓸 수 있습니다")
    if not 1 <= cell <= len(cells):
        raise ValueError(f"코드 셀은 1~{len(cells)}번까지 있습니다 (--cell {cell})")
    c = cells[cell - 1]
    if rng is None:
        return (c["start"], c["end"])
    return (c["start"] + rng[0] - 1, min(c["start"] + rng[1] - 1, c["end"]))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="vcr",
        description="Visual Code Runner - 파이썬 코드가 한 줄씩 실행되며 변수가 바뀌는 과정을 시각화합니다.",
    )
    p.add_argument("file", help="실행할 .py 또는 .ipynb 파일")
    p.add_argument("-o", "--output", help="저장할 HTML 경로 (기본: 임시 폴더)")
    p.add_argument("--no-open", action="store_true", help="브라우저를 자동으로 열지 않음")
    p.add_argument("--max-steps", type=int, default=3000, help="기록할 최대 단계 수 (기본 3000)")
    p.add_argument("--stdin", help="input() 에 차례로 넣을 값을 한 줄씩 적은 텍스트 파일")
    p.add_argument("--json", action="store_true", help="HTML 대신 trace JSON 저장")
    p.add_argument("-q", "--quiet", action="store_true", help="프로그램 출력을 터미널에 표시하지 않음")
    p.add_argument("--lines", help="이 줄 범위만 기록 (예: 12-15, 또는 12). 앞부분은 기록 없이 실행해 변수를 준비")
    p.add_argument("--cell", type=int, help="노트북: N번째 코드 셀만 기록. --lines 와 함께 쓰면 그 셀 안의 줄 번호")
    args = p.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

    path = Path(args.file).resolve()
    if not path.exists():
        print(f"[vcr] 파일을 찾을 수 없습니다: {path}", file=sys.stderr)
        return 2

    cells = None
    display_source = None
    if path.suffix.lower() == ".ipynb":
        display_source, source, cells = load_notebook(str(path))
    else:
        source = path.read_text(encoding="utf-8-sig")

    try:
        focus = _parse_focus(args.lines, args.cell, cells)
    except ValueError as e:
        print(f"[vcr] {e}", file=sys.stderr)
        return 2

    stdin_lines = None
    if args.stdin:
        stdin_lines = Path(args.stdin).read_text(encoding="utf-8-sig").splitlines()

    print(f"[vcr] 실행 중: {path.name}", file=sys.stderr)
    print("─" * 40, file=sys.stderr, flush=True)
    trace = run_trace(str(path), source, max_steps=args.max_steps,
                      stdin_lines=stdin_lines, echo=not args.quiet,
                      display_name=path.name, cells=cells, display_source=display_source, focus=focus)
    sys.stdout.flush()
    print("─" * 40, file=sys.stderr)

    if args.output:
        out = Path(args.output).resolve()
    else:
        out = Path(tempfile.gettempdir()) / "visual-code-runner" / (path.stem + (".json" if args.json else ".html"))
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.json:
        out.write_text(json.dumps(trace, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        out.write_text(build_html(trace), encoding="utf-8")

    n = len(trace["steps"])
    print(f"[vcr] {n}단계 기록 완료", file=sys.stderr)
    if focus:
        print(f"[vcr] 선택 영역: {focus[0]}~{focus[1]}번 줄만 기록 (앞부분은 미리 실행)", file=sys.stderr)
        if n == 0 and not trace["error"]:
            print("[vcr] 선택한 줄이 실행되지 않았습니다 (호출되지 않은 함수 안일 수 있어요)", file=sys.stderr)
    if trace["truncated"]:
        print(f"[vcr] 경고: 최대 단계 수({args.max_steps})에 도달해 중단했습니다. 무한 루프인지 확인하세요.", file=sys.stderr)
    if trace["error"]:
        e = trace["error"]
        print(f"[vcr] 오류: {e['type']}: {e['msg']} (줄 {e['line']})", file=sys.stderr)
        h = e.get("hint")
        if h:
            plain = lambda s: s.replace("**", "")
            print(f"[vcr]  → {h['title']}", file=sys.stderr)
            print(f"[vcr]    왜?  {plain(h['why'])}", file=sys.stderr)
            print(f"[vcr]    해결: {plain(h['fix'])}", file=sys.stderr)
        sg = e.get("suggest")
        if sg and sg.get("options"):
            print(f"[vcr]    추천: '{sg['wrong']}' 대신 혹시 '{sg['options'][0]}' ?", file=sys.stderr)
        if sg and sg.get("kind") == "kwarg" and sg.get("params"):
            print(f"[vcr]    {sg['owner']}() 가 받을 수 있는 인자: {', '.join(sg['params'])}", file=sys.stderr)
        if sg and sg.get("kind") == "module" and not sg.get("options"):
            print(f"[vcr]    설치: pip install {sg['pip']}", file=sys.stderr)
    print(f"[vcr] 결과: {out}", file=sys.stderr)

    if not args.no_open and not args.json:
        webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
