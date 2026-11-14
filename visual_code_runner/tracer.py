"""파이썬 코드를 한 줄씩 실행하면서 변수 상태를 기록하는 트레이서.

sys.settrace 를 이용해 사용자 파일 안에서 일어나는
  - call      : 함수 호출 (새 프레임 생성, 인자 바인딩 완료 시점)
  - line      : 어떤 줄을 실행하기 '직전'
  - return    : 함수가 값을 반환
  - exception : 예외 발생
이벤트마다 호출 스택 전체의 지역 변수를 JSON 으로 직렬화해 저장한다.
"""
from __future__ import annotations

import builtins
import inspect
import json
import linecache
import os
import sys
import traceback
import types
from collections import deque

from .errors import explain_error, suggest_fix

MAX_ITEMS = 40      # 리스트/딕셔너리에서 보여줄 최대 원소 수
MAX_DEPTH = 4       # 중첩 구조를 펼칠 최대 깊이
MAX_STR = 160       # repr 최대 길이

_GEN_FLAGS = inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR

# matplotlib 그림을 받을 콜백 (mpl_backend 가 사용). 실행 중에만 설정된다.
IMAGE_HOOK = None
_MPL_BACKEND = "module://visual_code_runner.mpl_backend"


def _flush_figures():
    mb = sys.modules.get("visual_code_runner.mpl_backend")
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
        if mod in ("numpy", "pandas", "torch", "polars"):
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
                label = f"ndarray[{v.dtype}]"
                if v.ndim == 1:  # 1차원: 칸으로 (앞쪽 MAX_ARR 개)
                    items = [{"k": "prim", "t": str(v.dtype), "r": _cell(x)} for x in v[:MAX_ARR].tolist()]
                    return {"k": "seq", "t": label, "id": oid, "len": int(v.size), "items": items}
                if v.ndim == 2:  # 2차원: 표로 (DataFrame 과 같은 모양)
                    r, c = v.shape
                    part = v[:MAX_DF_ROWS, :MAX_DF_COLS]
                    return {
                        "k": "df", "t": label, "id": oid, "shape": [int(r), int(c)],
                        "cols": [str(j) for j in range(part.shape[1])], "dtypes": [""] * part.shape[1],
                        "index": [str(i) for i in range(part.shape[0])], "index_name": "",
                        "rows": [[_cell(x) for x in row] for row in part.tolist()],
                    }
                return {"k": "prim", "t": f"{label} shape={v.shape}", "r": _safe_repr(v, 300)}
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


MAX_DF_ROWS = 12
MAX_DF_COLS = 12
MAX_ARR = 20


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
    """노트북 셀의 마지막 식 값을 출력 (주피터의 Out[] 흉내)."""
    if value is not None:
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
        if self.cells and event == "line" and frame.f_code.co_name == "<module>":
            cell = self._cell_of(frame.f_lineno)
            if cell is not None and cell != self.last_cell:
                if self.last_cell is not None:
                    _flush_figures()  # 이전 셀에서 그린 그래프를 출력
                self.last_cell = cell
        if event in ("call", "line", "return", "exception") and self._should_record(frame, event):
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
              display_source: str | None = None, focus: tuple[int, int] | None = None) -> dict:
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
    images: list[str] = []

    def on_image(png_b64: str):
        images.append(png_b64)
        sink.append(f"\x00img:{len(images) - 1}\x00")
        if echo:
            try:
                old_out.write(f"[그래프 {len(images)}]\n")
            except Exception:
                pass

    old_backend = os.environ.get("MPLBACKEND")
    os.environ["MPLBACKEND"] = _MPL_BACKEND
    IMAGE_HOOK = on_image

    old_out, old_err, old_input = sys.stdout, sys.stderr, builtins.input
    old_argv, old_cwd, old_path = sys.argv, os.getcwd(), list(sys.path)
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
    g = {"__name__": "__main__", "__file__": filename, "__builtins__": builtins,
         "__vcr_display__": _vcr_display}

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
        IMAGE_HOOK = None
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
