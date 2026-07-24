"""패키지가 임포트되고 버전이 노출되는지 확인한다."""

import seg_and_mesh
import seg_and_mesh.io


def test_version_is_exposed():
    assert seg_and_mesh.__version__ == "0.1.0"


def test_io_subpackage_does_not_shadow_stdlib_io():
    """seg_and_mesh.io가 표준 라이브러리 io를 가리지 않는지 확인한다."""
    import io as stdlib_io

    assert hasattr(stdlib_io, "BytesIO")
    assert seg_and_mesh.io is not stdlib_io
