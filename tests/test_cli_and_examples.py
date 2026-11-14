"""CLI, HTML 생성, 예제 파일."""
import json

import pytest
from conftest import EXAMPLES

from pyrewind.__main__ import main
from pyrewind.render import build_html


def test_cli_writes_html_viewer(tmp_path):
    prog = tmp_path / "hello.py"
    prog.write_text("x = 1\nprint(x)\n", encoding="utf-8")
    out = tmp_path / "out.html"
    assert main([str(prog), "--no-open", "-q", "-o", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "/*__TRACE_DATA__*/null" not in html
    assert '"file":"hello.py"' in html


def test_cli_missing_file_returns_error_code(tmp_path):
    assert main([str(tmp_path / "nope.py"), "--no-open"]) == 2


def test_html_cannot_be_broken_by_script_tags_in_code():
    template_count = build_html({"x": 1}).count("</script>")
    html = build_html({"source": "s = '</script><b>'"})
    assert html.count("</script>") == template_count


EXPECTED = {
    "basics.py": None,
    "bug_hunt.py": "IndexError",
    "notebook_demo.ipynb": None,
    "pandas_tour.ipynb": None,
    "ml_tour.ipynb": None,
}
NEEDS = {"pandas_tour.ipynb": ["pandas"], "ml_tour.ipynb": ["pandas", "sklearn", "matplotlib"]}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_examples_run(tmp_path, name):
    for mod in NEEDS.get(name, []):
        pytest.importorskip(mod)
    out = tmp_path / "t.json"
    assert main([str(EXAMPLES / name), "--json", "--no-open", "-q", "-o", str(out)]) == 0
    t = json.loads(out.read_text(encoding="utf-8"))
    assert t["steps"]
    assert (t["error"] or {}).get("type") == EXPECTED[name]
    assert not t["truncated"]
