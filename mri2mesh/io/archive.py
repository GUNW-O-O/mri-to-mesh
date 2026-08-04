"""ZIP 안전 해제 (스펙 §6.2).

강제 사항:
1. 경로 순회 차단 — '..', 절대 경로, 드라이브 지정, 심볼릭 링크 엔트리를 거부한다.
2. 파일명 인코딩 — UTF-8 플래그가 없으면 CP949로 재디코딩한다.
3. 용량 상한 — 전개 총 용량과 엔트리 수 상한을 넘으면 중단한다.

검사에 걸리면 잡 전체를 거부한다. 실패 시 이 함수가 새로 만든 dest_root는
지우려고 시도한다(베스트 에포트) — 삭제 자체가 파일 잠금 등으로 실패할 수
있어 "부분 전개물이 절대 남지 않는다"는 보장까지는 아니다.
"""

from __future__ import annotations

import ntpath
import shutil
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path

#: 스트리밍 읽기 단위
_CHUNK_BYTES = 1 << 20

#: ZIP 범용 비트 플래그 11번 — 파일명이 UTF-8임을 뜻한다.
_UTF8_FLAG = 0x800

#: Windows 예약 장치 이름. 확장자를 붙여도(NUL.dcm 등) 여전히 장치를 가리킨다.
_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class UnsafeArchiveError(Exception):
    """ZIP이 안전 검사에 걸렸다. 잡 전체를 거부한다."""


@dataclass(frozen=True)
class ExtractLimits:
    """전개 상한. 기본값은 스펙 §11의 MAX_EXTRACT_GB=20, MAX_UPLOAD_MB=4096
    값을 그대로 흉내 낸 상수다 — 환경변수나 설정 파일을 읽지 않는다. `.env`의
    MAX_EXTRACT_GB/MAX_UPLOAD_MB를 바꿔도 이 기본값은 바뀌지 않는다. 실제
    설정과 연결하는 일은 이 모듈의 책임이 아니다 — jobs/ 서브시스템이 설정을
    읽어 이 데이터클래스를 명시적으로 생성해 넘겨야 한다.

    zipfile.ZipFile()은 열자마자 central directory 전체를 ZipInfo 객체로
    파싱하므로, max_entries 검사는 이미 그 비용을 다 치른 뒤에야 실행된다.
    ZIP 파일 자체 크기를 먼저 검사해야 수천만 엔트리를 선언한 아카이브가
    central directory 파싱만으로 메모리를 고갈시키는 것을 막을 수 있다.
    """

    max_total_bytes: int = 20 * 1024**3
    max_entries: int = 200_000
    max_zip_bytes: int = 4 * 1024**3


@dataclass(frozen=True)
class ExtractResult:
    """전개 결과. files는 실제로 기록된 일반 파일 경로만 담는다.

    같은 이름이 대소문자만 다르게 반복되는 아카이브(예: a.txt와 A.txt)는
    대소문자를 구분하지 않는 파일시스템에서 하나의 경로로 겹쳐 쓰인다.
    files에는 두 엔트리 모두의 경로가 들어가지만 디스크에는 나중에 쓰인
    내용만 남으므로, 호출자는 이 목록의 중복 경로 가능성을 감안해야 한다.
    """

    files: list[Path]
    total_bytes: int


def decode_entry_name(info: zipfile.ZipInfo) -> str:
    """엔트리 이름을 올바른 인코딩으로 되돌린다.

    Windows 기본 압축은 CP949로 저장하면서 UTF-8 플래그를 켜지 않는다.
    zipfile은 플래그가 없는 이름을 CP437로 디코딩하므로 한글이 깨진다.
    CP437로 되감아 CP949로 다시 읽는다.

    판별에는 파일명을 쓰지 않지만 로그·오류 메시지 가독성에 필요하다.
    """
    if info.flag_bits & _UTF8_FLAG:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _is_symlink_entry(info: zipfile.ZipInfo) -> bool:
    """Unix에서 만든 ZIP은 external_attr 상위 16비트에 st_mode를 담는다."""
    return stat.S_ISLNK(info.external_attr >> 16)


def _is_reserved_device_name(part: str) -> bool:
    """경로 한 조각의 stem(첫 '.' 이전)이 Windows 예약 장치 이름인지 본다.

    Win32는 경로 정규화 중 뒤의 공백과 점을 제거한다. 예를 들어 'NUL '은
    여전히 널 장치로 해석되므로, 정확한 판별을 위해 정규화된 stem을 검사해야 한다.
    """
    stem = part.split(".", 1)[0].rstrip(" .")
    return stem.upper() in _RESERVED_DEVICE_NAMES


def _safe_destination(name: str, dest_root: Path, resolved_root: Path) -> Path:
    """엔트리 이름을 dest_root 하위의 안전한 경로로 바꾼다.

    Raises:
        UnsafeArchiveError: 절대 경로, 드라이브 지정, 경로 순회,
            Windows 예약 장치 이름일 때.
    """
    normalized = name.replace("\\", "/")

    if normalized.startswith("/"):
        raise UnsafeArchiveError(f"절대 경로 엔트리를 거부한다: {name!r}")

    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts:
        raise UnsafeArchiveError(f"빈 엔트리 이름을 거부한다: {name!r}")
    if any(p == ".." for p in parts):
        raise UnsafeArchiveError(f"경로 순회 엔트리를 거부한다: {name!r}")
    # 드라이브 지정은 선두 컴포넌트뿐 아니라 어느 컴포넌트에서든 나타날 수 있다.
    # 예: "a/C:evil.txt" — 이 경우를 놓치면 dest_root.joinpath(*parts)가
    # "C:evil.txt"에서 다시 앵커링되어 앞의 "a"가 조용히 사라진다.
    if any(ntpath.splitdrive(p)[0] for p in parts):
        raise UnsafeArchiveError(f"드라이브 지정 엔트리를 거부한다: {name!r}")
    if any(_is_reserved_device_name(p) for p in parts):
        raise UnsafeArchiveError(f"Windows 예약 장치 이름 엔트리를 거부한다: {name!r}")

    target = dest_root.joinpath(*parts)
    try:
        target.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise UnsafeArchiveError(f"경로 순회 엔트리를 거부한다: {name!r}") from exc
    return target


def _extract_all(zf: zipfile.ZipFile, dest_root: Path, limits: ExtractLimits) -> ExtractResult:
    infos = zf.infolist()
    if len(infos) > limits.max_entries:
        raise UnsafeArchiveError(
            f"엔트리 수 상한 초과: {len(infos)} > {limits.max_entries}"
        )

    declared = sum(info.file_size for info in infos)
    if declared > limits.max_total_bytes:
        raise UnsafeArchiveError(
            f"전개 용량 상한 초과(선언값): {declared} > {limits.max_total_bytes}"
        )

    resolved_root = dest_root.resolve()

    files: list[Path] = []
    total = 0
    for info in infos:
        name = decode_entry_name(info)
        if _is_symlink_entry(info):
            raise UnsafeArchiveError(f"심볼릭 링크 엔트리를 거부한다: {name!r}")

        target = _safe_destination(name, dest_root, resolved_root)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            while chunk := src.read(_CHUNK_BYTES):
                total += len(chunk)
                # 방어적 이중 확인: zipfile.ZipExtFile은 central directory에
                # 선언된 file_size를 넘는 바이트를 절대 내놓지 않으므로
                # (self._left = zipinfo.file_size로 읽기를 자른다), 이 분기는
                # 현재 stdlib 구현에서는 도달할 수 없다. 실질적인 zip bomb
                # 방어는 위 declared(선언값) 검사다. 이 스트리밍 검사는
                # 향후 stdlib 동작 변경에 대비한 심층 방어로만 남겨둔다.
                if total > limits.max_total_bytes:
                    raise UnsafeArchiveError(
                        f"전개 용량 상한 초과(실측): {total} > {limits.max_total_bytes}"
                    )
                dst.write(chunk)
        files.append(target)

    return ExtractResult(files=files, total_bytes=total)


def safe_extract(
    zip_path: Path,
    dest_root: Path,
    limits: ExtractLimits = ExtractLimits(),
) -> ExtractResult:
    """ZIP을 dest_root에 안전하게 전개한다.

    dest_root가 아직 없으면 이 함수가 만든다. 이미 존재한다면 반드시 비어
    있어야 하며, 그렇지 않으면 UnsafeArchiveError로 거부한다. 실패 시 지우는
    대상은 이 함수가 이번 호출에서 직접 생성한 dest_root뿐이다 — 이미
    존재하던 dest_root는 비어 있었더라도 호출자 소유이므로 절대 건드리지
    않는다(그 디렉터리를 만든 것이 이 함수가 아니기 때문이다). 삭제 자체는
    베스트 에포트다: 파일 잠금 등으로 삭제가 실패하면 그 실패는 조용히
    무시되고 원래 예외만 전파된다.

    호출자를 위한 유의 사항:
    - dest_root는 다른 프로세스가 함께 쓰지 않는 전용 디렉터리여야 하고,
      다른 사용자가 쓸 수 없어야 한다. 컨테인먼트 판정과 실제 open() 사이에
      틈이 있어 그 사이 심볼릭 링크가 심어지면 쓰기가 다른 곳으로 리디렉션될
      수 있다 — 아카이브 내용만으로는 이 틈을 이용할 수 없지만, 디렉터리
      자체가 안전해야 한다는 전제는 호출자 책임이다.
    - 대소문자만 다른 두 엔트리(a.txt, A.txt)는 대소문자를 구분하지 않는
      파일시스템에서 서로 덮어쓴다. ExtractResult.files에는 둘 다 나타나지만
      디스크에는 나중에 쓰인 내용만 남는다.

    Raises:
        UnsafeArchiveError: 안전 검사에 걸렸을 때, 또는 zip_path가 손상되었거나
            올바른 ZIP 형식이 아닐 때(zipfile.BadZipFile). 이 함수가 이번
            호출에서 새로 만든 dest_root만 삭제를 시도한다.
    """
    zip_path = Path(zip_path)
    dest_root = Path(dest_root)

    zip_size = zip_path.stat().st_size
    if zip_size > limits.max_zip_bytes:
        raise UnsafeArchiveError(
            f"ZIP 파일 크기 상한 초과: {zip_size} > {limits.max_zip_bytes}"
        )

    created_dest_root = False
    if dest_root.exists():
        try:
            if any(dest_root.iterdir()):
                raise UnsafeArchiveError(
                    f"대상 폴더가 이미 존재하고 비어 있지 않다: {dest_root}"
                )
        except NotADirectoryError as exc:
            raise UnsafeArchiveError(
                f"대상이 파일이므로 폴더를 만들 수 없다: {dest_root}"
            ) from exc
    else:
        try:
            dest_root.mkdir(parents=True)
            created_dest_root = True
        except FileExistsError as exc:
            raise UnsafeArchiveError(
                f"대상 폴더가 다른 프로세스에 의해 생성됨: {dest_root}"
            ) from exc

    try:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                return _extract_all(zf, dest_root, limits)
        except zipfile.BadZipFile as exc:
            raise UnsafeArchiveError(
                f"ZIP 파일이 손상되었거나 올바른 ZIP 형식이 아니다: {zip_path}"
            ) from exc
    except BaseException:
        if created_dest_root:
            shutil.rmtree(dest_root, ignore_errors=True)
        raise
