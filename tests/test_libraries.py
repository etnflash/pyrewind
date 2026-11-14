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
    subprocess.run([sys.executable, "-m", "pyrewind", str(prog), "--json", "--no-open", "-q", "-o", str(out)],
                   check=True, env=env, capture_output=True)
    t = json.loads(out.read_text(encoding="utf-8"))
    assert t["error"] is None
    assert len(t["images"]) == 2
    assert t["output"].count("\x00img:") == 2


def test_figures_captured_even_if_matplotlib_was_imported_first(tmp_path):
    """matplotlib 을 먼저 불러온 프로세스에서도 plt.show() 가 창을 띄우며 멈추지 않아야 한다.
    (예전에는 Windows / macOS CI 에서 6시간 동안 멈췄음) 혹시 멈춰도 60초 뒤 실패하도록 별도 프로세스에서 실행."""
    pytest.importorskip("matplotlib")
    prog = tmp_path / "plot.py"
    prog.write_text("import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.show()\n", encoding="utf-8")
    script = (
        "import json, sys, matplotlib\n"          # ← 먼저 불러옴 (기본 백엔드가 정해짐)
        "from pyrewind.tracer import run_trace\n"
        "p = sys.argv[1]\n"
        "t = run_trace(p, open(p, encoding='utf-8').read(), echo=False)\n"
        "print(json.dumps({'images': len(t['images']), 'error': t['error']}))\n"
    )
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    env.pop("MPLBACKEND", None)
    res = subprocess.run([sys.executable, "-c", script, str(prog)], env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=60, check=True)
    assert json.loads(res.stdout.strip().splitlines()[-1]) == {"images": 1, "error": None}
