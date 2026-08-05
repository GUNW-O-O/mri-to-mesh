// 다중 변형 뷰어. 생성된 변형(GLB)들을 한 scene에 world-mm 그대로 일렬 배치하고
// (스케일 정규화 안 함 — 같은 해부구조라 크기·품질 직접 비교), pan으로 이동하며
// 본다. 하단 토글로 각 변형 on/off, 빅뱅(explode)으로 영역별 외부면 확인.
// 색은 GLB가 아니라 regions-meta.json에서 온다(B안, GLB는 label_<id> 노드만).
//
// app.js가 제어: new Viewer(canvas) 후
//   await viewer.loadVariants(jobId, [variantId,...])
//   viewer.setVisible(variantId, on) / setBigbang(t) / setTransparent(on)
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { glbUrl, metaUrl } from './api.js';

const EXPLODE_K = 0.6;   // 빅뱅 최대 이동 = 영역 중심거리 × 이 배수
const SLOT_GAP = 1.35;   // 슬롯 간격 = 최대 슬롯 폭 × 이 배수

export class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(devicePixelRatio);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, innerWidth / innerHeight, 0.01, 5000);
    // brain-educate와 같은 관성 댐핑. pan은 켠다 — 변형들이 일렬이라 pan으로
    // 옆 변형으로 이동해 확인한다(사용자 요구).
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.05;
    this.controls.enablePan = true;
    this.camera.up.set(0, 1, 0);

    this.scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const d1 = new THREE.DirectionalLight(0xffffff, 1.2); d1.position.set(2, 3, 4); this.scene.add(d1);
    const d2 = new THREE.DirectionalLight(0x7799cc, 0.5); d2.position.set(-3, -1, -2); this.scene.add(d2);

    this.slots = [];          // {variantId, root, meta, meshes[], visible}
    this.transparent = false;
    this.bigbang = 0;
    this.loadGeneration = 0;

    this._resize = () => this._onResize();
    addEventListener('resize', this._resize);
    this._onResize();

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
    for (const s of this.slots) this._disposeSlot(s);
    this.slots = [];
    this.bigbang = 0;
    const metrics = document.getElementById('metrics');
    if (metrics) metrics.textContent = '';
  }

  _disposeSlot(s) {
    this.scene.remove(s.root);
    s.root.traverse(o => {
      if (o.isMesh) { o.geometry?.dispose?.(); o.material?.dispose?.(); }
    });
  }

  // 모든 변형을 로드해 일렬로 세운다. 이전 것은 정리한다.
  async loadVariants(jobId, variantIds) {
    this.clear();
    const generation = this.loadGeneration;
    const built = [];
    for (const variantId of variantIds) {
      const slot = await this._buildSlot(jobId, variantId);
      if (generation !== this.loadGeneration) { if (slot) this._disposeSlot(slot); return; }
      if (slot) { built.push(slot); this.scene.add(slot.root); }
    }
    this.slots = built;
    this._relayout();
    this._frameAll();
    this._renderMetrics();
  }

  async _buildSlot(jobId, variantId) {
    const metaRes = await fetch(metaUrl(jobId, variantId));
    if (!metaRes.ok) throw new Error(`${metaRes.status} meta 로드 실패 (${variantId})`);
    const meta = await metaRes.json();
    const glbRes = await fetch(glbUrl(jobId, variantId));
    if (!glbRes.ok) throw new Error(`${glbRes.status} GLB 로드 실패 (${variantId})`);
    const gltf = await new GLTFLoader().parseAsync(await glbRes.arrayBuffer(), '');

    const inner = gltf.scene;
    // 노멀 없는 GLB(ComputeNormalsOff)라 스무스 노멀을 계산해 음영을 낸다.
    inner.traverse(o => {
      if (o.isMesh && o.geometry && !o.geometry.attributes.normal) o.geometry.computeVertexNormals();
    });
    inner.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(inner);
    const center = box.getCenter(new THREE.Vector3());
    inner.position.sub(center);                    // 브레인 중심을 슬롯 원점으로

    const meshes = [];
    const byNode = new Map(meta.regions.map(r => [r.nodeName, r]));
    inner.traverse(o => {
      if (!o.isMesh) return;
      o.geometry.computeBoundingBox();
      const gc = o.geometry.boundingBox.getCenter(new THREE.Vector3());
      // 영역 중심의 브레인-중심 상대 벡터(빅뱅 방향). 정점이 world-mm라 지오메트리
      // 좌표가 곧 world이고, 중심을 뺀 게 상대 오프셋이다.
      o.userData.rel = gc.sub(center);
      const region = byNode.get(o.name);
      o.userData.color = region ? region.color : [200, 200, 200];
      o.material = this._makeMaterial(o.userData.color);
      meshes.push(o);
    });

    // 업라이트: RAS(+Z=상하)를 three.js up(+Y)에 맞춰 세운다(-90° about X).
    const root = new THREE.Group();
    root.rotation.x = -Math.PI / 2;
    root.add(inner);
    const size = box.getSize(new THREE.Vector3());
    root.userData.width = Math.max(size.x, size.z);   // 회전 후 가로폭(x·z)
    root.userData.radius = Math.max(size.x, size.y, size.z);

    return { variantId, root, meta, meshes, visible: true };
  }

  _makeMaterial(c) {
    return new THREE.MeshStandardMaterial({
      color: new THREE.Color(c[0] / 255, c[1] / 255, c[2] / 255),
      side: THREE.DoubleSide,
      transparent: this.transparent,
      opacity: this.transparent ? 0.12 : 1.0,
      depthWrite: !this.transparent,
    });
  }

  // 보이는 슬롯만 좌→우로 촘촘히 재배치(꺼진 건 자리 안 차지).
  _relayout() {
    const vis = this.slots.filter(s => s.visible);
    const maxW = vis.reduce((m, s) => Math.max(m, s.root.userData.width), 1);
    const gap = maxW * SLOT_GAP;
    const n = vis.length;
    vis.forEach((s, i) => { s.root.position.x = (i - (n - 1) / 2) * gap; });
    for (const s of this.slots) s.root.visible = s.visible;
  }

  _frameAll() {
    const vis = this.slots.filter(s => s.visible);
    if (!vis.length) return;
    this.scene.updateMatrixWorld(true);
    const box = new THREE.Box3();
    for (const s of vis) box.expandByObject(s.root);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const radius = Math.max(size.x, size.y, size.z);
    // 진입: 정면(얼굴=업라이트 후 -Z)에서 20° 위. row 전체가 들어오게 거리 잡음.
    const dist = radius * 1.6 + size.x * 0.2;
    const el = Math.PI / 9;
    this.camera.position.set(center.x, center.y + dist * Math.sin(el), center.z - dist * Math.cos(el));
    this.camera.near = radius / 100; this.camera.far = radius * 20;
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(center);
    this.camera.lookAt(center);
    this.controls.update();
  }

  setVisible(variantId, on) {
    const s = this.slots.find(s => s.variantId === variantId);
    if (!s) return;
    s.visible = on;
    this._relayout();
    this._frameAll();
    this._renderMetrics();
  }

  setBigbang(t) {
    this.bigbang = t;
    for (const s of this.slots) {
      for (const m of s.meshes) {
        m.position.copy(m.userData.rel).multiplyScalar(t * EXPLODE_K);
      }
    }
  }

  setTransparent(on) {
    this.transparent = on;
    for (const s of this.slots) {
      for (const m of s.meshes) m.material = this._makeMaterial(m.userData.color);
    }
  }

  _renderMetrics() {
    const el = document.getElementById('metrics');
    if (!el) return;
    const vis = this.slots.filter(s => s.visible);
    el.textContent = `변형 ${vis.length}/${this.slots.length} 표시`;
  }

  _animate() {
    requestAnimationFrame(this._animate);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
