"""트레이서: 실행 과정 기록이 정확한지."""
from conftest import final_globals, var


def test_records_assignments_and_loop_counts(trace_code):
    t = trace_code("""
        total = 0
        for i in range(3):
            total += i
        """)
    assert t["error"] is None
    assert final_globals(t)["total"]["r"] == "3"
    executed = [s["line"] for s in t["steps"] if s["event"] == "line"]
    assert executed.count(3) == 3


def test_function_call_arguments_stack_and_return(trace_code):
    t = trace_code("""
        def square(n):
            result = n * n
            return result
        x = square(4)
        """)
    call_idx = next(i for i, s in enumerate(t["steps"]) if s["event"] == "call" and s["func"] == "square")
    assert var(t, call_idx, "n")["r"] == "4"
    assert len(t["steps"][call_idx]["stack"]) == 2  # 전역 + square
    ret = next(s for s in t["steps"] if s["event"] == "return" and s["func"] == "square")
    assert ret["ret"]["r"] == "16"
    assert final_globals(t)["x"]["r"] == "16"


def test_aliases_share_object_id(trace_code):
    t = trace_code("""
        a = [1]
        b = a
        b.append(2)
        """)
    g = final_globals(t)
    assert g["a"]["id"] == g["b"]["id"]
    assert [x["r"] for x in g["a"]["items"]] == ["1", "2"]


def test_objects_and_dicts_are_structured(trace_code):
    t = trace_code("""
        class Account:
            def __init__(self, owner):
                self.owner = owner
                self.balance = 0
        acc = Account("민수")
        acc.balance += 500
        counts = {"a": 1}
        """)
    g = final_globals(t)
    assert g["acc"]["k"] == "obj" and g["acc"]["t"] == "Account"
    assert dict((k, v["r"]) for k, v in g["acc"]["attrs"]) == {"owner": "'민수'", "balance": "500"}
    assert g["counts"]["k"] == "map"
    assert g["Account"]["k"] == "type"


def test_print_output_and_step_offsets(trace_code):
    t = trace_code("""
        print("hi")
        x = 1
        """)
    assert t["output"] == "hi\n"
    assert t["steps"][-1]["out"] == len("hi\n")


def test_step_limit_stops_infinite_loop(trace_code):
    t = trace_code("""
        i = 0
        while True:
            i += 1
        """, max_steps=50)
    assert t["truncated"] is True
    assert len(t["steps"]) == 50
    assert t["error"] is None


def test_input_values_are_fed_in_order(trace_code):
    t = trace_code("""
        name = input("이름? ")
        age = int(input())
        """, stdin_lines=["지수", "20"])
    g = final_globals(t)
    assert g["name"]["r"] == "'지수'"
    assert g["age"]["r"] == "20"
    assert "이름? 지수" in t["output"]


def test_syntax_error_is_reported_without_running(trace_code):
    t = trace_code("for i in range(3)\n    print(i)\n")
    assert t["steps"] == []
    assert t["error"]["type"] == "SyntaxError"
    assert t["error"]["line"] == 1
    assert "콜론" in t["error"]["hint"]["title"]


def test_runtime_error_has_line_hint_and_real_source_in_traceback(trace_code):
    t = trace_code("""
        def first(xs):
            return xs[0]
        first([])
        """)
    e = t["error"]
    assert e["type"] == "IndexError"
    assert e["line"] == 2
    assert "범위" in e["hint"]["title"]
    assert "return xs[0]" in e["tb"]
    assert any(s["event"] == "exception" for s in t["steps"])


def test_repeated_values_are_stored_once(trace_code):
    t = trace_code("""
        big = list(range(30))
        for i in range(20):
            pass
        """)
    # big 은 단계마다 반복되지만 values 에는 한 번만 저장된다
    assert len(t["values"]) < 40
