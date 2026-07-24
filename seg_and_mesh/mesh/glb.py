"""GLB 작성 (스펙 §6.7).

trimesh로 직접 쓴다 — 렌더러 씬 export(pyvista.Plotter.export_gltf)는 조명·
카메라를 함께 싣고 압축이 없어 파일이 커진다. 노드명을 label_<id>로 확정하고
색을 굽지 않는다(색은 regions-meta.json이 운반, B안). 정점은 이미 world mm다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh


class GlbError(RuntimeError):
    """GLB를 쓰지 못했다."""


def write_glb(meshes: dict, out_path) -> int:
    """label_<id> 노드로 GLB를 쓰고 바이트 크기를 낸다.

    Args:
        meshes: {label_id: (verts[world mm], faces)}.
        out_path: 쓸 경로. 상위 폴더가 없으면 만든다.

    Raises:
        GlbError: meshes가 비었거나 쓰기에 실패했을 때.
    """
    if not meshes:
        raise GlbError("빈 메시로 GLB를 쓸 수 없다")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    scene = trimesh.Scene()
    for label_id, (verts, faces) in meshes.items():
        mesh = trimesh.Trimesh(
            vertices=np.asarray(verts, dtype=np.float32),
            faces=np.asarray(faces, dtype=np.int64),
            process=False,
        )
        name = f"label_{label_id}"
        scene.add_geometry(mesh, node_name=name, geom_name=name)

    try:
        data = scene.export(file_type="glb")
    except Exception as exc:  # trimesh는 다양한 예외를 던진다
        raise GlbError(f"GLB export 실패: {out_path}") from exc

    out_path.write_bytes(data)
    return len(data)
