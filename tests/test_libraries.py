"""pandas / numpy / matplotlib 지원 (라이브러리가 없으면 건너뜀)."""
import json
import os
import subprocess
import sys

import pytest
from conftest import ROOT, final_globals


def test_dataframe_is_encoded_as_table(trace_code):
    pytest.importorskip("pandas")
    t = trace_code("""
        import pandas as pd
        df = pd.DataFrame({"a": [1, 2], "b": [3.5, None]})
        df["c"] = df["a"] * 2
        s = df["a"]
        """)
    g = final_globals(t)
    df = g["df"]
    assert df["k"] == "df"
    assert df["cols"] == ["a", "b", "c"]
    assert df["shape"] == [2, 3]
    assert df["rows"][1][1] == "NaN"
    assert g["s"]["k"] == "series" and g["s"]["values"] == ["1", "2"]


def test_numpy_arrays_and_scalars(trace_code):
    pytest.importorskip("numpy")
    t = trace_code("""
        import numpy as np
        v = np.array([0.123456, 2.0, 3.0])
        m = np.zeros((2, 3))
        total = np.int64(6)
        """)
    g = final_globals(t)
    assert g["v"]["k"] == "seq" and [x["r"] for x in g["v"]["items"]] == ["0.1235", "2", "3"]
    assert g["m"]["k"] == "df" and g["m"]["shape"] == [2, 3] and g["m"]["t"].startswith("ndarray")
    assert g["total"]["k"] == "prim" and g["total"]["r"] == "6"


def test_matplotlib_figures_are_captured(tmp_path):
    pytest.importorskip("matplotlib")
    prog = tmp_path / "plot.py"
    prog.write_text(
        "import matplotlib.pyplot as plt\n"
        "plt.plot([1, 2, 3])\nplt.show()\n"
        "plt.plot([3, 2, 1])\n",  # show() 없이 끝나도 마지막에 잡힘
        encoding="utf-8")
    out = tmp_path / "t.json"
    # matplotlib 백엔드는 프로세스마다 한 번 정해지므로 새 프로세스에서 실행
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    subprocess.run([sys.executable, "-m", "visual_code_runner", str(prog), "--json", "--no-open", "-q", "-o", str(out)],
                   check=True, env=env, capture_output=True)
    t = json.loads(out.read_text(encoding="utf-8"))
    assert t["error"] is None
    assert len(t["images"]) == 2
    assert t["output"].count("\x00img:") == 2
