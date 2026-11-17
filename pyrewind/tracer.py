"""파이썬 코드를 한 줄씩 실행하면서 변수 상태를 기록하는 트레이서.

sys.settrace 를 이용해 사용자 파일 안에서 일어나는
  - call      : 함수 호출 (새 프레임 생성, 인자 바인딩 완료 시점)
  - line      : 어떤 줄을 실행하기 '직전'
  - return    : 함수가 값을 반환
  - exception : 예외 발생
이벤트마다 호출 스택 전체의 지역 변수를 JSON 으로 직렬화해 저장한다.
"""
from __future__ import annotations

import base64
import builtins
import inspect
import json
import linecache
import os
import struct
import sys
import traceback
import types
import zlib
from collections import deque

from .errors import explain_error, suggest_fix

MAX_ITEMS = 40      # 리스트/딕셔너리에서 보여줄 최대 원소 수
MAX_DEPTH = 4       # 중첩 구조를 펼칠 최대 깊이
MAX_STR = 160       # repr 최대 길이

_GEN_FLAGS = inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR

# matplotlib 그림을 받을 콜백 (mpl_backend 가 사용). 실행 중에만 설정된다.
IMAGE_HOOK = None
_MPL_BACKEND = "module://pyrewind.mpl_backend"


def _emit_plotly(fig) -> bool:
    """plotly 그림을 (마우스로 돌려 볼 수 있게) 출력에 넣는다."""
    hook = IMAGE_HOOK
    if hook is None:
        return False
    try:
        hook({"plotly": fig.to_json()})
        return True
    except Exception:
        return False


def _patch_plotly() -> bool:
    """plotly 를 불러왔으면 fig.show() 가 브라우저 대신 PyRewind 출력으로 오게 한다."""
    pio = sys.modules.get("plotly.io")
    if pio is None:
        return False
    if getattr(pio, "_pyrewind_orig_show", None) is None:
        orig = pio.show

        def show(fig, *args, **kwargs):
            if not _emit_plotly(fig):
                return orig(fig, *args, **kwargs)
        pio._pyrewind_orig_show = orig
        pio.show = show
    return True


def _unpatch_plotly():
    pio = sys.modules.get("plotly.io")
    orig = getattr(pio, "_pyrewind_orig_show", None) if pio else None
    if orig is not None:
        pio.show = orig
        pio._pyrewind_orig_show = None


def _emit_pil(im) -> bool:
    hook = IMAGE_HOOK
    if hook is None:
        return False
    try:
        import io as _io
        x = im.copy()
        x.thumbnail((480, 480))
        if x.mode not in ("RGB", "RGBA", "L"):
            x = x.convert("RGBA" if "A" in x.mode else "RGB")
        buf = _io.BytesIO()
        x.save(buf, format="PNG")
        hook(base64.b64encode(buf.getvalue()).decode("ascii"))
        return True
    except Exception:
        return False


def _flush_figures():
    mb = sys.modules.get("pyrewind.mpl_backend")
    if mb is not None:
        try:
            mb.flush()
        except Exception:
            pass


class StepLimitReached(BaseException):
    """최대 단계 수 초과 (무한 루프 방지). 사용자의 except Exception 에 잡히지 않도록 BaseException."""


class FocusDone(BaseException):
    """선택 영역 실행이 끝남 (그 뒤 코드는 실행하지 않는다)."""


def _short(s: str, n: int = MAX_STR) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _safe_repr(v, n: int = MAX_STR) -> str:
    try:
        return _short(repr(v), n)
    except Exception:
        return f"<{type(v).__name__}>"


def _sorted_safe(items):
    try:
        return sorted(items)
    except Exception:
        return list(items)


class Encoder:
    """파이썬 값을 뷰어가 그릴 수 있는 JSON 구조로 변환."""

    def encode(self, v, depth: int = 0, path: tuple = ()):
        t = type(v)
        tn = t.__name__

        if v is None or isinstance(v, (bool, int, float, complex)):
            if t not in _BUILTIN_NUM and hasattr(v, "item"):  # np.float64(1.5) -> 1.5
                try:
                    return {"k": "prim", "t": tn, "r": _safe_repr(v.item())}
                except Exception:
                    pass
            return {"k": "prim", "t": tn, "r": _safe_repr(v)}
        if isinstance(v, str):
            return {"k": "prim", "t": "str", "r": _safe_repr(v)}
        if isinstance(v, (bytes, bytearray, range)):
            return {"k": "prim", "t": tn, "r": _safe_repr(v)}
        if isinstance(v, types.ModuleType):
            return {"k": "module", "t": "module", "r": v.__name__}
        if isinstance(v, (types.FunctionType, types.BuiltinFunctionType, types.MethodType)):
            name = getattr(v, "__name__", "?")
            try:
                sig = str(inspect.signature(v))
            except (TypeError, ValueError):
                sig = "(...)"
            return {"k": "func", "t": tn, "r": f"{name}{sig}"}
        if isinstance(v, type):
            return {"k": "type", "t": "class", "r": v.__name__}

        oid = id(v)
        if oid in path:
            return {"k": "ref", "t": tn, "id": oid}
        if depth >= MAX_DEPTH:
            return {"k": "more", "t": tn, "r": _safe_repr(v, 60), "id": oid}
        sub = path + (oid,)

        fields = getattr(t, "_fields", None) if isinstance(v, tuple) else None
        if fields and all(isinstance(f, str) for f in fields):
            attrs = [[f, self.encode(getattr(v, f, None), depth + 1, sub)] for f in list(fields)[:MAX_ITEMS]]
            return {"k": "obj", "t": tn, "id": oid, "attrs": attrs}

        if isinstance(v, (list, tuple, set, frozenset, deque)):
            src = _sorted_safe(v) if isinstance(v, (set, frozenset)) else v
            items = []
            for i, x in enumerate(src):
                if i >= MAX_ITEMS:
                    break
                items.append(self.encode(x, depth + 1, sub))
            return {"k": "seq", "t": tn, "id": oid, "len": len(v), "items": items}

        if isinstance(v, dict):
            items = []
            for i, (key, val) in enumerate(list(v.items())):
                if i >= MAX_ITEMS:
                    break
                items.append([self.encode(key, depth + 1, sub), self.encode(val, depth + 1, sub)])
            return {"k": "map", "t": tn, "id": oid, "len": len(v), "items": items}

        mod = (getattr(t, "__module__", "") or "").split(".")[0]
        if mod in ("numpy", "pandas"):
            enc = self._encode_numeric_lib(v, tn, oid, depth, sub)
            if enc is not None:
                return enc
        if mod in ("torch", "tensorflow", "keras", "jax", "jaxlib"):
            enc = self._encode_dl(v, tn, oid, mod)
            if enc is not None:
                return enc
        if mod in ("matplotlib", "plotly"):
            # 그림 객체의 내부 속성은 수백 개라 보기 어렵다 → 한 줄 요약 (그림 자체는 출력 칸에)
            return {"k": "prim", "t": tn, "r": _figure_summary(v, mod)}
        if mod == "PIL":
            enc = self._encode_pil(v, tn, oid)
            if enc is not None:
                return enc
        if mod in ("numpy", "pandas", "torch", "polars", "tensorflow", "keras", "jax", "jaxlib"):
            return {"k": "prim", "t": tn, "r": _safe_repr(v, 600)}

        d = getattr(v, "__dict__", None)
        if isinstance(d, dict) and not isinstance(v, types.GeneratorType):
            attrs = []
            for key, val in list(d.items()):
                if key.startswith("__"):
                    continue
                if len(attrs) >= MAX_ITEMS:
                    break
                attrs.append([key, self.encode(val, depth + 1, sub)])
            return {"k": "obj", "t": tn, "id": oid, "attrs": attrs}

        return {"k": "prim", "t": tn, "r": _safe_repr(v)}

    # --- numpy / pandas -------------------------------------------------
    def _encode_numeric_lib(self, v, tn, oid, depth, sub):
        try:
            # numpy 스칼라 (np.int64(5) -> 5)
            if getattr(v, "ndim", None) == 0 and hasattr(v, "item"):
                e = self.encode(v.item(), depth, sub)
                e["t"] = tn
                return e
            if tn == "ndarray":
                return _encode_array(v, f"ndarray[{v.dtype}]", oid)
            if tn == "DataFrame":
                r, c = v.shape
                part = v.iloc[:MAX_DF_ROWS, :MAX_DF_COLS]
                return {
                    "k": "df", "t": tn, "id": oid, "shape": [int(r), int(c)],
                    "cols": [_cell(x) for x in part.columns],
                    "dtypes": [str(d) for d in part.dtypes],
                    "index": [_cell(x) for x in part.index],
                    "index_name": _cell(v.index.name) if v.index.name is not None else "",
                    "rows": [[_cell(x) for x in row] for row in part.itertuples(index=False, name=None)],
                }
            if tn == "Series":
                part = v.iloc[:MAX_DF_ROWS]
                return {
                    "k": "series", "t": tn, "id": oid, "len": int(len(v)),
                    "name": _cell(v.name) if v.name is not None else "",
                    "dtype": str(v.dtype),
                    "index": [_cell(x) for x in part.index],
                    "values": [_cell(x) for x in part.tolist()],
                }
            if tn in ("Index", "RangeIndex", "DatetimeIndex", "MultiIndex"):
                items = [self.encode(x, depth + 1, sub) for x in list(v[:MAX_ITEMS])]
                return {"k": "seq", "t": tn, "id": oid, "len": int(len(v)), "items": items}
        except Exception:
            return None
        return None


    # --- 딥러닝 텐서 / 모델 (torch, tensorflow, jax) ----------------------
    def _encode_dl(self, v, tn, oid, mod):
        try:
            # 모델: 구조 출력(repr)이 가장 알아보기 쉽다
            if mod == "torch" and hasattr(v, "named_parameters") and hasattr(v, "forward"):
                n = sum(int(p.numel()) for p in v.parameters())
                return {"k": "text", "t": tn, "id": oid, "r": _short(repr(v), 3000),
                        "note": f"학습할 값(파라미터) {n:,}개"}
            if hasattr(v, "count_params") and hasattr(v, "layers"):  # keras 모델
                lines = [f"{type(l).__name__}  {getattr(l, 'name', '')}" for l in list(v.layers)[:60]]
                return {"k": "text", "t": tn, "id": oid, "r": "\n".join(lines) or tn,
                        "note": f"학습할 값(파라미터) {int(v.count_params()):,}개"}
            shape = getattr(v, "shape", None)
            if shape is None or not hasattr(v, "dtype"):
                return None
            shape = tuple(int(x) for x in shape)
            dt = v.dtype
            dtype = getattr(dt, "name", None) if mod in ("tensorflow", "keras") else None
            dtype = dtype or str(dt).replace("torch.", "")
            info = [dtype]
            dev = getattr(v, "device", None)
            if dev is not None and mod == "torch":
                info.append(str(dev))
            elif isinstance(dev, str) and "GPU" in dev.upper():
                info.append("GPU")
            if getattr(v, "requires_grad", False):
                info.append("grad")
            label = f"{tn}[{', '.join(info)}]"
            a, approx = _tensor_to_numpy(v, shape, mod)
            if a is None:
                return None
            return _encode_array(a, label, oid, full_shape=shape, approx=approx,
                                 stats=_tensor_stats(v, mod) if approx else None)
        except Exception:
            return None

    # --- PIL 이미지 ----------------------------------------------------
    def _encode_pil(self, v, tn, oid):
        if not (hasattr(v, "size") and hasattr(v, "mode") and hasattr(v, "getpixel")):
            return None
        try:
            w, h = v.size
            key = ("pil", id(v), v.size, v.mode, _pil_probe(v))
            src = _PNG_CACHE.get(key)
            if src is None:
                import io as _io
                im = v.copy()
                im.thumbnail((THUMB, THUMB))
                if im.mode not in ("RGB", "RGBA", "L"):
                    im = im.convert("RGBA" if "A" in im.mode else "RGB")
                buf = _io.BytesIO()
                im.save(buf, format="PNG")
                src = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
                _cache_put(key, src)
            ch = len(v.getbands())
            return {"k": "nd", "t": f"{tn}[{v.mode}]", "id": oid,
                    "shape": [h, w] + ([ch] if ch > 1 else []),
                    "dims": "높이 × 너비" + (" × 채널" if ch > 1 else ""),
                    "slices": [{"l": "", "src": src, "w": w, "h": h}], "total": 1, "stats": None,
                    "photo": True}
        except Exception:
            return None


MAX_DF_ROWS = 12
MAX_DF_COLS = 12
MAX_ARR = 20
THUMB = 128          # 그림으로 보여줄 때 가장 긴 변 (픽셀)
MAX_SLICES = 16      # 3차원 이상 배열에서 그림으로 보여줄 최대 장 수
_BUILTIN_NUM = (bool, int, float, complex, type(None))
_PNG_CACHE: dict = {}


def _figure_summary(v, mod) -> str:
    try:
        if mod == "plotly" and hasattr(v, "data") and hasattr(v, "layout"):
            kinds = [type(tr).__name__ for tr in v.data]
            title = getattr(getattr(v.layout, "title", None), "text", None)
            parts = [f"그래프 {len(kinds)}개: {', '.join(kinds[:5])}" if kinds else "빈 그림"]
            if title:
                parts.append(f"제목 '{title}'")
            return " · ".join(parts) + "  (fig.show() 하면 출력 칸에 표시)"
    except Exception:
        pass
    return _safe_repr(v, 200)


def _cache_put(key, val):
    if len(_PNG_CACHE) > 4000:
        _PNG_CACHE.clear()
    _PNG_CACHE[key] = val


def _pil_probe(im):
    """PIL 이미지가 제자리에서 바뀌었는지 대략 알기 위해 몇 픽셀을 읽는다."""
    w, h = im.size
    if not w or not h:
        return None
    pts = [(x * (w - 1) // 3, y * (h - 1) // 3) for x in range(4) for y in range(4)]
    try:
        return tuple(im.getpixel(p) for p in pts)
    except Exception:
        return None


# ---------------------------------------------------------------- 배열 → 표 / 그림

def _encode_array(a, label, oid, full_shape=None, approx=False, stats=None):
    """numpy 배열 (또는 텐서를 numpy 로 바꾼 것) 을 뷰어 형식으로.
    1차원 → 칸, 2차원 → 표 (+ 충분히 크면 그림), 3차원 이상 → 장(slice)별 그림."""
    import numpy as np
    shape = tuple(int(x) for x in (full_shape if full_shape is not None else a.shape))
    if a.ndim == 0:
        return {"k": "prim", "t": label, "r": _cell(a.item())}
    if a.ndim == 1:
        items = [{"k": "prim", "t": str(a.dtype), "r": _cell(x)} for x in a[:MAX_ARR].tolist()]
        return {"k": "seq", "t": label, "id": oid, "len": shape[0], "items": items}
    if a.ndim >= 3 and sum(1 for x in a.shape if x > 1) <= 2 and not approx:
        # (64, 1, 1) 이나 (1, 1, 28, 28) 처럼 크기 1인 축을 빼면 1·2차원인 배열은 그 모양으로
        sq = a.reshape([x for x in a.shape if x > 1] or [1])
        return _encode_array(sq, f"{label} {tuple(shape)}", oid)
    numeric = a.dtype.kind in "biuf"
    if a.ndim == 2:
        r, c = shape
        part = a[:MAX_DF_ROWS, :MAX_DF_COLS]
        e = {
            "k": "df", "t": label, "id": oid, "shape": [r, c],
            "cols": [str(j) for j in range(part.shape[1])], "dtypes": [""] * part.shape[1],
            "index": [str(i) for i in range(part.shape[0])], "index_name": "",
            "rows": [[_cell(x) for x in row] for row in part.tolist()],
        }
        if numeric and min(a.shape) >= 8:   # 28×28 손글씨 같은 2차원 그림
            lo, hi = _range(a, np)
            heat = _is_heat(a, np)
            e["img"] = _png_src(_colorize(_to_u8(_shrink(a, np), lo, hi, np), lo, hi, heat, np), np)
            if heat:
                e["heat"] = _heat_info(lo, hi)
        return e
    if not numeric:
        return {"k": "prim", "t": f"{label} shape={shape}", "r": _safe_repr(a, 300)}
    planes, total, dims = _planes(a, shape, np)
    lo, hi = _range(a, np)
    color = bool(planes) and planes[0][1].ndim == 3
    heat = not color and _is_heat(a, np)
    slices = []
    for lab, pl in planes:
        small = _shrink(pl, np)
        u8 = _to_u8(small, lo, hi, np)
        sl = {"l": lab, "src": _png_src(_colorize(u8, lo, hi, heat, np), np),
              "w": int(pl.shape[1]), "h": int(pl.shape[0])}
        if pl.ndim == 2 and pl.shape[0] * pl.shape[1] <= 64:   # 3×3 필터처럼 작으면 숫자도
            sl["vals"] = [[_cell(x) for x in row] for row in pl.tolist()]
        slices.append(sl)
    if stats is None:
        stats = _np_stats(a, np, approx)
    e = {"k": "nd", "t": label, "id": oid, "shape": list(shape), "dims": dims,
         "slices": slices, "total": total, "stats": stats}
    if color:
        e["photo"] = True      # 히트맵을 겹쳐 볼 수 있는 사진
    if heat:
        e["heat"] = _heat_info(lo, hi)
    return e


# 히트맵 색 (matplotlib 의 viridis 와 RdBu 를 본뜬 것)
_VIRIDIS = [(68, 1, 84), (72, 40, 120), (62, 74, 137), (49, 104, 142), (38, 130, 142),
            (31, 158, 137), (53, 183, 121), (109, 205, 89), (180, 222, 44), (253, 231, 37)]
_RDBU = [(5, 48, 97), (33, 102, 172), (67, 147, 195), (146, 197, 222), (209, 229, 240), (247, 247, 247),
         (253, 219, 199), (244, 165, 130), (214, 96, 77), (178, 24, 43), (103, 0, 31)]
_LUTS: dict = {}


def _lut(name, np):
    if name not in _LUTS:
        anchors = np.array(_VIRIDIS if name == "viridis" else _RDBU, dtype=np.float64)
        xs = np.linspace(0, 255, len(anchors))
        _LUTS[name] = np.stack([np.interp(np.arange(256), xs, anchors[:, c]) for c in range(3)], axis=1).astype(np.uint8)
    return _LUTS[name]


def _is_heat(a, np) -> bool:
    """실수 값의 한 채널 배열 (특징 맵, Grad-CAM, 가중치 등) 은 색 히트맵으로 그린다.
    (0~255 정수 / bool 은 흑백 사진으로)"""
    return a.dtype.kind == "f"


def _heat_info(lo, hi):
    signed = lo < 0 < hi
    return {"cmap": "RdBu" if signed else "viridis", "lo": _cell(float(lo)), "hi": _cell(float(hi))}


def _colorize(u8, lo, hi, heat, np):
    if not heat or u8.ndim != 2:
        return u8
    if lo < 0 < hi:  # 0 이 흰색 가운데가 되도록 다시 맞춤 (음수 파랑, 양수 빨강)
        m = max(-lo, hi)
        x = (u8.astype(np.float64) * ((hi - lo) / 255.0) + lo) / m        # -1 ~ 1
        idx = np.clip((x + 1) * 127.5, 0, 255).astype(np.uint8)
        return np.ascontiguousarray(_lut("RdBu", np)[idx])
    return np.ascontiguousarray(_lut("viridis", np)[u8])


def _planes(a, shape, np):
    """그림으로 그릴 2차원(또는 컬러) 장 목록, 전체 장 수, 축 설명."""
    s = a.shape

    def color(n):
        return n in (3, 4)

    if a.ndim == 3:
        if color(s[2]) and s[0] > 4 and s[1] > 4:
            return [("", a)], 1, "높이 × 너비 × 색(RGB)"
        if s[2] == 1:
            return [("", a[..., 0])], 1, "높이 × 너비 × 1"
        if s[0] == 3 and s[1] > 4 and s[2] > 4:   # 채널 먼저(CHW)는 RGB 3채널만 (4채널은 보통 특징 맵)
            return [("", a.transpose(1, 2, 0))], 1, "색(RGB) × 높이 × 너비"
        return [(f"[{i}]", a[i]) for i in range(min(s[0], MAX_SLICES))], shape[0], "장 × 높이 × 너비"
    if a.ndim == 4:
        n = shape[0]
        k = min(s[0], MAX_SLICES)
        if color(s[3]) and s[1] > 4 and s[2] > 4:
            return [(f"[{i}]", a[i]) for i in range(k)], n, "개수 × 높이 × 너비 × 색"
        if s[1] == 3 and s[2] > 4 and s[3] > 4:
            return [(f"[{i}]", a[i].transpose(1, 2, 0)) for i in range(k)], n, "개수 × 색 × 높이 × 너비"
        if s[1] == 1:
            return [(f"[{i}]", a[i, 0]) for i in range(k)], n, "개수 × 1 × 높이 × 너비"
        if s[3] == 1:
            return [(f"[{i}]", a[i, ..., 0]) for i in range(k)], n, "개수 × 높이 × 너비 × 1"
        out = [(f"[{i}, {j}]", a[i, j]) for i in range(s[0]) for j in range(s[1])][:MAX_SLICES]
        return out, shape[0] * shape[1], "개수 × 채널 × 높이 × 너비"
    lead = a.reshape(-1, s[-2], s[-1])
    total = 1
    for x in shape[:-2]:
        total *= x
    out = []
    for i in range(min(lead.shape[0], MAX_SLICES)):
        idx = np.unravel_index(i, s[:-2])
        out.append((f"[{', '.join(str(int(x)) for x in idx)}]", lead[i]))
    return out, total, " × ".join(["…"] * (a.ndim - 2) + ["높이", "너비"])


def _shrink(pl, np):
    h, w = pl.shape[0], pl.shape[1]
    st = max(1, -(-max(h, w) // THUMB))
    return pl[::st, ::st] if st > 1 else pl


def _range(a, np):
    """그림 밝기 기준. 0~1 실수 / 0~255 정수 그림은 그대로, 나머지는 최솟값~최댓값."""
    if a.dtype == np.uint8:
        return 0.0, 255.0
    if a.dtype == np.bool_:
        return 0.0, 1.0
    sample = a if a.size <= 2_000_000 else a.reshape(-1)[:: a.size // 1_000_000 + 1]
    with np.errstate(all="ignore"):
        lo, hi = float(np.nanmin(sample)), float(np.nanmax(sample))
    if lo != lo or hi != hi:
        return 0.0, 1.0
    if a.dtype.kind == "f" and lo >= 0 and hi <= 1:
        return 0.0, 1.0
    if a.dtype.kind in "iu" and lo >= 0 and 1 < hi <= 255:
        return 0.0, 255.0
    return lo, hi


def _to_u8(pl, lo, hi, np):
    if pl.dtype == np.uint8:
        return np.ascontiguousarray(pl)
    x = np.nan_to_num(pl.astype(np.float64), nan=lo)
    if hi <= lo:
        return np.full(pl.shape, 128 if hi else 0, dtype=np.uint8)
    return np.ascontiguousarray(((x - lo) * (255.0 / (hi - lo))).clip(0, 255).astype(np.uint8))


def _png_src(u8, np):
    """uint8 배열 (높이×너비 또는 높이×너비×3/4) → PNG data URL. 같은 그림은 캐시."""
    key = ("png", u8.shape, hash(u8.tobytes()))
    src = _PNG_CACHE.get(key)
    if src is None:
        src = "data:image/png;base64," + base64.b64encode(_png_bytes(u8, np)).decode("ascii")
        _cache_put(key, src)
    return src


def _png_bytes(u8, np):
    h, w = u8.shape[:2]
    ch = 1 if u8.ndim == 2 else u8.shape[2]
    color = {1: 0, 3: 2, 4: 6}[ch]
    rows = u8.reshape(h, w * ch)
    raw = np.concatenate([np.zeros((h, 1), dtype=np.uint8), rows], axis=1).tobytes()

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _np_stats(a, np, approx=False):
    try:
        sample = a if a.size <= 2_000_000 else a.reshape(-1)[:: a.size // 1_000_000 + 1]
        with np.errstate(all="ignore"):
            st = {"min": _cell(float(np.nanmin(sample))), "max": _cell(float(np.nanmax(sample))),
                  "mean": _cell(float(np.nanmean(sample)))}
        if approx or sample is not a:
            st["approx"] = True
        return st
    except Exception:
        return None


def _tensor_to_numpy(v, shape, mod):
    """텐서를 numpy 로. 아주 크면 앞쪽 일부만 잘라서 (복사 비용을 줄이려고) 바꾼다."""
    numel = 1
    for x in shape:
        numel *= x
    approx = False
    x = v
    if numel > 2_000_000 and len(shape) >= 3:
        x = v[tuple(slice(0, MAX_SLICES) for _ in range(len(shape) - 2))]
        approx = True
    if mod == "torch":
        x = x.detach()
        if str(x.dtype) in ("torch.bfloat16", "torch.float16"):
            x = x.float()
        return x.cpu().numpy(), approx
    if hasattr(x, "numpy"):
        return x.numpy(), approx
    import numpy as np
    return np.asarray(x), approx


def _tensor_stats(v, mod):
    try:
        if mod == "torch":
            f = v.detach().float()
            return {"min": _cell(f.min().item()), "max": _cell(f.max().item()), "mean": _cell(f.mean().item())}
    except Exception:
        pass
    return None


def _cell(x) -> str:
    """DataFrame 칸 하나를 pandas 출력처럼 짧은 문자열로."""
    if isinstance(x, str):
        return _short(x, 40)
    if isinstance(x, tuple):
        return " / ".join(_cell(y) for y in x)
    if isinstance(x, float):
        if x != x:
            return "NaN"
        return f"{x:.4g}" if abs(x) < 1e6 else f"{x:.3e}"
    try:
        return _short(str(x), 40)
    except Exception:
        return f"<{type(x).__name__}>"


def _vcr_display(value):
    """노트북 셀의 마지막 식 값을 출력 (주피터의 Out[] 흉내). 그림은 그림으로."""
    if value is None:
        return
    mod = (type(value).__module__ or "").split(".")[0]
    if mod == "plotly" and hasattr(value, "to_json") and hasattr(value, "data") and _emit_plotly(value):
        return
    if mod == "PIL" and hasattr(value, "getpixel") and _emit_pil(value):
        return
    print(repr(value))


def _jupyter_display(*values, **_kwargs):
    """주피터의 display() 대용 (노트북을 실행할 때만 제공)."""
    for v in values:
        _vcr_display(v)


def _visible(name: str, value) -> bool:
    if name.startswith("__") and name.endswith("__"):
        return False
    if isinstance(value, types.ModuleType):
        return False
    return True


class OutputBuffer:
    def __init__(self):
        self.parts: list[str] = []
        self.length = 0

    def append(self, s: str):
        self.parts.append(s)
        self.length += len(s)

    def text(self) -> str:
        return "".join(self.parts)


class Tee:
    """print 출력을 터미널에도 보내고 기록도 한다."""

    def __init__(self, real, sink: OutputBuffer):
        self.real = real
        self.sink = sink
        self.encoding = "utf-8"
        self.errors = "replace"

    def write(self, s):
        s = str(s)
        if self.real is not None:
            try:
                self.real.write(s)
            except UnicodeEncodeError:
                enc = getattr(self.real, "encoding", None) or "utf-8"
                self.real.write(s.encode(enc, "replace").decode(enc))
            except Exception:
                pass
        self.sink.append(s)
        return len(s)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        if self.real is not None:
            try:
                self.real.flush()
            except Exception:
                pass

    def isatty(self):
        return False


class Tracer:
    def __init__(self, filename: str, max_steps: int, sink: OutputBuffer):
        self.filename = filename
        self.max_steps = max_steps
        self.sink = sink
        self.enc = Encoder()
        self.steps: list[dict] = []
        self.fids: dict = {}
        self.next_fid = 1
        self.last_event: dict[int, str] = {}
        self.truncated = False
        # 같은 값은 한 번만 저장 (단계마다 큰 DataFrame 이 반복 저장되는 것 방지)
        self.values: list = []
        self.value_ids: dict[str, int] = {}
        # 노트북 셀 경계 (셀이 바뀌면 남은 그래프를 출력 - 주피터와 같은 동작)
        self.cells: list = []
        self.last_cell = None
        # 선택 영역만 기록 (R / Spyder 의 '선택 실행'): (시작 줄, 끝 줄) 또는 None
        self.focus: tuple[int, int] | None = None
        self.focus_started = False
        self.focus_from_module = False   # 전역 코드에서 시작했으면 영역이 끝날 때 실행을 멈춘다
        self.focus_frames: set = set()   # 선택 영역에서 호출한 함수 프레임 (안쪽도 기록)
        self.plotly_ready = False
        # 반복 기록 제한: 같은 줄이 loop_limit 번 넘게 실행되면 그 뒤로는 기록 없이 실행
        self.loop_limit = 0
        self.line_hits: dict = {}
        self.muted: set = set()          # 지금 기록을 건너뛰는 프레임
        self.skipped = 0                 # 마지막 기록 이후 건너뛴 줄 수

    # --- 스냅샷 ---------------------------------------------------------
    def _frame_info(self, f) -> dict:
        co = f.f_code
        name = getattr(co, "co_qualname", co.co_name)
        data, funcs = [], []
        try:
            items = list(f.f_locals.items())
        except Exception:
            items = []
        for k, v in items:
            if not _visible(k, v):
                continue
            e = self.enc.encode(v)
            (funcs if e["k"] in ("func", "type") else data).append([k, self._intern(e)])
        return {"fid": self.fids[f], "name": name, "line": f.f_lineno, "vars": data + funcs}

    def _intern(self, e) -> int:
        key = json.dumps(e, ensure_ascii=False, separators=(",", ":"))
        idx = self.value_ids.get(key)
        if idx is None:
            idx = len(self.values)
            self.values.append(e)
            self.value_ids[key] = idx
        return idx

    def _in_focus(self, frame) -> bool:
        return (frame.f_code.co_filename == self.filename
                and self.focus[0] <= frame.f_lineno <= self.focus[1])

    def _should_record(self, frame, event) -> bool:
        """선택 영역 모드에서 이 이벤트를 기록할지. (선택 영역이 없으면 항상 기록)"""
        if self.focus is None:
            return True
        if event == "call":
            caller = frame.f_back
            if self.focus_started and caller is not None and (caller in self.focus_frames or self._in_focus(caller)):
                self.focus_frames.add(frame)
                return True
            return False
        if event == "line":
            if self._in_focus(frame):
                if not self.focus_started:
                    self.focus_started = True
                    self.focus_from_module = frame.f_code.co_name == "<module>"
                return True
            if (self.focus_started and self.focus_from_module and frame.f_code.co_name == "<module>"
                    and frame.f_lineno > self.focus[1]):
                # 마지막 선택 줄을 실행한 뒤의 상태를 남기고, 다음 줄부터는 실행하지 않음
                self._record(frame, "focus_end", None)
                raise FocusDone()
            return frame in self.focus_frames
        return frame in self.focus_frames or (self.focus_started and self._in_focus(frame))

    def _limited(self, frame, event) -> bool:
        """반복 기록 제한에 걸려 이번 이벤트를 기록하지 않을지."""
        if event == "call":
            caller = frame.f_back
            n = self.line_hits.get(frame.f_code, 0) + 1   # 같은 함수를 부른 횟수
            self.line_hits[frame.f_code] = n
            if (caller is not None and caller in self.muted) or n > self.loop_limit:
                self.muted.add(frame)   # 건너뛰는 중에 부른 함수 / N번 넘게 부른 함수도 건너뜀
                self.skipped += 1
                return True
            return False
        if event == "line":
            key = (frame.f_code, frame.f_lineno)
            n = self.line_hits.get(key, 0) + 1
            self.line_hits[key] = n
            if n > self.loop_limit or (frame in self.muted and frame.f_back in self.muted):
                self.muted.add(frame)
                self.skipped += 1
                return True
            self.muted.discard(frame)   # 반복문을 빠져나와 처음 보는 줄 → 다시 기록
            return False
        if event == "return":
            if frame in self.muted:
                self.muted.discard(frame)
                return True
            return False
        return False   # exception 은 항상 기록 (오류가 난 순간을 보여주려고)

    def _cell_of(self, line):
        for i, c in enumerate(self.cells):
            if c["start"] <= line <= c["end"]:
                return i
        return None

    def _stack(self, frame) -> list[dict]:
        frames = []
        f = frame
        while f is not None:
            if f.f_code.co_filename == self.filename and f in self.fids:
                frames.append(f)
            f = f.f_back
        frames.reverse()
        return [self._frame_info(x) for x in frames]

    def _record(self, frame, event, arg):
        if len(self.steps) >= self.max_steps:
            self.truncated = True
            raise StepLimitReached()
        fid = self.fids[frame]
        co = frame.f_code
        step = {
            "event": event,
            "line": frame.f_lineno,
            "fid": fid,
            "func": getattr(co, "co_qualname", co.co_name),
            "stack": self._stack(frame),
            "out": self.sink.length,
        }
        if self.skipped:
            step["skipped"] = self.skipped   # 이 단계 전에 기록 없이 실행한 줄 수
            self.skipped = 0
        if event == "return":
            if self.last_event.get(fid) == "exception":
                step["unwind"] = True  # 예외 때문에 함수가 중단됨
            else:
                step["ret"] = self.enc.encode(arg)
        elif event == "exception":
            exc_type, exc, _tb = arg
            step["exc"] = f"{exc_type.__name__}: {exc}"
        self.last_event[fid] = event
        self.steps.append(step)

    # --- sys.settrace 콜백 ---------------------------------------------
    def trace(self, frame, event, arg):
        if frame.f_code.co_filename != self.filename:
            return None  # 라이브러리 내부는 추적하지 않음
        if event == "call" or frame not in self.fids:
            if frame not in self.fids:
                self.fids[frame] = self.next_fid
                self.next_fid += 1
        if not self.plotly_ready and event == "line" and "plotly.io" in sys.modules:
            self.plotly_ready = _patch_plotly()
        if self.cells and event == "line" and frame.f_code.co_name == "<module>":
            cell = self._cell_of(frame.f_lineno)
            if cell is not None and cell != self.last_cell:
                if self.last_cell is not None:
                    _flush_figures()  # 이전 셀에서 그린 그래프를 출력
                self.last_cell = cell
        if event in ("call", "line", "return", "exception") and self._should_record(frame, event):
            if not (self.loop_limit and self._limited(frame, event)):
                self._record(frame, event, arg)
        if event == "return" and not (frame.f_code.co_flags & _GEN_FLAGS):
            self.fids.pop(frame, None)
            self.focus_frames.discard(frame)
        return self.trace


def _format_error(e: BaseException, filename: str) -> dict:
    # 오류가 난 사용자 코드 프레임 (이름 추천에 그 순간의 변수 목록을 쓰기 위해)
    frame = None
    t = e.__traceback__
    while t is not None:
        if t.tb_frame.f_code.co_filename == filename:
            frame = t.tb_frame
        t = t.tb_next
    tb = traceback.extract_tb(e.__traceback__)
    user = [fs for fs in tb if fs.filename == filename]
    line = user[-1].lineno if user else None
    text = "Traceback (most recent call last):\n"
    text += "".join(traceback.format_list(user))
    text += "".join(traceback.format_exception_only(type(e), e))
    msg = str(e)
    if isinstance(e, KeyError) and e.args:
        msg = repr(e.args[0])
    return {"type": type(e).__name__, "msg": msg, "line": line, "tb": text,
            "hint": explain_error(type(e).__name__, msg),
            "suggest": suggest_fix(e, frame)}


def run_trace(path: str, source: str, *, max_steps: int = 3000,
              stdin_lines: list[str] | None = None, echo: bool = True,
              display_name: str | None = None, cells: list | None = None,
              display_source: str | None = None, focus: tuple[int, int] | None = None,
              loop_limit: int = 0) -> dict:
    """source 를 실행한다. display_source 가 있으면 화면에는 그것을 보여준다 (줄 번호는 같아야 함).

    focus=(시작 줄, 끝 줄) 을 주면 그 앞의 코드는 기록 없이 실행해 변수를 준비하고,
    선택 영역만 한 줄씩 기록한 뒤 (전역 코드 기준) 영역이 끝나면 멈춘다.
    """
    filename = os.path.abspath(path)
    result = {
        "version": 1,
        "file": display_name or os.path.basename(path),
        "path": filename,
        "source": display_source if display_source is not None else source,
        "cells": cells,
        "python": sys.version.split()[0],
        "steps": [],
        "output": "",
        "error": None,
        "truncated": False,
        "max_steps": max_steps,
        "focus": {"start": focus[0], "end": focus[1]} if focus else None,
        "loop_limit": loop_limit or 0,
    }

    try:
        code = compile(source, filename, "exec")
    except SyntaxError as e:
        result["error"] = {
            "type": type(e).__name__, "msg": e.msg, "line": e.lineno,
            "tb": "".join(traceback.format_exception_only(type(e), e)),
            "hint": explain_error(type(e).__name__, e.msg or ""),
        }
        return result

    # 오류 메시지나 경고에 실제 코드 줄이 나오도록 (노트북은 파일 내용이 JSON 이라서 필요)
    shown = result["source"]
    linecache.cache[filename] = (len(shown), None, shown.splitlines(True), filename)

    sink = OutputBuffer()

    # matplotlib: 창을 띄우지 않고 그림을 PNG 로 받아 출력에 넣는다
    global IMAGE_HOOK
    images: list = []

    def on_image(png_b64):  # base64 PNG 문자열, 또는 {"plotly": json}
        images.append(png_b64)
        sink.append(f"\x00img:{len(images) - 1}\x00")
        if echo:
            try:
                old_out.write(f"[그래프 {len(images)}]\n")
            except Exception:
                pass

    old_backend = os.environ.get("MPLBACKEND")
    os.environ["MPLBACKEND"] = _MPL_BACKEND
    # matplotlib 을 이미 불러온 프로세스라면 환경 변수는 무시되므로 직접 바꾼다.
    # (안 바꾸면 Windows / macOS 에서 plt.show() 가 창을 띄우고 닫힐 때까지 멈춤)
    if "matplotlib" in sys.modules:
        try:
            sys.modules["matplotlib"].use(_MPL_BACKEND, force=True)
        except Exception:
            pass
    IMAGE_HOOK = on_image

    old_out, old_err, old_input = sys.stdout, sys.stderr, builtins.input
    old_argv, old_cwd, old_path = sys.argv, os.getcwd(), list(sys.path)
    # 코드가 "지금 PyRewind 로 실행 중" 인지 알 수 있게 (예: 시각화할 때만 데이터를 작게)
    old_flag = os.environ.get("PYREWIND")
    os.environ["PYREWIND"] = "1"
    pending = list(stdin_lines) if stdin_lines is not None else None

    def traced_input(prompt=""):
        sys.stdout.write(str(prompt))
        sys.stdout.flush()
        if pending is not None:
            if not pending:
                raise EOFError("input() 에 넣을 값이 더 없습니다 (--stdin 파일 확인)")
            val = pending.pop(0)
            if echo:
                old_out.write(val + "\n")
        else:
            val = sys.__stdin__.readline() if sys.__stdin__ else ""
            if val == "":
                raise EOFError("입력이 없습니다")
            val = val.rstrip("\r\n")
        sink.append(val + "\n")
        return val

    tracer = Tracer(filename, max_steps, sink)
    tracer.cells = cells or []
    tracer.focus = focus
    tracer.loop_limit = max(0, int(loop_limit or 0))
    g = {"__name__": "__main__", "__file__": filename, "__builtins__": builtins,
         "__pyrewind_display__": _vcr_display}

    sys.stdout = Tee(old_out if echo else None, sink)
    sys.stderr = Tee(old_err if echo else None, sink)
    builtins.input = traced_input
    # 노트북이면 주피터 내장 display() 를 흉내 (변수 목록에 안 보이도록 builtins 에 넣음)
    had_display = hasattr(builtins, "display")
    if cells is not None and not had_display:
        builtins.display = _jupyter_display
    sys.argv = [filename]
    script_dir = os.path.dirname(filename)
    sys.path.insert(0, script_dir)
    try:
        os.chdir(script_dir)
    except OSError:
        pass

    sys.settrace(tracer.trace)
    try:
        exec(code, g)
    except (StepLimitReached, FocusDone):
        pass
    except SystemExit:
        pass
    except BaseException as e:  # noqa: BLE001 - 사용자 코드 오류 전부 기록
        result["error"] = _format_error(e, filename)
    finally:
        sys.settrace(None)
        _flush_figures()  # 마지막 셀에서 그리고 show() 안 한 그래프
        _unpatch_plotly()
        IMAGE_HOOK = None
        if old_flag is None:
            os.environ.pop("PYREWIND", None)
        else:
            os.environ["PYREWIND"] = old_flag
        if old_backend is None:
            os.environ.pop("MPLBACKEND", None)
        else:
            os.environ["MPLBACKEND"] = old_backend
        sys.stdout, sys.stderr, builtins.input = old_out, old_err, old_input
        if cells is not None and not had_display:
            del builtins.display
        sys.argv = old_argv
        sys.path[:] = old_path
        try:
            os.chdir(old_cwd)
        except OSError:
            pass

    result["steps"] = tracer.steps
    result["values"] = tracer.values
    result["images"] = images
    result["output"] = sink.text()
    result["truncated"] = tracer.truncated
    return result
