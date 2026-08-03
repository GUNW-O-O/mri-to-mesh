"""완전 로컬 · 외부 통신 0 가드 — static 에셋에 외부 참조가 없어야 한다."""
import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parents[2] / "seg_and_mesh" / "web" / "static"

# vendor/ 는 로컬 번들이라 검사 대상이 아니지만, 그 안에도 외부 URL이 박혀선
# 안 되므로 함께 훑는다. 예외: three.js 소스 주석의 라이선스 URL은 실행 시
# 요청이 아니다 — 그래서 <script src>/import/fetch/url() 문맥만 잡는다.
_EXTERNAL = re.compile(
    r"""(?:src|href)\s*=\s*['"]https?://"""
    r"""|from\s+['"]https?://"""
    r"""|import\s*\(\s*['"]https?://"""
    r"""|fetch\(\s*['"]https?://"""
    r"""|url\(\s*['"]?https?://"""
    r"""|//(?:cdn|unpkg|esm\.sh|cdn\.jsdelivr|fonts\.googleapis)""",
)


def test_no_external_references_in_own_assets():
    offenders = []
    for p in _STATIC.rglob("*"):
        if not p.is_file() or p.suffix not in (".html", ".js", ".css"):
            continue
        # vendored three.js 자체는 우리가 작성한 코드가 아니므로 제외한다.
        if "vendor" in p.relative_to(_STATIC).parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in _EXTERNAL.finditer(text):
            offenders.append(f"{p.name}: …{text[max(0,m.start()-20):m.start()+40]}…")
    assert not offenders, "외부 참조 발견:\n" + "\n".join(offenders)
