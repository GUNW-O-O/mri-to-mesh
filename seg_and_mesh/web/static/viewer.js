// 단일 변형 뷰어 (스펙 §8). 색·group·side는 GLB가 아니라 regions-meta.json에서
// 온다(B안) — GLB는 노드 이름 label_<id>만 실어 나른다.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const canvas = document.getElementById('canvas');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(devicePixelRatio);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(35, innerWidth / innerHeight, 0.01, 1000);
const controls = new OrbitControls(camera, canvas);

scene.add(new THREE.AmbientLight(0xffffff, 0.6));
const d1 = new THREE.DirectionalLight(0xffffff, 1.2); d1.position.set(2, 3, 4); scene.add(d1);
const d2 = new THREE.DirectionalLight(0x7799cc, 0.5); d2.position.set(-3, -1, -2); scene.add(d2);

function resize() {
  renderer.setSize(innerWidth, innerHeight, false);
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize); resize();

let root = null;          // 현재 GLB 그룹
let meta = null;          // regions-meta.json
const matById = new Map();

function clear() {
  if (root) { scene.remove(root); root = null; }
  matById.clear();
}

function frame(group) {
  const box = new THREE.Box3().setFromObject(group);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const radius = Math.max(size.x, size.y, size.z);
  group.position.sub(center);           // bbox 중심을 원점으로 (스펙 §8)
  camera.position.set(0, 0, radius * 2.2);
  camera.near = radius / 100; camera.far = radius * 10;
  camera.updateProjectionMatrix();
  controls.target.set(0, 0, 0); controls.update();
}

function applyColors(transparent) {
  const byNode = new Map(meta.regions.map(r => [r.nodeName, r]));
  root.traverse(obj => {
    if (!obj.isMesh) return;
    const region = byNode.get(obj.name);
    const c = region ? region.color : [200, 200, 200];
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(c[0] / 255, c[1] / 255, c[2] / 255),
      side: THREE.DoubleSide,
      transparent: transparent,
      opacity: transparent ? 0.05 : 1.0,
      depthWrite: !transparent,
    });
    obj.material = mat;
    if (region) matById.set(region.labelId, obj);
  });
}

async function load(jobId, variantId) {
  clear();
  const base = `/api/jobs/${jobId}/variants/${variantId}`;
  meta = await (await fetch(`${base}/regions-meta.json`)).json();
  const buf = await (await fetch(`${base}/regions.glb`)).arrayBuffer();
  const gltf = await new GLTFLoader().parseAsync(buf, '');
  root = gltf.scene;
  scene.add(root);
  applyColors(document.getElementById('transparent').checked);
  frame(root);
  renderMetrics();
  renderGroups();
}

function renderMetrics() {
  const n = meta.regions.length;
  const vols = meta.regions.map(r => r.volumeMm3);
  document.getElementById('metrics').textContent =
    `변형 ${meta.meshVariantId}\n영역 ${n}개\n라벨표 ${meta.labelTable}`;
}

// group·side 둘 다 토글한다(스펙 §8). 각 영역의 최종 visible은 두 토글의
// AND다 — 한쪽만 갱신하면서 m.visible을 직접 덮어쓰면 다른 쪽 상태를
// 잃으므로, 두 Map을 따로 들고 매번 재계산한다.
const groupChecked = new Map();
const sideChecked = new Map();

function updateVisibility() {
  for (const r of meta.regions) {
    const m = matById.get(r.labelId);
    if (!m) continue;
    m.visible = groupChecked.get(r.group) !== false && sideChecked.get(r.side) !== false;
  }
}

function renderToggleList(el, field, state) {
  const values = [...new Set(meta.regions.map(r => r[field]))];
  for (const v of values) {
    state.set(v, true);
    const lab = document.createElement('label');
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = true;
    cb.onchange = () => { state.set(v, cb.checked); updateVisibility(); };
    lab.append(cb, ` ${v}`);
    el.append(lab);
  }
}

function renderGroups() {
  groupChecked.clear(); sideChecked.clear();
  const el = document.getElementById('groups');
  el.innerHTML = '';
  renderToggleList(el, 'group', groupChecked);
  renderToggleList(el, 'side', sideChecked);
}

document.getElementById('load').onclick = () =>
  load(document.getElementById('jobId').value.trim(),
       document.getElementById('variantId').value.trim());

document.getElementById('transparent').onchange = (e) => {
  if (root) applyColors(e.target.checked);
};

(function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
})();
