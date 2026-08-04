"""축2: 표면 추출기 (스펙 §6.5).

각 추출기는 스칼라장 + isolevel을 받아 (정점[voxel 좌표], 면)을 낸다.
정점은 아직 voxel 좌표다 — affine은 generate가 한 번만 적용한다(스펙 §6.7).

v1은 전부 라벨별(마스크 하나) 추출이다. 다중라벨 1패스(vtkSurfaceNets3D)는
공유경계라 라벨별로 닫히지 않아 부피가 무의미하다(프로토타입 실측). 라벨별
이진 추출은 태생적으로 닫혀 부피가 맞다.
"""

from __future__ import annotations

import numpy as np


class ExtractError(RuntimeError):
    """알 수 없는 추출기이거나 추출이 실패했다."""


def _skimage_mc(field, isolevel, options):
    from skimage import measure

    pad = 1
    padded = np.pad(field, pad, mode="constant", constant_values=field.min())
    verts, faces, _, _ = measure.marching_cubes(padded, level=isolevel)
    return verts - pad, faces


def _pymcubes(field, isolevel, options):
    import mcubes

    pad = 1
    padded = np.pad(field, pad, mode="constant", constant_values=field.min())
    verts, faces = mcubes.marching_cubes(padded, isolevel)
    return np.asarray(verts) - pad, np.asarray(faces)


def _to_vtk_image(field):
    import vtk
    from vtk.util import numpy_support

    img = vtk.vtkImageData()
    img.SetDimensions(*field.shape)
    img.SetSpacing(1.0, 1.0, 1.0)
    img.SetOrigin(0.0, 0.0, 0.0)
    arr = numpy_support.numpy_to_vtk(
        field.ravel(order="F").astype(np.float32), deep=True
    )
    img.GetPointData().SetScalars(arr)
    return img


def _vtk_poly_to_arrays(poly):
    from vtk.util import numpy_support

    pts = numpy_support.vtk_to_numpy(poly.GetPoints().GetData())
    conn = numpy_support.vtk_to_numpy(poly.GetPolys().GetConnectivityArray())
    offs = numpy_support.vtk_to_numpy(poly.GetPolys().GetOffsetsArray())
    sizes = np.diff(offs)
    if not np.all(sizes == 3):
        import vtk

        tri = vtk.vtkTriangleFilter()
        tri.SetInputData(poly)
        tri.Update()
        return _vtk_poly_to_arrays(tri.GetOutput())
    return np.asarray(pts), conn.reshape(-1, 3)


def _vtk_flyingedges(field, isolevel, options):
    import vtk

    img = _to_vtk_image(field)
    fe = vtk.vtkFlyingEdges3D()
    fe.SetInputData(img)
    fe.SetValue(0, isolevel)
    fe.ComputeNormalsOff()
    fe.Update()
    return _vtk_poly_to_arrays(fe.GetOutput())


def _vtk_surfacenets(field, isolevel, options):
    import vtk

    # SurfaceNets는 라벨 이미지를 기대한다. 이진화해 라벨 1 vs 0으로 준다.
    binary = (field >= isolevel).astype(np.float32)
    img = _to_vtk_image(binary)
    sn = vtk.vtkSurfaceNets3D()
    sn.SetInputData(img)
    sn.GenerateLabels(1, 1, 1)
    sn.SetValue(0, 1)
    sn.SetOutputStyleToBoundary()
    sn.Update()
    return _vtk_poly_to_arrays(sn.GetOutput())


def _vtk_contour_perlabel(field, isolevel, options):
    import vtk

    img = _to_vtk_image(field)
    cf = vtk.vtkContourFilter()
    cf.SetInputData(img)
    cf.SetValue(0, isolevel)
    cf.ComputeNormalsOff()
    cf.Update()
    return _vtk_poly_to_arrays(cf.GetOutput())


_EXTRACTORS = {
    "skimage_mc": _skimage_mc,
    "pymcubes": _pymcubes,
    "vtk_flyingedges": _vtk_flyingedges,
    "vtk_surfacenets": _vtk_surfacenets,
    "vtk_contour_perlabel": _vtk_contour_perlabel,
}

EXTRACTOR_NAMES = tuple(_EXTRACTORS)


def extract(field, isolevel, name, options=None):
    """스칼라장 + isolevel -> (정점[voxel], 면).

    Raises:
        ExtractError: 알 수 없는 추출기.
    """
    impl = _EXTRACTORS.get(name)
    if impl is None:
        raise ExtractError(f"알 수 없는 추출기: {name} (가능: {EXTRACTOR_NAMES})")
    verts, faces = impl(field, isolevel, dict(options or {}))
    return np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)
