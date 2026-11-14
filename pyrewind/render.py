"""trace 결과를 독립 실행형 HTML 뷰어로 만든다."""
from __future__ import annotations

import json
from pathlib import Path

_TEMPLATE = Path(__file__).with_name("viewer.html")
_PLACEHOLDER = "/*__TRACE_DATA__*/null"


def build_html(trace: dict) -> str:
    data = json.dumps(trace, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")  # </script> 조기 종료 방지
    html = _TEMPLATE.read_text(encoding="utf-8")
    return html.replace(_PLACEHOLDER, data, 1)
