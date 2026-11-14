"""matplotlib 백엔드: plt.show() 를 부르면 창을 띄우는 대신 그림을 PNG 로 저장해 시각화 화면에 넣는다.

MPLBACKEND=module://visual_code_runner.mpl_backend 로 지정해서 사용한다.
트레이서가 tracer.IMAGE_HOOK 에 콜백을 넣어 두면, 그림이 생길 때마다 base64 PNG 를 넘겨준다.
"""
from __future__ import annotations

import base64
import io

from matplotlib._pylab_helpers import Gcf
from matplotlib.backend_bases import FigureManagerBase as FigureManager  # noqa: F401
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas  # noqa: F401

from . import tracer as _tracer


def flush():
    """열려 있는 그림을 모두 PNG 로 넘기고 닫는다 (주피터처럼 셀이 끝날 때도 호출됨)."""
    managers = Gcf.get_all_fig_managers()
    for manager in managers:
        fig = manager.canvas.figure
        if not fig.axes:
            continue
        buf = io.BytesIO()
        try:
            fig.savefig(buf, format="png", dpi=80, bbox_inches="tight")
        except Exception:
            continue
        hook = _tracer.IMAGE_HOOK
        if hook is not None:
            hook(base64.b64encode(buf.getvalue()).decode("ascii"))
    if managers:
        Gcf.destroy_all()


def show(*args, **kwargs):
    flush()
