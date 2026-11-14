"""노트북(.ipynb) 변환과 실행."""
import json

from conftest import final_globals

from pyrewind.notebook import load_notebook
from pyrewind.tracer import run_trace


def make_nb(tmp_path, cells, name="nb.ipynb"):
    nb = {
        "cells": [{"cell_type": kind, "metadata": {}, "source": src,
                   **({"outputs": [], "execution_count": None} if kind == "code" else {})}
                  for kind, src in cells],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
    }
    path = tmp_path / name
    path.write_text(json.dumps(nb, ensure_ascii=False), encoding="utf-8")
    return path


def test_code_cells_are_joined_with_line_mapping(tmp_path):
    p = make_nb(tmp_path, [("markdown", "# 제목"), ("code", "a = 1\nb = 2"), ("code", "c = a + b")])
    shown, run, cells = load_notebook(str(p))
    lines = shown.splitlines()
    assert [c["n"] for c in cells] == [1, 2]
    assert lines[cells[0]["start"] - 1] == "a = 1"
    assert lines[cells[1]["start"] - 1] == "c = a + b"
    assert len(shown.splitlines()) == len(run.splitlines())  # 줄 번호가 같아야 함


def test_magics_are_commented_out(tmp_path):
    p = make_nb(tmp_path, [("code", "%matplotlib inline\n!pip install x\nx = 1")])
    shown, run, _ = load_notebook(str(p))
    assert "# (노트북 전용 명령 - 생략됨) %matplotlib inline" in run
    assert "# (노트북 전용 명령 - 생략됨) !pip install x" in run
    compile(run, "nb", "exec")


def test_last_expression_is_displayed_like_jupyter(tmp_path):
    p = make_nb(tmp_path, [("code", "x = 1\nx"), ("code", "a, b = 1, 2\na, b"),
                           ("code", "y = 3\ny;"), ("code", "print(1)")])
    shown, run, _ = load_notebook(str(p))
    assert "__pyrewind_display__((x))" in run
    assert "__pyrewind_display__((a, b))" in run      # 괄호 없는 튜플도 값 하나로
    assert "__pyrewind_display__((y))" not in run     # ; 로 끝나면 출력 안 함
    assert "__pyrewind_display__((print" not in run
    assert "__pyrewind_display__" not in shown        # 화면에는 원래 코드


def test_notebook_runs_with_display_and_cells(tmp_path):
    p = make_nb(tmp_path, [("code", "nums = [3, 1, 2]\nnums.sort()\nnums"),
                           ("code", "display({'k': 1})\ntotal = sum(nums)")])
    shown, run, cells = load_notebook(str(p))
    t = run_trace(str(p), run, display_source=shown, cells=cells, echo=False)
    assert t["error"] is None
    assert "[1, 2, 3]" in t["output"]
    assert "{'k': 1}" in t["output"]
    assert final_globals(t)["total"]["r"] == "6"
    assert t["source"] == shown
