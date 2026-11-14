"""공용 픽스처와 도우미."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from visual_code_runner.tracer import run_trace

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


@pytest.fixture
def trace_code(tmp_path):
    """코드 문자열을 임시 파일로 저장하고 추적한 결과를 돌려준다."""
    def _run(code: str, name: str = "prog.py", **kwargs):
        src = textwrap.dedent(code).lstrip("\n")
        path = tmp_path / name
        path.write_text(src, encoding="utf-8")
        kwargs.setdefault("echo", False)
        return run_trace(str(path), src, **kwargs)
    return _run


def var(trace: dict, step: int, name: str, frame: int = -1):
    """step 번째 단계, frame 번째 프레임(기본: 가장 안쪽)의 변수 값을 돌려준다."""
    f = trace["steps"][step]["stack"][frame]
    for n, idx in f["vars"]:
        if n == name:
            return trace["values"][idx]
    return None


def final_globals(trace: dict) -> dict:
    """마지막 단계의 전역 변수 {이름: 인코딩된 값}."""
    f = trace["steps"][-1]["stack"][0]
    return {n: trace["values"][idx] for n, idx in f["vars"]}
