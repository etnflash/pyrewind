"""선택 영역만 시각화 (R / Spyder 의 '선택 실행' 같은 기능)."""
import json

from conftest import final_globals
from test_notebook import make_nb

from pyrewind.__main__ import main
from pyrewind.notebook import load_notebook


def test_module_selection_records_only_range_and_stops_after(trace_code):
    t = trace_code("""
        a = 1
        b = a + 1
        c = b * 10
        d = c + 1
        print("after")
        """, focus=(2, 3))
    assert [(s["event"], s["line"]) for s in t["steps"]] == [("line", 2), ("line", 3), ("focus_end", 4)]
    g = final_globals(t)
    assert g["a"]["r"] == "1"          # 앞부분은 미리 실행됨
    assert g["c"]["r"] == "20"         # 마지막 선택 줄의 결과도 남음
    assert "d" not in g                # 뒷부분은 실행하지 않음
    assert "after" not in t["output"]
    assert t["focus"] == {"start": 2, "end": 3}


def test_functions_called_from_selection_are_traced(trace_code):
    t = trace_code("""
        def double(x):
            return x * 2
        n = 5
        m = double(n)
        """, focus=(4, 4))
    assert "double" in {s["func"] for s in t["steps"]}
    assert final_globals(t)["m"]["r"] == "10"


def test_selection_inside_function_records_every_call(trace_code):
    t = trace_code("""
        def sq(n):
            r = n * n
            return r
        for i in range(3):
            sq(i)
        """, focus=(2, 3))
    assert sum(1 for s in t["steps"] if s["event"] == "line" and s["line"] == 2) == 3
    assert {s["func"] for s in t["steps"]} == {"sq"}


def test_selection_that_never_runs(trace_code):
    t = trace_code("def f():\n    x = 1\n", focus=(2, 2))
    assert t["steps"] == []
    assert t["error"] is None


def test_error_inside_selection_is_reported(trace_code):
    t = trace_code("a = []\nb = a[0]\nc = 1\n", focus=(2, 2))
    assert t["error"]["type"] == "IndexError"
    assert t["steps"]


def test_cli_cell_option_runs_only_that_notebook_cell(tmp_path):
    p = make_nb(tmp_path, [("code", "x = 1"), ("code", "y = x + 1\nz = y * 3"), ("code", "w = 0")])
    _, _, cells = load_notebook(str(p))
    out = tmp_path / "t.json"
    assert main([str(p), "--cell", "2", "--json", "--no-open", "-q", "-o", str(out)]) == 0
    t = json.loads(out.read_text(encoding="utf-8"))
    assert t["focus"] == {"start": cells[1]["start"], "end": cells[1]["end"]}
    g = final_globals(t)
    assert g["z"]["r"] == "6" and "w" not in g

    assert main([str(p), "--cell", "2", "--lines", "2", "--json", "--no-open", "-q", "-o", str(out)]) == 0
    t = json.loads(out.read_text(encoding="utf-8"))
    assert t["focus"] == {"start": cells[1]["start"] + 1, "end": cells[1]["start"] + 1}


def test_cli_rejects_bad_focus_options(tmp_path):
    prog = tmp_path / "a.py"
    prog.write_text("x = 1\n", encoding="utf-8")
    assert main([str(prog), "--cell", "1", "--no-open", "-q"]) == 2     # .py 에는 셀이 없음
    assert main([str(prog), "--lines", "abc", "--no-open", "-q"]) == 2
