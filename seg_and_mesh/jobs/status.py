"""status.json 상태 모델 + PHI-안전 에러 기록 (스펙 §9.1, §12).

UI는 이 파일을 폴링해 진행을 본다. error에는 원본 파일명·경로·DICOM 태그를
절대 넣지 않는다 — 환자 식별자가 새면 PHI 유출이다.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from seg_and_mesh.jobs.layout import JobPaths

STATES = ("awaiting_series", "running", "done", "error")

#: 마스킹 정규식 네 개. 순서가 중요하다 — 공백을 허용하는 윈도우/UNC 경로부터
#: 잡아야, 뒤에 오는 범용 패턴이 그 절반만 먹어치우는 걸 막는다.
#:
#: 윈도우 드라이브 경로. 폴더명에 공백이 있어도("Program Files") 잡되,
#: 확장자에서 멈춘다 — 뒤따르는 진단 문구("failed", "line 3")는 남긴다.
#: 예약 문자(<>:"|?*)는 윈도우 파일명에 못 오므로 경계로 쓴다.
_WIN_PATH_RE = re.compile(
    r'[A-Za-z]:\\(?:[^\\<>:"|?*\r\n]+\\)*[^\\<>:"|?*\r\n]*\.[A-Za-z0-9]{1,10}\b'
)
#: UNC 경로(\\server\share\...). 윈도우 경로와 같은 이유로 확장자에서 멈춘다.
_UNC_PATH_RE = re.compile(
    r'\\\\[^\\<>:"|?*\r\n]+(?:\\[^\\<>:"|?*\r\n]+)*\.[A-Za-z0-9]{1,10}\b'
)
#: 공백 없이 '/'나 '\'를 포함한 토큰은 통째로 마스킹한다(선두 세그먼트까지
#: 포함) — POSIX 절대경로뿐 아니라 "Hong_Gil_Dong/orig.mgz" 같은 상대경로도
#: 첫 세그먼트가 환자 식별자일 수 있어 앞부분만 남기면 안 된다.
_TOKEN_PATH_RE = re.compile(r"\S*[/\\]\S+")
#: 확장자가 있는 맨 파일명(경로 구분자 없음). 화이트리스트를 안 둔다 —
#: dcm2niix는 SeriesDescription으로 사이드카 파일명을 짓는다
#: (io/dcm2niix.py 참고, 예: "5_T1.nii_repeat.json"). 버전 문자열
#: ("v1.0.20211006")까지 파일로 오인하지 않도록, "글자가 토큰 어딘가에
#: 있다"가 아니라 "확장자 자체에 글자가 있다"를 조건(전방탐색)으로 건다 —
#: 순수 숫자 확장자(".20211006")는 그래서 걸러진다.
_FILE_RE = re.compile(r"\b[\w.\-]+\.(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{1,10}\b")
#: 위 네 패턴은 공백에서 끊긴다 — "Hong Gil Dong" 같은 확장자 없는 이름은
#: 토큰별로 따로 마스킹돼 가운데 토막(Gil)이 두 <path> 사이에 그대로
#: 남는다. 마스킹 두 개 사이에 낀 토큰 하나(또는 0개)는 원래 한 경로/이름이
#: 공백에 잘린 조각이라고 보고 통째로 접는다. 고정점까지 반복 적용해야
#: 3어절 이상의 이름도 끝까지 접힌다(sanitize_stderr 참고).
_ADJACENT_MASK_RE = re.compile(r"(?:<path>|<file>)(?:\s+\S+)?\s+(?:<path>|<file>)")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class JobStatus:
    job_id: str
    case_name: str
    created_at: str
    updated_at: str
    state: str
    step: str
    input: dict
    series: list = field(default_factory=list)
    selected_series: dict | None = None
    engine: dict | None = None
    variants: list = field(default_factory=list)
    error: dict | None = None

    def to_json_dict(self) -> dict:
        """camelCase (스펙 §9.1 계약)."""
        return {
            "jobId": self.job_id,
            "caseName": self.case_name,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "state": self.state,
            "step": self.step,
            "input": self.input,
            "series": self.series,
            "selectedSeries": self.selected_series,
            "engine": self.engine,
            "variants": self.variants,
            "error": self.error,
        }

    @classmethod
    def from_json_dict(cls, d: dict) -> "JobStatus":
        return cls(
            job_id=d["jobId"], case_name=d.get("caseName", ""),
            created_at=d["createdAt"], updated_at=d["updatedAt"],
            state=d["state"], step=d.get("step", ""),
            input=d.get("input", {}), series=d.get("series", []),
            selected_series=d.get("selectedSeries"), engine=d.get("engine"),
            variants=d.get("variants", []), error=d.get("error"),
        )


def _collapse_adjacent_masks(text: str) -> str:
    """<path> 조각 사이에 낀 토큰을 접어 하나로 합친다.

    고정점까지 반복한다 — 한 번의 sub는 왼쪽에서 오른쪽으로 겹치지 않게만
    훑어서, "<path> Gil <path> Dong <path>"처럼 마스킹이 세 개 이상 이어지면
    첫 결합만 먹고 끝난다. 그대로면 멈춘다.
    """
    while True:
        collapsed = _ADJACENT_MASK_RE.sub("<path>", text)
        if collapsed == text:
            return collapsed
        text = collapsed


def sanitize_stderr(text: str) -> str:
    """경로·파일명 토큰을 모양으로 마스킹한다. 나머지 진단 문구는 남긴다.

    잡는 것: POSIX/윈도우/UNC 절대경로(폴더명 공백 허용), 구분자가 든
    상대경로, 확장자가 있는 맨 파일명(화이트리스트 없음) — 전부 "모양"으로
    판단하지 확장자 목록으로 판단하지 않는다. 윈도우/UNC 경로는 확장자
    앞에서만 멈추므로 공백 포함 경로 하나가 여러 토큰으로 쪼개질 수 있는데,
    그중 확장자 없는 조각(예: 파일명이 IM0001처럼 확장자가 없는 필립스
    DICOM)은 공백에서 끊긴 맨 토큰으로 남는다 — 그래서 마지막에
    _collapse_adjacent_masks로 마스킹 사이에 낀 토막을 접어 붙인다.

    과다 마스킹과 과소 마스킹이 충돌하면 항상 과다 마스킹을 택한다 —
    진단 문구 한 토막을 잃는 비용은, 환자 식별자 하나가 새는 비용보다
    항상 작다. self.conv1.weight 같은 점으로 이어진 속성 체인이 파일명과
    모양이 같아서 <file>로 뭉개지는 것도 이 원칙에 따른 의도된 동작이다 —
    모양만으로는 진짜 파일명과 구별할 수 없어서 따로 봐주지 않는다.

    못 잡는 것(의도적 한계): 구분자도 확장자도 없는 맨 식별자
    (예: "FastSurferCNN failed for subject Hong Gil Dong")는 평범한 문장과
    모양으로 구별이 안 돼 정규식으로 못 잡는다 — 잡으려 하면 진단 문구를
    전부 마스킹해 이 필드의 존재 이유(진단)를 없앤다. 대신 상류에서 막는다:
    io.prepare_input이 업로드를 고정 이름으로 정규화하고,
    segment.run_fastsurfer가 sid="case"로 고정해서 넘기므로, 환자 식별자가
    섞인 맨 토큰이 FastSurfer stderr까지 올 일이 애초에 드물다.
    """
    text = _WIN_PATH_RE.sub("<path>", text)
    text = _UNC_PATH_RE.sub("<path>", text)
    text = _TOKEN_PATH_RE.sub("<path>", text)
    text = _FILE_RE.sub("<file>", text)
    text = _collapse_adjacent_masks(text)
    return text.strip()


def write_status(paths: JobPaths, status: JobStatus) -> None:
    """updated_at 갱신 후 원자적으로 쓴다(임시파일 → rename).

    Raises:
        ValueError: status.state가 STATES에 없을 때 — 오타가 조용히
            디스크까지 왕복하는 걸 막는다.
    """
    if status.state not in STATES:
        raise ValueError(f"알 수 없는 state: {status.state!r} (STATES={STATES})")
    status.updated_at = now_iso()
    tmp = paths.status_file.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(status.to_json_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, paths.status_file)


def read_status(paths: JobPaths) -> JobStatus:
    return JobStatus.from_json_dict(
        json.loads(paths.status_file.read_text(encoding="utf-8"))
    )


def record_error(paths: JobPaths, step: str, returncode: int | None, stderr_tail: str) -> None:
    """PHI-안전 에러 기록. state=error.

    status.step도 실패한 단계로 옮긴다 — 스펙 §9.1에서 step은 잡의 "현재
    위치"라, 실패했으면 그 위치가 곧 실패 지점이어야 한다(마지막으로 시작한
    단계가 아니라).
    """
    status = read_status(paths)
    status.state = "error"
    status.step = step
    status.error = {
        "step": step,
        "returncode": returncode,
        "message": sanitize_stderr(stderr_tail),
    }
    write_status(paths, status)
