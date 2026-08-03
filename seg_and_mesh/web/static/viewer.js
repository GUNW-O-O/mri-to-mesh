// 단일 변형 뷰어 (스펙 §8). 색·group·side는 GLB가 아니라 regions-meta.json에서
// 온다(B안) — GLB는 노드 이름 label_<id>만 실어 나른다.
//
// app.js가 생성·제어한다: `new Viewer(canvas)` 후 `viewer.showVariant(jobId, variantId)`.
// P2에서 이 클래스에 비교 슬롯 배열을 더할 예정이다.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { glbUrl, metaUrl } from './api.js';

export class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(devicePixelRatio);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, innerWidth / innerHeight, 0.01, 1000);
    this.controls = new OrbitControls(this.camera, canvas);

    this.scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const d1 = new THREE.DirectionalLight(0xffffff, 1.2); d1.position.set(2, 3, 4); this.scene.add(d1);
    const d2 = new THREE.DirectionalLight(0x7799cc, 0.5); d2.position.set(-3, -1, -2); this.scene.add(d2);

    this.root = null;          // 현재 GLB 그룹
    this.meta = null;          // regions-meta.json
    this.matById = new Map();
    this.loadGeneration = 0;

    // group·side 둘 다 토글한다(스펙 §8). 각 영역의 최종 visible은 두 토글의
    // AND다 — 한쪽만 갱신하면서 m.visible을 직접 덮어쓰면 다른 쪽 상태를
    // 잃으므로, 두 Map을 따로 들고 매번 재계산한다.
    this.groupChecked = new Map();
    this.sideChecked = new Map();

    this._resize = () => this._onResize();
    addEventListener('resize', this._resize);
    this._onResize();

    const transparentEl = document.getElementById('transparent');
    if (transparentEl) {
      transparentEl.onchange = (e) => {
        if (this.root) this._applyColors(e.target.checked);
      };
    }

    this._animate = this._animate.bind(this);
    requestAnimationFrame(this._animate);
  }

  _onResize() {
    this.renderer.setSize(innerWidth, innerHeight, false);
    this.camera.aspect = innerWidth / innerHeight;
    this.camera.updateProjectionMatrix();
  }

  clear() {
    this.loadGeneration += 1;
    if (this.root) { this.scene.remove(this.root); this.root = null; }
    this.meta = null;
    this.matById.clear();
    const groups = document.getElementById('groups');
    const metrics = document.getElementById('metrics');
    if (groups) groups.innerHTML = '';
    if (metrics) metrics.textContent = '';
  }

  _frame(group) {
    const box = new THREE.Box3().setFromObject(group);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(size.x, size.y, size.z);
    group.position.sub(center);           // bbox 중심을 원점으로 (스펙 §8)
    this.camera.position.set(0, 0, radius * 2.2);
    this.camera.near = radius / 100; this.camera.far = radius * 10;
    this.camera.updateProjectionMatrix();
    this.controls.target.set(0, 0, 0); this.controls.update();
  }

  _applyColors(transparent) {
    const byNode = new Map(this.meta.regions.map(r => [r.nodeName, r]));
    this.root.traverse(obj => {
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
      if (region) this.matById.set(region.labelId, obj);
    });
  }

  setTransparent(transparent) {
    if (this.root) this._applyColors(transparent);
  }

  async showVariant(jobId, variantId) {
    this.clear();
    const generation = this.loadGeneration;
    try {
      const metaRes = await fetch(metaUrl(jobId, variantId));
      if (!metaRes.ok) throw new Error(`${metaRes.status} meta 로드 실패`);
      const meta = await metaRes.json();
      const glbRes = await fetch(glbUrl(jobId, variantId));
      if (!glbRes.ok) throw new Error(`${glbRes.status} GLB 로드 실패`);
      const buf = await glbRes.arrayBuffer();
      const gltf = await new GLTFLoader().parseAsync(buf, '');
      if (generation !== this.loadGeneration) return;
      this.meta = meta;
      this.root = gltf.scene;
      this.scene.add(this.root);
      this._applyColors(document.getElementById('transparent')?.checked ?? false);
      this._frame(this.root);
      this._renderMetrics();
      this._renderGroups();
    } catch (err) {
      if (generation !== this.loadGeneration) return;
      throw err;
    }
  }

  _renderMetrics() {
    const el = document.getElementById('metrics');
    if (!el) return;
    const n = this.meta.regions.length;
    el.textContent =
      `변형 ${this.meta.meshVariantId}\n영역 ${n}개\n라벨표 ${this.meta.labelTable}`;
  }

  _updateVisibility() {
    for (const r of this.meta.regions) {
      const m = this.matById.get(r.labelId);
      if (!m) continue;
      m.visible = this.groupChecked.get(r.group) !== false && this.sideChecked.get(r.side) !== false;
    }
  }

  _renderToggleList(el, field, state) {
    const values = [...new Set(this.meta.regions.map(r => r[field]))];
    for (const v of values) {
      state.set(v, true);
      const lab = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = true;
      cb.onchange = () => { state.set(v, cb.checked); this._updateVisibility(); };
      lab.append(cb, ` ${v}`);
      el.append(lab);
    }
  }

  _renderGroups() {
    this.groupChecked.clear(); this.sideChecked.clear();
    const el = document.getElementById('groups');
    if (!el) return;
    el.innerHTML = '';
    this._renderToggleList(el, 'group', this.groupChecked);
    this._renderToggleList(el, 'side', this.sideChecked);
  }

  _animate() {
    requestAnimationFrame(this._animate);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
