"""orig.nii.gz defacing — 얼굴 복셀 마스킹(재식별 방지).

익명화 후 orig.nii.gz에 남는 유일한 잠재 재식별 요소는 영상 자체의 얼굴
형상이다(헤더 텍스트 PHI는 nifti_anon이 이미 제거). 이 모듈은 그 얼굴을
마스킹하는 자리다.

현재는 **코드/UI 틀만** 있고 구현은 나중이다(스텁). deface=True로 들어오면
NotImplementedError를 던져 "익명화한 척"하지 않는다 — 조용한 no-op는 잘못된
안전감을 주므로 금지. UI 토글은 비활성(구현 예정) 상태로 둔다.
"""

from __future__ import annotations


class DefaceError(RuntimeError):
    """defacing 실패."""


def deface_nifti(path) -> None:
    """orig.nii.gz의 얼굴을 마스킹한다(제자리). 아직 미구현.

    Raises:
        NotImplementedError: 항상 — 구현 전까지 활성화되면 명시적으로 실패한다.
    """
    raise NotImplementedError("orig defacing 미구현 — UI 토글은 예정 상태다")
