"""파이썬 오류를 초보자용 한국어 설명으로 바꾼다.

explain_error("IndexError", "list index out of range")
  -> {"title": "...", "why": "...", "fix": "..."}
"""
from __future__ import annotations

import builtins
import difflib
import inspect
import pkgutil
import re
import sys

# import 이름과 pip 설치 이름이 다른 대표적인 경우
PIP_NAMES = {
    "sklearn": "scikit-learn", "cv2": "opencv-python", "PIL": "pillow", "bs4": "beautifulsoup4",
    "yaml": "pyyaml", "dotenv": "python-dotenv", "skimage": "scikit-image", "Crypto": "pycryptodome",
}

# (오류 이름, 메시지 정규식 또는 None, 제목, 왜, 어떻게 고치나)  - 위에서부터 먼저 맞는 것 사용
_RULES = [
    ("ZeroDivisionError", None,
     "0으로 나누려고 했어요",
     "나누는 값(분모)이 0이었습니다. 수학처럼 파이썬도 0으로는 나눌 수 없어요.",
     "나누기 직전에 분모가 왜 0이 되었는지 확인하세요. 비어 있는 목록의 개수(len)가 0이 아닌지도 보세요. 필요하면 `if 분모 == 0:` 으로 따로 처리하세요."),
    ("IndexError", r"out of range",
     "목록(리스트)의 범위를 벗어난 위치를 읽으려 했어요",
     "리스트의 위치(인덱스)는 0부터 `길이-1`까지만 있습니다. 그보다 큰 위치를 읽었거나, **빈 리스트에서 [0]** 을 읽으면 이 오류가 나요.",
     "오류 줄의 리스트 길이와 인덱스 값을 비교해 보세요. 빈 리스트일 수 있다면 `if len(리스트) == 0:` 으로 먼저 처리하세요."),
    ("KeyError", None,
     "딕셔너리(또는 DataFrame)에 없는 키를 찾았어요",
     "찾으려는 키(DataFrame이면 열 이름)가 존재하지 않습니다. 철자, 띄어쓰기, 대소문자가 조금만 달라도 다른 키예요.",
     "실제로 있는 키 목록과 비교해 보세요. 없을 수도 있는 키는 `딕셔너리.get(키, 기본값)` 으로 꺼내면 안전합니다."),
    ("UnboundLocalError", None,
     "함수 안에서 값을 정하기 전에 변수를 읽었어요",
     "함수 안에서 어떤 변수에 값을 대입하면 그 변수는 그 함수만의 '지역 변수'가 됩니다. 그런데 대입하기 전에 먼저 읽으려 했어요.",
     "바깥 변수를 바꾸려는 거라면 함수의 인자로 넘기고 결과를 return 하세요. (꼭 필요하면 `global 변수이름`)"),
    ("NameError", None,
     "만든 적 없는 이름을 썼어요",
     "그 이름의 변수나 함수가 아직 없습니다. 철자가 틀렸거나, 변수를 만들기 전에 사용했거나, import를 빠뜨렸을 수 있어요.",
     "철자를 확인하고, 그 변수를 만드는 줄이 이 줄보다 먼저 실행됐는지 확인하세요."),
    ("TypeError", r"can only concatenate str|must be str, not|unsupported operand type.*'str'",
     "문자열과 숫자를 섞어서 계산했어요",
     "`'점수: ' + 90` 처럼 문자열(str)과 숫자(int)는 + 로 바로 붙일 수 없습니다.",
     "숫자를 `str(숫자)` 로 바꾸거나 `f'점수: {점수}'` 처럼 f-string을 쓰세요. 반대로 계산을 하려면 `int(문자열)` 로 바꾸세요."),
    ("TypeError", r"'NoneType' object is not subscriptable|'NoneType' object is not iterable",
     "None(값 없음)에서 값을 꺼내려 했어요",
     "변수에 None이 들어 있었습니다. 가장 흔한 원인은 **함수가 return을 하지 않아서** 결과가 None이 된 경우예요. `리스트.sort()` 처럼 None을 돌려주는 메서드의 결과를 저장했을 때도 그래요.",
     "시각화 화면의 '오류 줄에 쓰인 값' 표에서 None 인 변수 옆 링크를 눌러 None 이 된 곳으로 가 보세요. 그 함수에 return 이 빠지지 않았는지 확인하세요."),
    ("TypeError", r"missing \d+ required positional argument|takes \d+ positional argument",
     "함수에 넘긴 인자 개수가 맞지 않아요",
     "함수를 정의할 때 받기로 한 인자 개수와, 호출할 때 넘긴 개수가 다릅니다.",
     "함수의 `def` 줄과 호출하는 줄의 괄호 안 값 개수를 비교해 보세요. 클래스 메서드라면 첫 인자 `self` 를 빠뜨리지 않았는지도 보세요."),
    ("TypeError", r"unexpected keyword argument",
     "함수가 받지 않는 이름의 인자를 넘겼어요",
     "함수는 미리 정해진 이름의 인자만 받을 수 있어요. 인자 이름에 오타가 있거나, 그 함수에는 없는 옵션입니다.",
     "아래 '받을 수 있는 인자' 목록에서 맞는 이름을 골라 고치세요."),
    ("TypeError", r"object is not callable",
     "함수가 아닌 것을 함수처럼 ( ) 로 호출했어요",
     "변수 이름이 함수 이름과 겹쳐서 덮어썼을 수 있어요. 예를 들어 `sum = 10` 을 하고 나면 `sum(...)` 을 쓸 수 없습니다.",
     "오류 줄에서 ( ) 앞에 있는 이름에 무엇이 들어 있는지 확인하고, 겹치는 변수 이름을 바꾸세요."),
    ("TypeError", r"not supported between instances|unorderable",
     "서로 비교할 수 없는 값끼리 크기를 비교했어요",
     "예를 들어 문자열과 숫자는 `<`, `>` 로 비교할 수 없어요. 숫자처럼 보이는 문자열 '10' 일 수도 있습니다.",
     "비교하는 두 값의 자료형(type)을 확인하고 `int()` / `float()` 로 맞추세요."),
    ("TypeError", None,
     "자료형이 맞지 않는 연산을 했어요",
     "그 연산이나 함수가 받을 수 없는 종류(자료형)의 값이 들어갔습니다.",
     "아래 '오류 줄에 쓰인 값'에서 각 값의 자료형을 확인하세요."),
    ("AttributeError", r"'NoneType' object has no attribute",
     "None(값 없음)에서 기능(메서드)을 쓰려 했어요",
     "변수에 None이 들어 있었습니다. 함수가 return을 하지 않았거나, `.sort()` / `.append()` 처럼 None을 돌려주는 메서드의 결과를 저장했을 수 있어요.",
     "그 변수가 None이 된 곳을 찾아가 보세요. 예: `a = a.sort()` 는 `a.sort()` 만 쓰거나 `a = sorted(a)` 로 바꾸세요."),
    ("AttributeError", None,
     "그 자료형에는 없는 기능(속성/메서드)을 썼어요",
     "예를 들어 리스트에는 `.push()` 가 없고 `.append()` 가 있습니다. 철자가 틀렸거나 생각한 것과 다른 자료형일 수 있어요.",
     "아래 값의 자료형을 확인하고 그 자료형에 있는 메서드 이름을 쓰세요."),
    ("ValueError", r"invalid literal for int|could not convert string to float",
     "숫자로 바꿀 수 없는 글자를 숫자로 바꾸려 했어요",
     "`int('abc')` 나 `int('3.5')` 처럼 숫자 모양이 아닌 문자열은 정수로 바꿀 수 없어요. 앞뒤 공백이나 쉼표가 섞였을 수도 있습니다.",
     "바꾸려는 문자열 값을 확인하세요. 소수라면 `float()` 를 쓰고, 공백은 `.strip()` 으로 지우세요."),
    ("ValueError", r"too many values to unpack|not enough values to unpack",
     "나눠 담으려는 변수 개수와 값 개수가 달라요",
     "`a, b = [1, 2, 3]` 처럼 왼쪽 변수 개수와 오른쪽 값 개수가 같아야 합니다.",
     "오른쪽 값이 몇 개인지 확인하고 변수 개수를 맞추세요."),
    ("ValueError", None,
     "값의 종류는 맞지만 내용이 올바르지 않아요",
     "함수가 받을 수 있는 범위를 벗어난 값이 들어갔습니다.",
     "오류 메시지와 아래 값을 비교해 보세요."),
    ("RecursionError", None,
     "함수가 자기 자신을 끝없이 호출했어요",
     "재귀 함수에 멈추는 조건(base case)이 없거나, 그 조건에 도달하지 못했습니다.",
     "`if n <= 1: return ...` 같은 멈춤 조건이 있는지, 호출할 때마다 값이 그 조건 쪽으로 줄어드는지 확인하세요."),
    ("ModuleNotFoundError", None,
     "설치되지 않은 모듈을 import 했어요",
     "그 라이브러리가 지금 사용하는 파이썬(가상환경)에 설치되어 있지 않습니다.",
     "터미널에서 `pip install 모듈이름` 으로 설치하세요. VS Code라면 오른쪽 아래에서 고른 인터프리터에 설치해야 합니다."),
    ("FileNotFoundError", None,
     "파일을 찾을 수 없어요",
     "파일 이름이나 경로가 틀렸거나, 실행 위치(폴더)가 생각과 다릅니다.",
     "파일 이름과 확장자를 확인하세요. 상대 경로는 실행하는 파이썬 파일이 있는 폴더 기준입니다."),
    ("IndentationError", None,
     "들여쓰기가 맞지 않아요",
     "파이썬은 들여쓰기(앞쪽 공백)로 코드 블록을 구분합니다. `if`, `for`, `def` 다음 줄은 들여써야 하고, 같은 블록은 칸 수가 같아야 해요.",
     "빨간 줄과 그 위 줄의 들여쓰기 칸 수를 맞추세요. 탭과 스페이스를 섞지 마세요."),
    ("SyntaxError", r"was never closed|unmatched|unexpected EOF",
     "괄호나 따옴표의 짝이 맞지 않아요",
     "여는 괄호 ( [ { 나 따옴표가 닫히지 않았거나, 닫는 것이 하나 더 있습니다.",
     "빨간 줄(또는 그 바로 위 줄)의 괄호 개수를 세어 보세요."),
    ("SyntaxError", r"expected ':'",
     "콜론(:)이 빠졌어요",
     "`if`, `for`, `while`, `def`, `class` 줄 끝에는 `:` 이 있어야 합니다.",
     "빨간 줄 끝에 `:` 을 붙이세요."),
    ("SyntaxError", None,
     "파이썬 문법에 맞지 않는 줄이 있어요",
     "오타, 빠진 기호, 잘못된 위치의 키워드 때문에 파이썬이 코드를 읽지 못했습니다. 이 경우 코드는 한 줄도 실행되지 않아요.",
     "빨간 줄과 그 바로 위 줄을 확인하세요. 콜론(:), 괄호, 따옴표, `=` 와 `==` 를 헷갈리지 않았는지 보세요."),
]


def _close(word: str, candidates, n: int = 3) -> list[str]:
    """철자가 비슷한 이름 찾기 (대소문자 무시 비교도 시도)."""
    cands = sorted({c for c in candidates if isinstance(c, str) and not c.startswith("__")})
    found = difflib.get_close_matches(word, cands, n=n, cutoff=0.6)
    if not found:
        lower = {c.lower(): c for c in cands}
        found = [lower[m] for m in difflib.get_close_matches(word.lower(), list(lower), n=n, cutoff=0.6)]
    return [f for f in found if f != word]


def _resolve(dotted: str, frame):
    """'LogisticRegression.__init__' 같은 이름을 사용자 코드의 변수에서 찾아 실제 객체로."""
    parts = dotted.split(".")
    scopes = [frame.f_locals, frame.f_globals, vars(builtins)] if frame is not None else [vars(builtins)]
    obj = next((s[parts[0]] for s in scopes if parts[0] in s), None)
    for p in parts[1:]:
        if obj is None:
            return None
        obj = getattr(obj, p, None)
    return obj


def suggest_fix(e: BaseException, frame) -> dict | None:
    """잘못 쓴 이름을 찾아 비슷한 이름을 추천한다.

    반환: {"kind": attr|name|module|import|kwarg, "wrong": 틀린 이름, "options": [추천...], ...}
    """
    msg = str(e)
    try:
        if isinstance(e, ModuleNotFoundError):
            top = (getattr(e, "name", None) or "").split(".")[0]
            if not top:
                return None
            cands = [m.name for m in pkgutil.iter_modules()] + list(sys.builtin_module_names)
            return {"kind": "module", "wrong": top, "options": _close(top, cands),
                    "pip": PIP_NAMES.get(top, top)}

        if isinstance(e, ImportError):
            m = re.search(r"cannot import name '(\w+)' from '([\w.]+)'", msg)
            if m and m.group(2) in sys.modules:
                return {"kind": "import", "wrong": m.group(1), "owner": m.group(2),
                        "options": _close(m.group(1), dir(sys.modules[m.group(2)]))}
            return None

        if isinstance(e, AttributeError):
            name = getattr(e, "name", None)
            if name is None:
                m = re.search(r"has no attribute '(\w+)'", msg)
                name = m.group(1) if m else None
            obj = getattr(e, "obj", None)
            if obj is None:
                # numpy 처럼 모듈이 직접 AttributeError 를 만드는 경우 obj 가 비어 있음 → 메시지에서 모듈 찾기
                mm = re.search(r"module '([\w.]+)' has no attribute", msg)
                obj = sys.modules.get(mm.group(1)) if mm else None
            if not name or obj is None:
                return None  # None 에서 난 오류(NoneType)는 이름 문제가 아님
            owner = getattr(obj, "__name__", None) if inspect.ismodule(obj) else type(obj).__name__
            return {"kind": "attr", "wrong": name, "owner": owner, "options": _close(name, dir(obj))}

        if isinstance(e, NameError):  # UnboundLocalError 포함
            name = getattr(e, "name", None)
            if name is None:
                m = re.search(r"name '(\w+)' is not defined", msg)
                name = m.group(1) if m else None
            if not name or isinstance(e, UnboundLocalError):
                return None
            cands = list(dir(builtins))
            if frame is not None:
                cands += list(frame.f_locals) + list(frame.f_globals)
            return {"kind": "name", "wrong": name, "options": _close(name, cands)}

        if isinstance(e, TypeError):
            m = re.search(r"(?:([\w.]+)\(\) )?got an unexpected keyword argument '(\w+)'", msg)
            if m:
                fname, kw = m.group(1), m.group(2)
                if not fname and frame is not None:
                    # sklearn 처럼 함수를 감싼 경우 메시지에 함수 이름이 없음 → 오류 줄 코드에서 'kw=' 가 들어간 호출 찾기
                    import linecache
                    src = linecache.getline(frame.f_code.co_filename, frame.f_lineno)
                    cm = re.search(r"([A-Za-z_][\w.]*)\s*\((?:[^()]|\([^()]*\))*?\b" + re.escape(kw) + r"\s*=", src)
                    fname = cm.group(1) if cm else None
                func = _resolve(fname, frame) if fname else None
                if func is None:
                    return None
                params = [p for p, v in inspect.signature(func).parameters.items()
                          if p != "self" and v.kind not in (v.VAR_POSITIONAL, v.VAR_KEYWORD)]
                return {"kind": "kwarg", "wrong": kw, "owner": fname.replace(".__init__", ""),
                        "options": _close(kw, params), "params": params[:15]}
    except Exception:
        return None
    return None


def explain_error(etype: str, msg: str) -> dict:
    for name, pattern, title, why, fix in _RULES:
        if name != etype:
            continue
        if pattern and not re.search(pattern, msg or ""):
            continue
        return {"title": title, "why": why, "fix": fix}
    return {
        "title": f"{etype} 오류가 났어요",
        "why": "프로그램이 이 줄을 실행하다가 멈췄습니다.",
        "fix": "아래 '오류 줄에 쓰인 값'을 확인하고, 단계를 거슬러 올라가며 값이 언제 예상과 달라졌는지 찾아보세요.",
    }
