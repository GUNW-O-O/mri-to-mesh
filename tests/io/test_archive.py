"""ZIP 안전 해제 테스트 (스펙 §6.2)."""

from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path

import pytest

from seg_and_mesh.io.archive import (
    ExtractLimits,
    UnsafeArchiveError,
    decode_entry_name,
    safe_extract,
)


def _zip_with(tmp_path: Path, entries: dict[str, bytes], name: str = "in.zip") -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for entry_name, data in entries.items():
            zf.writestr(entry_name, data)
    return path


def _raw_zip(path: Path, entries: list[tuple[bytes, bytes]]) -> Path:
    """파일명 바이트를 그대로 넣고 UTF-8 플래그를 켜지 않는 ZIP을 만든다.

    zipfile.ZipFile.writestr는 비ASCII 이름을 만나면 UTF-8로 인코딩하고
    플래그 0x800을 강제로 켠다. 그래서 CP949 이름을 재현하려면 헤더를 직접
    써야 한다. 압축 없음(stored)으로 최소 구조만 만든다.
    """
    local = b""
    central = b""
    offset = 0
    for name_bytes, data in entries:
        crc = zlib.crc32(data) & 0xFFFFFFFF
        n, size = len(name_bytes), len(data)
        local += (
            struct.pack("<4sHHHHHIIIHH", b"PK\x03\x04", 20, 0, 0, 0, 0, crc, size, size, n, 0)
            + name_bytes
            + data
        )
        central += (
            struct.pack("<4sHHHHHHIIIHHHHHII", b"PK\x01\x02", 20, 20, 0, 0, 0, 0,
                        crc, size, size, n, 0, 0, 0, 0, 0, offset)
            + name_bytes
        )
        offset = len(local)
    eocd = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, len(entries), len(entries),
                       len(central), len(local), 0)
    path.write_bytes(local + central + eocd)
    return path


def test_extracts_nested_dirs(tmp_path):
    src = _zip_with(tmp_path, {
        "study/series1/IM000001": b"a" * 10,
        "study/series1/IM000002": b"b" * 10,
        "study/series2/IM000001": b"c" * 10,
    })
    dest = tmp_path / "out"

    result = safe_extract(src, dest)

    assert result.total_bytes == 30
    assert len(result.files) == 3
    assert (dest / "study" / "series1" / "IM000001").read_bytes() == b"a" * 10


def test_rejects_parent_traversal(tmp_path):
    src = _zip_with(tmp_path, {"../evil.txt": b"x"})
    with pytest.raises(UnsafeArchiveError, match="경로 순회"):
        safe_extract(src, tmp_path / "out")


def test_rejects_deep_parent_traversal(tmp_path):
    src = _zip_with(tmp_path, {"a/b/../../../evil.txt": b"x"})
    with pytest.raises(UnsafeArchiveError, match="경로 순회"):
        safe_extract(src, tmp_path / "out")


def test_rejects_absolute_path(tmp_path):
    src = _zip_with(tmp_path, {"/etc/passwd": b"x"})
    with pytest.raises(UnsafeArchiveError, match="절대 경로"):
        safe_extract(src, tmp_path / "out")


def test_rejects_windows_drive_path(tmp_path):
    src = _zip_with(tmp_path, {"C:/Windows/evil.dll": b"x"})
    with pytest.raises(UnsafeArchiveError, match="드라이브"):
        safe_extract(src, tmp_path / "out")


@pytest.mark.parametrize("entry_name", ["a/C:evil.txt", "a/C:/evil.txt"])
def test_rejects_drive_spec_in_non_leading_component(tmp_path, entry_name):
    """드라이브 지정이 첫 컴포넌트가 아니어도 거부한다 (dest_root.joinpath 재-앵커링 방지)."""
    safe_name = entry_name.replace(":", "_").replace("/", "-")
    src = _zip_with(tmp_path, {entry_name: b"x"}, name=f"drive-{safe_name}.zip")
    with pytest.raises(UnsafeArchiveError, match="드라이브"):
        safe_extract(src, tmp_path / f"out-{safe_name}")


def test_rejects_backslash_traversal(tmp_path):
    """Windows 구분자를 쓴 순회도 막는다."""
    src = _zip_with(tmp_path, {"..\\evil.txt": b"x"})
    with pytest.raises(UnsafeArchiveError, match="경로 순회"):
        safe_extract(src, tmp_path / "out")


def test_rejects_symlink_entry(tmp_path):
    """심볼릭 링크 엔트리는 external_attr의 S_IFLNK로 판별해 거부한다."""
    src = tmp_path / "link.zip"
    with zipfile.ZipFile(src, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.create_system = 3  # Unix
        info.external_attr = (0o120777 << 16)  # S_IFLNK | 0777
        zf.writestr(info, "/etc/passwd")

    with pytest.raises(UnsafeArchiveError, match="심볼릭 링크"):
        safe_extract(src, tmp_path / "out")


def test_rejects_when_declared_size_exceeds_limit(tmp_path):
    src = _zip_with(tmp_path, {"big.bin": b"x" * 5000})
    limits = ExtractLimits(max_total_bytes=1000)
    with pytest.raises(UnsafeArchiveError, match="용량 상한"):
        safe_extract(src, tmp_path / "out", limits)


def test_rejects_when_entry_count_exceeds_limit(tmp_path):
    src = _zip_with(tmp_path, {f"f{i}": b"x" for i in range(10)})
    limits = ExtractLimits(max_entries=5)
    with pytest.raises(UnsafeArchiveError, match="엔트리 수 상한"):
        safe_extract(src, tmp_path / "out", limits)


def test_rejects_zip_file_exceeding_max_zip_bytes(tmp_path):
    """중앙 디렉터리를 파싱해 ZipInfo 목록을 만들기 전에 ZIP 파일 자체 크기를 검사한다.

    max_entries 검사는 zipfile.ZipFile()이 이미 전체 central directory를 파싱한
    뒤에야 실행되므로, 그 전에 파일 크기로 먼저 걸러내야 상한이 실효성을 가진다.

    유효한 ZIP이 아닌 쓰레기 바이트를 쓰는 이유: 사전 검사는 크기로만 판별하고
    구조를 확인하지 않아야 한다. 만약 zipfile.ZipFile() 이후에 검사한다면
    BadZipFile이 먼저 발생하므로, "ZIP 파일 크기 상한" 메시지가 나오는지 확인하면
    사전 검사의 정확한 시점을 검증할 수 있다.
    """
    # 상한보다 큰 쓰레기 바이트 (유효한 ZIP이 아님)
    src = tmp_path / "oversized.bin"
    src.write_bytes(b"JUNK" * 100)
    limits = ExtractLimits(max_zip_bytes=10)
    with pytest.raises(UnsafeArchiveError, match="ZIP 파일 크기 상한"):
        safe_extract(src, tmp_path / "out", limits)


def test_cleans_up_dest_on_failure(tmp_path):
    """안전 검사에 걸리면 잡 전체를 거부한다 — 부분 전개물을 남기지 않는다."""
    src = _zip_with(tmp_path, {"ok.bin": b"x" * 10, "../evil.txt": b"y"})
    dest = tmp_path / "out"

    with pytest.raises(UnsafeArchiveError):
        safe_extract(src, dest)

    assert not dest.exists()


def test_refuses_preexisting_nonempty_dest_root(tmp_path):
    """dest_root가 이미 존재하고 비어 있지 않으면 거부한다.

    실패 시 rmtree가 삭제하는 대상은 이 함수가 소유(직접 생성했거나 비어 있음을
    확인)한 디렉터리뿐이어야 한다. 그렇지 않으면 무관한 실패(예: 손상된 ZIP)가
    호출자의 기존 내용을 통째로 지워버릴 수 있다.
    """
    src = _zip_with(tmp_path, {"ok.bin": b"x" * 10})
    dest = tmp_path / "out"
    dest.mkdir()
    sentinel = dest / "preexisting.txt"
    sentinel.write_bytes(b"keep me")

    with pytest.raises(UnsafeArchiveError, match="비어 있지"):
        safe_extract(src, dest)

    assert sentinel.exists()
    assert sentinel.read_bytes() == b"keep me"


def test_rejects_dest_root_as_file(tmp_path):
    """dest_root가 이미 파일로 존재하면 UnsafeArchiveError로 거부한다.

    iterdir()이 NotADirectoryError를 던지지만, 이를 UnsafeArchiveError로
    감싸서 모듈의 약속된 예외 계약을 지켜야 한다.
    """
    src = _zip_with(tmp_path, {"ok.bin": b"x" * 10})
    dest = tmp_path / "out"
    dest.write_bytes(b"i am a file")

    with pytest.raises(UnsafeArchiveError):
        safe_extract(src, dest)


@pytest.mark.parametrize("entry_name", ["NUL", "nul.dcm", "a/CON", "COM1.txt", "lpt9", "NUL ", "nul ", "a/nul "])
def test_rejects_windows_reserved_device_name(tmp_path, entry_name):
    """Windows 예약 장치 이름 엔트리는 데이터를 널 장치로 흘려보내므로 거부한다.

    Win32는 경로 정규화 중 뒤의 공백과 점을 제거하므로, 'NUL ' 같은 뒤에 공백이 있는
    이름도 널 장치로 해석된다. 또한 정규식 매치가 아닌 정확한 stem 매치이므로
    'CON000123' 같은 DICOM 파일은 계속 허용해야 한다.
    """
    safe_name = entry_name.replace("/", "-").replace(" ", "_")
    src = _zip_with(tmp_path, {entry_name: b"x"}, name=f"rsv-{safe_name}.zip")
    with pytest.raises(UnsafeArchiveError, match="예약"):
        safe_extract(src, tmp_path / f"out-{safe_name}")


def test_extracts_dicom_style_names(tmp_path):
    """DICOM 파일명 규칙상 'CON000123' 같은 이름은 예약 장치 이름이 아니므로 추출된다.

    단순 prefix 매치 대신 stem 정확 매치를 하는 이유가 여기다.
    """
    src = _zip_with(tmp_path, {
        "CON000123": b"data1",
        "LPT9file.dcm": b"data2",
        "PRN_backup": b"data3",
    })
    dest = tmp_path / "out"

    result = safe_extract(src, dest)

    assert len(result.files) == 3
    assert (dest / "CON000123").read_bytes() == b"data1"
    assert (dest / "LPT9file.dcm").read_bytes() == b"data2"
    assert (dest / "PRN_backup").read_bytes() == b"data3"


def test_decodes_cp949_name_without_utf8_flag():
    """Windows 기본 압축은 CP949로 저장하면서 UTF-8 플래그(0x800)를 켜지 않는다.

    zipfile은 이를 CP437로 해석해 이름이 깨진다. 플래그가 없으면 CP949로 재디코딩한다.
    """
    raw = "환자01".encode("cp949")
    info = zipfile.ZipInfo(raw.decode("cp437"))
    info.flag_bits = 0

    assert decode_entry_name(info) == "환자01"


def test_keeps_utf8_name_when_flag_set():
    info = zipfile.ZipInfo("환자01")
    info.flag_bits = 0x800

    assert decode_entry_name(info) == "환자01"


def test_falls_back_to_raw_name_when_cp949_decode_fails():
    """cp437로 인코딩 자체가 불가능한 이름은 except 분기를 타 원본을 그대로 돌려준다.

    "환자01"은 cp437 문자표에 없는 한글이므로 .encode("cp437")에서
    UnicodeEncodeError가 발생해 실제로 except 분기를 실행시킨다.
    """
    info = zipfile.ZipInfo("환자01")
    info.flag_bits = 0

    assert decode_entry_name(info) == "환자01"


def test_rejects_truncated_zip_as_unsafe_archive(tmp_path):
    """중앙 디렉터리가 잘려나간 손상된 ZIP은 zipfile.BadZipFile이 아니라
    UnsafeArchiveError로 감싸 던져야 한다 — prepare_input의 계약이
    UnsupportedInputError/UnsafeArchiveError만 약속하기 때문이다.
    """
    good = _zip_with(tmp_path, {"a.txt": b"x" * 1000}, name="good.zip")
    data = good.read_bytes()
    truncated = tmp_path / "truncated.zip"
    truncated.write_bytes(data[:-50])  # central directory/EOCD를 잘라낸다

    with pytest.raises(UnsafeArchiveError):
        safe_extract(truncated, tmp_path / "out")


def test_preexisting_empty_dest_root_survives_failure(tmp_path):
    """이 함수가 만들지 않은, 이미 존재하던 빈 dest_root는 실패해도 지우지 않는다.

    비어 있음 검사만 통과하면 곧바로 rmtree 대상이 되던 예전 동작을 막는다 —
    이 함수가 실제로 생성한 디렉터리인지를 추적해야 한다.
    """
    src = _zip_with(tmp_path, {"../evil.txt": b"x"})
    dest = tmp_path / "out"
    dest.mkdir()  # 미리 존재하는 빈 폴더 — 이 함수가 만든 것이 아니다

    with pytest.raises(UnsafeArchiveError):
        safe_extract(src, dest)

    assert dest.exists()


def test_cp949_name_is_used_on_disk(tmp_path):
    """Windows 기본 압축이 만든 ZIP을 재현해 전개 후 경로가 한글인지 본다."""
    src = _raw_zip(tmp_path / "kr.zip", [("환자01/IM000001".encode("cp949"), b"data")])
    dest = tmp_path / "out"

    result = safe_extract(src, dest)

    assert result.files == [dest / "환자01" / "IM000001"]
    assert (dest / "환자01" / "IM000001").read_bytes() == b"data"
