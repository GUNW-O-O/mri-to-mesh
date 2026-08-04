"""축3: 스무딩·데시메이션 (스펙 §6.5).

스무딩은 위상을 유지한 채 정점을 옮긴다. 데시메이션은 삼각형 수를 줄인다.
humphrey는 trimesh, 나머지 스무딩과 데시메이션은 VTK를 쓴다 — 새 의존성이
없다. hc_laplacian(pymeshlab)은 후속으로 미룬다.
"""

from __future__ import annotations

import numpy as np


class PostprocessError(RuntimeError):
    """알 수 없는 후처리 method이거나 처리가 실패했다."""


def _to_vtk_poly(verts, faces):
    import vtk
    from vtk.util import numpy_support

    poly = vtk.vtkPolyData()
    pts = vtk.vtkPoints()
    pts.SetData(numpy_support.numpy_to_vtk(np.ascontiguousarray(verts, dtype=np.float64), deep=True))
    poly.SetPoints(pts)

    # VTK 9.6에서 SetCells는 deprecated. offsets+connectivity로 SetData를 쓴다.
    conn = np.ascontiguousarray(np.asarray(faces, dtype=np.int64).ravel())
    offsets = np.arange(0, len(faces) * 3 + 1, 3, dtype=np.int64)
    cells = vtk.vtkCellArray()
    cells.SetData(
        numpy_support.numpy_to_vtkIdTypeArray(offsets, deep=True),
        numpy_support.numpy_to_vtkIdTypeArray(conn, deep=True),
    )
    poly.SetPolys(cells)
    return poly


def _from_vtk_poly(poly):
    from vtk.util import numpy_support

    verts = numpy_support.vtk_to_numpy(poly.GetPoints().GetData())
    conn = numpy_support.vtk_to_numpy(poly.GetPolys().GetConnectivityArray())
    return np.asarray(verts, dtype=np.float64), conn.reshape(-1, 3).astype(np.int64)


def smooth(verts, faces, params):
    """정점을 스무딩한다. 위상(면)은 유지한다.

    Raises:
        PostprocessError: 알 수 없는 method.
    """
    if params.method == "none":
        return np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)

    if params.method == "humphrey":
        import trimesh

        mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        trimesh.smoothing.filter_humphrey(
            mesh, alpha=params.alpha, beta=params.beta, iterations=params.iterations
        )
        return np.asarray(mesh.vertices), np.asarray(mesh.faces)

    if params.method in ("laplacian", "taubin"):
        import vtk

        poly = _to_vtk_poly(verts, faces)
        if params.method == "laplacian":
            f = vtk.vtkSmoothPolyDataFilter()
            f.SetInputData(poly)
            f.SetNumberOfIterations(params.iterations)
            f.SetRelaxationFactor(params.relaxation)
            f.FeatureEdgeSmoothingOff()
            f.BoundarySmoothingOn()
        else:  # taubin — windowed sinc, 수축 억제
            f = vtk.vtkWindowedSincPolyDataFilter()
            f.SetInputData(poly)
            f.SetNumberOfIterations(params.iterations)
            f.SetPassBand(params.pass_band)
            f.SetFeatureAngle(params.feature_angle)
            f.NonManifoldSmoothingOn()
            f.NormalizeCoordinatesOn()
        f.Update()
        return _from_vtk_poly(f.GetOutput())

    raise PostprocessError(f"알 수 없는 스무딩 method: {params.method}")


def decimate(verts, faces, params):
    """삼각형 수를 줄인다.

    Raises:
        PostprocessError: 알 수 없는 method.
    """
    if params.method == "none":
        return np.asarray(verts, dtype=np.float64), np.asarray(faces, dtype=np.int64)

    if params.method == "quadric":
        import vtk

        poly = _to_vtk_poly(verts, faces)
        d = vtk.vtkQuadricDecimation()
        d.SetInputData(poly)
        # targetRatio는 "남길 비율" — vtk의 TargetReduction은 "줄일 비율"
        d.SetTargetReduction(1.0 - params.target_ratio)
        d.Update()
        return _from_vtk_poly(d.GetOutput())

    raise PostprocessError(f"알 수 없는 데시메이션 method: {params.method}")
