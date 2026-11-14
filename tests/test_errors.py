"""오류 도우미: 한국어 설명과 이름 오타 추천."""
import pytest

from visual_code_runner.errors import explain_error


@pytest.mark.parametrize("etype, msg, keyword", [
    ("ZeroDivisionError", "division by zero", "0으로"),
    ("KeyError", "'x'", "키"),
    ("TypeError", 'can only concatenate str (not "int") to str', "문자열과 숫자"),
    ("TypeError", "'NoneType' object is not subscriptable", "None"),
    ("TypeError", "f() got an unexpected keyword argument 'z'", "인자"),
    ("ValueError", "invalid literal for int() with base 10: 'a'", "숫자로"),
    ("IndentationError", "unexpected indent", "들여쓰기"),
])
def test_explanations_match_error_kind(etype, msg, keyword):
    hint = explain_error(etype, msg)
    assert keyword in hint["title"]
    assert hint["why"] and hint["fix"]


def test_unknown_error_gets_generic_explanation():
    hint = explain_error("WeirdError", "something")
    assert "WeirdError" in hint["title"]


@pytest.mark.parametrize("code, kind, wrong, best", [
    ("nums = [1]\nnums.apend(2)\n", "attr", "apend", "append"),
    ("import math\nmath.flor(1.5)\n", "attr", "flor", "floor"),
    ("prnt('x')\n", "name", "prnt", "print"),
    ("total_score = 1\nprint(total_scor)\n", "name", "total_scor", "total_score"),
    ("def add(a, bonus=0):\n    return a\nadd(1, bonnus=2)\n", "kwarg", "bonnus", "bonus"),
    ("import jsonn\n", "module", "jsonn", "json"),
    ("from collections import Countr\n", "import", "Countr", "Counter"),
])
def test_name_typos_get_suggestions(trace_code, code, kind, wrong, best):
    sg = trace_code(code)["error"]["suggest"]
    assert sg["kind"] == kind
    assert sg["wrong"] == wrong
    assert sg["options"][0] == best


def test_kwarg_error_lists_accepted_parameters(trace_code):
    sg = trace_code("def add(a, b=0):\n    return a + b\nadd(1, c=2)\n")["error"]["suggest"]
    assert sg["kind"] == "kwarg"
    assert sg["params"] == ["a", "b"]


def test_none_attribute_error_is_not_treated_as_typo(trace_code):
    err = trace_code("x = None\nx.append(1)\n")["error"]
    assert err["suggest"] is None
    assert "None" in err["hint"]["title"]


def test_missing_package_suggests_pip_install(trace_code):
    sg = trace_code("import notarealpackage_xyz\n")["error"]["suggest"]
    assert sg["kind"] == "module"
    assert sg["options"] == []
    assert sg["pip"] == "notarealpackage_xyz"
