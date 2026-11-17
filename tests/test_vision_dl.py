"""이미지 / 3차원 이상 배열 / 딥러닝 텐서 / scipy / plotly 표시 (라이브러리가 없으면 건너뜀)."""
import base64
import json
import os
import subprocess
import sys

import pytest
from conftest import ROOT, final_globals

from pyrewind.tracer import Encoder


def _png_size(src: str):
    raw = base64.b64decode(src.split(",", 1)[1])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def test_color_image_array_becomes_picture(trace_code):
    pytest.importorskip("numpy")
    t = trace_code("""
        import numpy as np
        img = np.zeros((32, 48, 3), dtype=np.uint8)
        img[:, :16] = [255, 0, 0]
        """)
    v = final_globals(t)["img"]
    assert v["k"] == "nd" and v["shape"] == [32, 48, 3]
    assert v["dims"].startswith("높이 × 너비 × 색")
    assert len(v["slices"]) == 1 and _png_size(v["slices"][0]["src"]) == (48, 32)
    assert v["stats"]["max"] == "255"


def test_batch_and_feature_maps_become_slices(trace_code):
    pytest.importorskip("numpy")
    t = trace_code("""
        import numpy as np
        batch = np.zeros((20, 1, 28, 28), dtype=np.float32)   # 개수 × 1 × 높이 × 너비
        maps = np.ones((5, 6, 6))                              # 특징 맵 5장
        big = np.zeros((2, 3, 4, 300, 300))                     # 5차원, 큰 그림은 줄여서
        """)
    g = final_globals(t)
    assert g["batch"]["total"] == 20 and len(g["batch"]["slices"]) == 16   # 앞 16장만
    assert g["batch"]["slices"][3]["l"] == "[3]"
    assert len(g["maps"]["slices"]) == 5 and "vals" in g["maps"]["slices"][0]   # 6×6 은 숫자도
    assert g["big"]["total"] == 24 and g["big"]["slices"][5]["l"] == "[0, 1, 1]"
    assert max(_png_size(g["big"]["slices"][0]["src"])) <= 128


def test_large_2d_array_gets_a_picture_small_one_does_not(trace_code):
    pytest.importorskip("numpy")
    t = trace_code("""
        import numpy as np
        digit = np.eye(28)
        X = np.ones((150, 4))
        """)
    g = final_globals(t)
    assert g["digit"]["k"] == "df" and _png_size(g["digit"]["img"]) == (28, 28)
    assert "img" not in g["X"]


def test_pil_image_is_shown_and_inplace_drawing_is_detected(trace_code):
    pytest.importorskip("PIL")
    t = trace_code("""
        from PIL import Image, ImageDraw
        pic = Image.new("RGB", (40, 20), "white")
        ImageDraw.Draw(pic).rectangle([0, 0, 39, 19], fill="red")
        done = True
        """)
    first = None
    for st in t["steps"]:
        for n, i in st["stack"][-1]["vars"]:
            if n == "pic":
                first = first or t["values"][i]
    last = final_globals(t)["pic"]
    assert last["k"] == "nd" and last["shape"] == [20, 40, 3]
    assert first["slices"][0]["src"] != last["slices"][0]["src"]   # 그린 뒤 그림이 바뀜


def test_numpy_scalars_inside_containers_and_namedtuples(trace_code):
    pytest.importorskip("numpy")
    t = trace_code("""
        import numpy as np
        from collections import namedtuple
        R = namedtuple("R", "statistic pvalue")
        r = R(np.float64(-1.25), np.float64(0.5))
        xs = [np.float64(1.5), np.int64(2)]
        """)
    g = final_globals(t)
    assert g["r"]["k"] == "obj" and g["r"]["attrs"][0] == ["statistic", {"k": "prim", "t": "float64", "r": "-1.25"}]
    assert [x["r"] for x in g["xs"]["items"]] == ["1.5", "2"]


class _FakeTensor:
    """torch 없이도 텐서 처리 경로를 시험하기 위한 가짜 텐서."""
    def __init__(self, arr, device="cuda:0", grad=True):
        self.arr, self.device, self.requires_grad = arr, device, grad
        self.shape, self.dtype = arr.shape, "torch.float32"

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.arr

    def __getitem__(self, idx):
        return _FakeTensor(self.arr[idx], self.device, self.requires_grad)


_FakeTensor.__module__ = "torch"
_FakeTensor.__name__ = "Tensor"


def test_tensor_label_shows_dtype_device_and_grad():
    np = pytest.importorskip("numpy")
    e = Encoder().encode(_FakeTensor(np.zeros((4, 3, 16, 16), dtype=np.float32)))
    assert e["k"] == "nd" and e["t"] == "Tensor[float32, cuda:0, grad]"
    assert e["shape"] == [4, 3, 16, 16] and e["dims"] == "개수 × 색 × 높이 × 너비"
    v = Encoder().encode(_FakeTensor(np.arange(5.0), device="cpu", grad=False))
    assert v["k"] == "seq" and v["t"] == "Tensor[float32, cpu]" and v["len"] == 5


def test_real_torch_tensors_and_model(trace_code):
    pytest.importorskip("torch")
    t = trace_code("""
        import torch, torch.nn as nn
        x = torch.rand(2, 3, 8, 8)
        model = nn.Sequential(nn.Conv2d(3, 4, 3), nn.ReLU())
        y = model(x)
        """)
    g = final_globals(t)
    assert g["x"]["k"] == "nd" and g["x"]["t"] == "Tensor[float32, cpu]"
    assert g["y"]["t"] == "Tensor[float32, cpu, grad]" and g["y"]["total"] == 8
    assert g["model"]["k"] == "text" and "Conv2d" in g["model"]["r"] and "112" in g["model"]["note"]


def test_matplotlib_figure_objects_are_one_line(trace_code):
    pytest.importorskip("matplotlib")
    t = trace_code("""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        """)
    g = final_globals(t)
    assert g["fig"]["k"] == "prim" and g["fig"]["r"].startswith("<Figure")
    assert g["ax"]["k"] == "prim"


def test_plotly_show_is_captured_as_interactive_plot(tmp_path):
    pytest.importorskip("plotly")
    prog = tmp_path / "p.py"
    prog.write_text(
        "import plotly.graph_objects as go\n"
        "fig = go.Figure(go.Scatter3d(x=[1, 2], y=[3, 4], z=[5, 6]))\n"
        "fig.show()\n", encoding="utf-8")
    out = tmp_path / "t.json"
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    subprocess.run([sys.executable, "-m", "pyrewind", str(prog), "--json", "--no-open", "-q", "-o", str(out)],
                   check=True, env=env, capture_output=True, timeout=120)
    t = json.loads(out.read_text(encoding="utf-8"))
    assert t["error"] is None
    assert len(t["images"]) == 1 and "plotly" in t["images"][0]
    assert json.loads(t["images"][0]["plotly"])["data"][0]["type"] == "scatter3d"
    assert t["output"].count("\x00img:") == 1
    fig = final_globals(t)["fig"]
    assert fig["k"] == "prim" and "Scatter3d" in fig["r"]


def test_float_planes_are_colored_heatmaps_and_photos_are_marked(trace_code):
    np = pytest.importorskip("numpy")
    t = trace_code("""
        import numpy as np
        photo = np.zeros((1, 3, 16, 16), dtype=np.float32)   # 사진 한 장
        cam = np.linspace(0, 1, 256).reshape(16, 16)          # 0~1 히트맵
        signed = np.linspace(-1, 2, 256).reshape(1, 1, 16, 16)  # 음수/양수 → 파랑/빨강
        gray = np.full((16, 16), 7, dtype=np.uint8)           # 0~255 정수는 흑백 그대로
        """)
    g = final_globals(t)
    assert g["photo"]["photo"] is True and "heat" not in g["photo"]
    assert g["cam"]["heat"]["cmap"] == "viridis"
    assert g["signed"]["k"] == "df" and g["signed"]["heat"]["cmap"] == "RdBu"   # 크기 1인 축은 빠짐
    assert g["signed"]["t"].endswith("(1, 1, 16, 16)")
    assert "heat" not in g["gray"]


def test_size_one_axes_are_squeezed(trace_code):
    pytest.importorskip("numpy")
    t = trace_code("""
        import numpy as np
        cx = np.arange(64).reshape(64, 1, 1)
        """)
    cx = final_globals(t)["cx"]
    assert cx["k"] == "seq" and cx["len"] == 64 and cx["t"] == "ndarray[int64] (64, 1, 1)"


def test_loop_limit_records_only_first_iterations(trace_code):
    code = """
        def double(v):
            return v * 2

        total = 0
        for i in range(100):
            total += double(i)
        done = total
        """
    full = trace_code(code)
    lim = trace_code(code, loop_limit=3)
    assert len(lim["steps"]) < len(full["steps"]) / 10
    assert lim["loop_limit"] == 3
    skips = [s for s in lim["steps"] if s.get("skipped")]
    assert len(skips) == 1 and skips[0]["line"] == 7          # 반복문 다음 줄에서 다시 기록
    assert final_globals(lim)["done"]["r"] == str(sum(i * 2 for i in range(100)))
    assert sum(1 for s in lim["steps"] if s["event"] == "call" and s["func"] == "double") == 3


def test_loop_limit_still_records_the_error(trace_code):
    t = trace_code("""
        for i in range(50):
            x = 10 / (40 - i)
        """, loop_limit=2)
    assert t["error"]["type"] == "ZeroDivisionError"
    assert any(s["event"] == "exception" for s in t["steps"])


def test_pyrewind_env_flag_is_set_only_while_running(trace_code):
    import os
    t = trace_code("""
        import os
        flag = os.environ.get("PYREWIND")
        """)
    assert final_globals(t)["flag"]["r"] == "'1'"
    assert os.environ.get("PYREWIND") is None
