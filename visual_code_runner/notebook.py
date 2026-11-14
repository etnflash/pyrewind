"""Jupyter 노트북(.ipynb)의 코드 셀을 하나의 파이썬 소스로 합친다.

- 화면용 소스(display)와 실행용 소스(exec)를 따로 만든다. 두 소스의 줄 번호는 같다.
- 실행용 소스에서는 각 셀의 마지막 식을 __vcr_display__(...) 로 감싸
  주피터처럼 그 값을 출력한다. (끝에 ; 가 있으면 출력하지 않음)
"""
from __future__ import annotations

import ast
import json

CELL_MARK = "# ═════ [셀 {n}] ═════"
_SKIP = "# (노트북 전용 명령 - 생략됨) "


def _wrap_last_expr(lines: list[str]) -> list[str]:
    try:
        tree = ast.parse("\n".join(lines))
    except SyntaxError:
        return lines
    if not tree.body or not isinstance(tree.body[-1], ast.Expr):
        return lines
    node = tree.body[-1]
    call = node.value
    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id in ("print", "display"):
        return lines
    sl, el = node.lineno - 1, node.end_lineno - 1
    sc, ec = node.col_offset, node.end_col_offset   # UTF-8 바이트 기준 오프셋
    if lines[el].encode("utf-8")[ec:].decode("utf-8").strip().startswith(";"):
        return lines
    # 괄호를 두 겹 씌운다: `a, b` 같은 괄호 없는 튜플도 값 하나로 넘어가도록
    out = list(lines)
    b = out[el].encode("utf-8")
    out[el] = (b[:ec] + b"))" + b[ec:]).decode("utf-8")
    b = out[sl].encode("utf-8")
    out[sl] = (b[:sc] + b"__vcr_display__((" + b[sc:]).decode("utf-8")
    return out


def load_notebook(path: str) -> tuple[str, str, list[dict]]:
    """(화면용 소스, 실행용 소스, 셀 정보) 반환."""
    with open(path, encoding="utf-8") as f:
        nb = json.load(f)

    shown: list[str] = []
    run: list[str] = []
    cells: list[dict] = []
    n = 0
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        n += 1
        src = cell.get("source", "")
        if isinstance(src, list):
            src = "".join(src)
        cell_lines = src.splitlines()

        mark = CELL_MARK.format(n=n)
        shown.append(mark)
        run.append(mark)
        start = len(shown) + 1

        # %%bash 같은 셀 매직은 셀 전체를 생략 (%%time 류는 첫 줄만 생략)
        first = cell_lines[0].lstrip() if cell_lines else ""
        whole_skip = first.startswith("%%") and not first.startswith(("%%time", "%%timeit", "%%capture"))

        body = []
        for ln in cell_lines:
            s = ln.lstrip()
            indent = ln[: len(ln) - len(s)]
            if whole_skip or s.startswith(("%", "!")) or (s.endswith("?") and not s.startswith("#")):
                body.append(indent + _SKIP + s)
            else:
                body.append(ln)
        shown.extend(body)
        run.extend(_wrap_last_expr(body))
        cells.append({"n": n, "start": start, "end": len(shown)})
        shown.append("")
        run.append("")

    return "\n".join(shown) + "\n", "\n".join(run) + "\n", cells
