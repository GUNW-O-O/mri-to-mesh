"""패키지가 임포트되고 버전이 노출되는지 확인한다."""

import mri2mesh
import mri2mesh.io


def test_version_is_exposed():
    assert mri2mesh.__version__ == "0.1.0"


def test_io_subpackage_does_not_shadow_stdlib_io():
    """mri2mesh.io가 표준 라이브러리 io를 가리지 않는지 확인한다."""
    import io as stdlib_io

    assert hasattr(stdlib_io, "BytesIO")
    assert mri2mesh.io is not stdlib_io
