# 에셋별 비교(scissor) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 잡 간(에셋별) 메쉬 비교를 2열 scissor 뷰(동기 회전 + 크기 정규화 토글)로 추가한다.

**Architecture:** 프론트엔드 전용(백엔드 변경 0). 단일 `<canvas>`/renderer를 소유한 `Viewer`에 모드 분기(`single` 기존 pan-row / `compare` 신규 scissor)를 더한다. compare 모드는 scene·camera 2개를 `setScissorTest`로 좌/우 반씩 그리고, 좌 카메라 OrbitControls를 매 프레임 우 카메라에 복사해 동기 회전한다. 기존 표기 컨트롤(반투명·빅뱅·영역 group/side)은 두 모드 공통으로 동작하게 `_allBrains()`로 일반화한다.

**Tech Stack:** three.js(vendored), 순수 ESM(빌드 없음), FastAPI 정적 서빙.

**Scope note — 조건 프리셋 보류:** 설계 문서(`2026-08-05-asset-compare-presets-design.md`) §5 조건 프리셋(노화/치매/알콜)은 **이번 구현에서 제외**한다 — 영역 세트의 원천인 brain-educate가 작업 중이라 확정 전이다. scissor 비교·정규화·기존 영역 토글만 구현한다. 프리셋은 educate 안정 후 별도로 추가한다.

## Global Constraints

- 완전 로컬 · 외부 통신 0. `innerHTML`에 꽂는 값은 `esc()`로 이스케이프.
- 백엔드/엔드포인트 추가 금지 — `listJobs`·`getStatus(jobId)`·기존 GLB/meta 엔드포인트만.
- world-mm 공유 스케일이 기본(정규화 OFF). pan-row(잡 내, 같은 사람)는 정규화하지 않는다.
- compare는 딱 2창. 3창 이상 안 함.
- JS 단위 테스트 인프라 없음 — 검증 = `node --check` + `uv run pytest`(회귀, 백엔드 무변경 확인) + 명시적 수동 체크리스트. `python`은 Windows 스텁이라 깨짐, 항상 `uv run`.
- index.html의 기존 앵커 id(`mesh-options`, `gen-variant`, `dicom-info`, `job-info`)는 웹 테스트가 검사하므로 **제거하지 않는다**.

---

### Task 1: viewer.js — compare(scissor) 모드 + 표기 컨트롤 일반화

`Viewer`에 두 번째 모드를 더한다. scene·camera 2개를 `setScissorTest`로 좌/우 반씩 그리고, 좌 카메라 OrbitControls를 매 프레임 우 카메라에 복사해 동기 회전한다. 각 창은 잡+변형을 독립 로드한다. 정규화 토글은 두 창을 공통 크기로 스케일한다. 기존 표기 컨트롤(반투명·빅뱅·group/side)은 `_allBrains()`로 일반화해 두 모드에서 동작한다.

기존 `_buildSlot(jobId, variantId)`가 `{ variantId, root, meta, meshes, visible }`를 반환하고(업라이트 회전·스무스 노멀·중심정렬·영역 메시 배열·색 재질 포함), `_disposeSlot(slot)`이 정리한다 — 둘 다 재사용한다. 각 mesh는 `userData.region`(영역 메타)·`userData.color`·`userData.rel`(빅뱅 방향)을 갖는다.

**Files:**
- Modify: `mri2mesh/web/static/viewer.js`

**Interfaces:**
- Consumes: `_buildSlot`/`_disposeSlot`/`_makeMaterial`(기존, viewer.js 내부).
- Produces (Viewer 새 공개 메서드):
  - `enterCompareMode()` / `enterSingleMode()` — 모드 전환.
  - `async setPane(side, jobId, variantId)` — `side`는 `'L'`|`'R'`; jobId/variantId가 `null`이면 그 창 비움.
  - `setNormalize(on)` — 두 창 공통 크기(on) / world-mm 원본(off).
  - (기존 `setTransparent`/`setBigbang`/영역 토글이 compare 창에도 적용됨)

- [ ] **Step 1: constructor에 compare 상태 추가**

기존 `this.slots = [];`·`this.groupChecked`/`this.sideChecked` 초기화 근처에 추가:

```javascript
    // ---- compare(scissor) 모드 ----
    this.mode = 'single';                 // 'single' | 'compare'
    this.normalize = false;
    this._groupsRendered = false;         // 영역 토글 UI 1회 생성 여부
    this.panes = {
      L: { scene: null, camera: null, brain: null, box: null },
      R: { scene: null, camera: null, brain: null, box: null },
    };
```

- [ ] **Step 2: `_allBrains()` 헬퍼 + 기존 표기 컨트롤 일반화**

`_allBrains()`를 추가하고, 기존 `setTransparent`·`setBigbang`·`_updateVisibility`의 `this.slots` 순회를 `_allBrains()`로 바꾼다.

```javascript
  // 현재 모드의 brain 슬롯들 — 표기 컨트롤(반투명·빅뱅·영역)이 공통으로 순회한다.
  _allBrains() {
    if (this.mode === 'compare') return ['L', 'R'].map(s => this.panes[s].brain).filter(Boolean);
    return this.slots;
  }
```

`setTransparent`:
```javascript
  setTransparent(on) {
    this.transparent = on;
    for (const s of this._allBrains()) {
      for (const m of s.meshes) m.material = this._makeMaterial(m.userData.color);
    }
  }
```

`setBigbang`:
```javascript
  setBigbang(t) {
    this.bigbang = t;
    for (const s of this._allBrains()) {
      for (const m of s.meshes) m.position.copy(m.userData.rel).multiplyScalar(t * EXPLODE_K);
    }
  }
```

`_updateVisibility`(group/side 토글 반영)의 `for (const s of this.slots)`를 `for (const s of this._allBrains())`로 바꾼다.

- [ ] **Step 3: 모드 전환 메서드**

```javascript
  enterCompareMode() {
    this.clear();                         // single 슬롯·영역 토글 정리
    this._groupsRendered = false;
    this.mode = 'compare';
    for (const side of ['L', 'R']) {
      const p = this.panes[side];
      if (!p.scene) {
        p.scene = new THREE.Scene();
        p.scene.add(new THREE.AmbientLight(0xffffff, 0.6));
        const d1 = new THREE.DirectionalLight(0xffffff, 1.2); d1.position.set(2, 3, 4); p.scene.add(d1);
        const d2 = new THREE.DirectionalLight(0x7799cc, 0.5); d2.position.set(-3, -1, -2); p.scene.add(d2);
        p.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 5000);
        p.camera.up.set(0, 1, 0);
      }
    }
    this.controls.object = this.panes.L.camera;   // 동기 회전 기준 = 좌 카메라
    this.controls.update();
  }

  enterSingleMode() {
    for (const side of ['L', 'R']) {
      const p = this.panes[side];
      if (p.brain) { p.scene.remove(p.brain.root); this._disposeSlot(p.brain); p.brain = null; p.box = null; }
    }
    this.controls.object = this.camera;
    this.mode = 'single';
    const w = this.renderer.domElement.width / devicePixelRatio;
    const h = this.renderer.domElement.height / devicePixelRatio;
    this.renderer.setViewport(0, 0, w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }
```

- [ ] **Step 4: setPane + 정규화 + 프레이밍**

```javascript
  async setPane(side, jobId, variantId) {
    const p = this.panes[side];
    if (p.brain) { p.scene.remove(p.brain.root); this._disposeSlot(p.brain); p.brain = null; p.box = null; }
    if (jobId && variantId) {
      const slot = await this._buildSlot(jobId, variantId);   // 기존 로더 재사용
      if (this.mode !== 'compare') { this._disposeSlot(slot); return; }  // 그새 모드 바뀜
      p.brain = slot;
      p.box = new THREE.Box3().setFromObject(slot.root);       // 원본 크기(정규화 기준)
      p.scene.add(slot.root);
      // 새 창에도 현재 표기 상태 반영
      for (const m of slot.meshes) m.material = this._makeMaterial(m.userData.color);
      this.setBigbang(this.bigbang);
      if (!this._groupsRendered) { this._renderGroups(); this._groupsRendered = true; }
      this._updateVisibility();
    }
    this._applyNormalize();
    this._frameCompare();
  }

  setNormalize(on) { this.normalize = on; this._applyNormalize(); this._frameCompare(); }

  // 정규화 ON: 각 창 brain을 공통 크기로(bbox 최대변 → TARGET). OFF: 원본(scale 1).
  _applyNormalize() {
    const TARGET = 100;
    for (const side of ['L', 'R']) {
      const p = this.panes[side];
      if (!p.brain || !p.box) continue;
      const size = p.box.getSize(new THREE.Vector3());
      const maxdim = Math.max(size.x, size.y, size.z) || 1;
      p.brain.root.scale.setScalar(this.normalize ? TARGET / maxdim : 1);
    }
  }

  // 두 창을 각자 원점 중심 정렬하고 같은 카메라 거리로 프레임(둘 다 화면에 꽉 차게).
  _frameCompare() {
    let radius = 1;
    for (const side of ['L', 'R']) {
      const p = this.panes[side];
      if (!p.brain) continue;
      p.brain.root.position.set(0, 0, 0);
      p.brain.root.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(p.brain.root);
      const size = box.getSize(new THREE.Vector3());
      const c = box.getCenter(new THREE.Vector3());
      p.brain.root.position.sub(c);
      radius = Math.max(radius, size.x, size.y, size.z);
    }
    const dist = radius * 2.5, el = Math.PI / 9;
    for (const side of ['L', 'R']) {
      const cam = this.panes[side].camera;
      if (!cam) continue;
      cam.position.set(0, dist * Math.sin(el), -dist * Math.cos(el));
      cam.near = radius / 100; cam.far = radius * 20;
      cam.updateProjectionMatrix();
      cam.lookAt(0, 0, 0);
    }
    this.controls.target.set(0, 0, 0);
    this.controls.update();
  }
```

주의: `_renderGroups`는 대표 meta를 `this.slots[0]`에서 읽는다(기존). compare 모드에서도 동작하도록 `_renderGroups` 안의 `const regions = this.slots[0].meta.regions;`를 다음으로 바꾼다:

```javascript
    const brains = this._allBrains();
    if (!brains.length) return;
    const regions = brains[0].meta.regions;
```
(기존 `if (!this.slots.length) return;` 줄을 위 두 줄로 대체.)

- [ ] **Step 5: animate 루프에 scissor 렌더 + 동기 회전**

기존 `_animate`의 `this.controls.update(); this.renderer.render(this.scene, this.camera);`를 교체:

```javascript
  _animate() {
    requestAnimationFrame(this._animate);
    this.controls.update();
    if (this.mode === 'compare') this._renderCompare();
    else this.renderer.render(this.scene, this.camera);
  }

  _renderCompare() {
    const L = this.panes.L.camera, R = this.panes.R.camera;
    R.position.copy(L.position); R.quaternion.copy(L.quaternion); R.zoom = L.zoom;
    R.updateProjectionMatrix();
    const w = this.renderer.domElement.width / devicePixelRatio;
    const h = this.renderer.domElement.height / devicePixelRatio;
    const halfW = w / 2;
    this.renderer.setScissorTest(true);
    const draw = (side, x) => {
      const p = this.panes[side];
      p.camera.aspect = halfW / h; p.camera.updateProjectionMatrix();
      this.renderer.setViewport(x, 0, halfW, h);
      this.renderer.setScissor(x, 0, halfW, h);
      this.renderer.render(p.scene, p.camera);
    };
    draw('L', 0); draw('R', halfW);
    this.renderer.setScissorTest(false);
  }
```

- [ ] **Step 6: Syntax check + regression**

Run: `node --check mri2mesh/web/static/viewer.js`
Expected: 통과.

Run: `uv run pytest -q`
Expected: 기존 그대로 통과(백엔드·index.html 앵커 무변경).

- [ ] **Step 7: Commit**

```bash
git add mri2mesh/web/static/viewer.js
git commit -m "feat(web): 뷰어 compare(scissor) 모드 — 2창 동기회전·정규화 + 표기 컨트롤 일반화"
```

---

### Task 2: app.js + index.html — 모드 토글·드롭다운·정규화 배선

사이드바 "에셋 비교" 토글로 compare 모드 진입. compare 모드에선 vpanel의 생성 UI(`#mesh-options`)·변형 바(`#variant-bar`)·범례를 숨기고 표기 컨트롤(반투명·빅뱅·영역)만 보인다. 두 창 드롭다운(잡→변형)과 정규화 토글을 뷰어에 배선한다.

**Files:**
- Modify: `mri2mesh/web/static/index.html`
- Modify: `mri2mesh/web/static/app.js`

**Interfaces:**
- Consumes: `viewer.enterCompareMode/enterSingleMode/setPane/setNormalize`(Task 1), `api.listJobs`, `api.getStatus`, 기존 `esc`·`selectedJob`·`renderStage`·`showStage`.

- [ ] **Step 1: index.html — 모드 토글 + compare 바 + 스타일**

`#sidebar header`의 `#new-job` 다음에 토글 버튼:
```html
      <button id="mode-compare" class="new-btn" style="background:#374151">에셋 비교</button>
```

`#main` 안, `#vpanel` 앞에 compare 바:
```html
  <div id="compare-bar" style="display:none">
    <div class="cmp-pane" data-side="L">
      <select class="cmp-job"></select><select class="cmp-variant"></select>
    </div>
    <label id="normalize-wrap"><input type="checkbox" id="normalize"> 크기 정규화</label>
    <div class="cmp-pane" data-side="R">
      <select class="cmp-job"></select><select class="cmp-variant"></select>
    </div>
  </div>
```

`<style>`에 추가:
```css
  #compare-bar { position: fixed; top: 12px; left: 300px; right: 12px; z-index: 6;
                 display: flex; gap: 12px; align-items: flex-start; justify-content: space-between;
                 pointer-events: auto; }
  .cmp-pane { display: flex; gap: 6px; background: rgba(0,0,0,.55); padding: 8px; border-radius: 8px; }
  .cmp-pane select { background: #111; color: #eee; border: 1px solid #333; border-radius: 5px;
                     font-size: 12px; max-width: 160px; }
  #normalize-wrap { background: rgba(0,0,0,.55); padding: 8px 12px; border-radius: 8px;
                    font-size: 12px; pointer-events: auto; align-self: center; }
  .new-btn.active-mode { background: #2d6cdf !important; }
```

- [ ] **Step 2: app.js — 정규화 배선 (부트 근처)**

부트부(예: 마지막 `refreshJobs();` 앞)에:
```javascript
document.getElementById('normalize').onchange = (e) => viewer.setNormalize(e.target.checked);
```

- [ ] **Step 3: app.js — 모드 토글 + compare 드롭다운**

```javascript
let compareMode = false;
document.getElementById('mode-compare').onclick = async () => {
  compareMode = !compareMode;
  document.getElementById('mode-compare').classList.toggle('active-mode', compareMode);
  document.getElementById('compare-bar').style.display = compareMode ? 'flex' : 'none';
  document.getElementById('variant-bar').style.display = 'none';
  // compare: 생성 UI·범례 숨기고 표기 컨트롤만. vpanel은 열어 둔다.
  const genUi = document.getElementById('mesh-options');
  if (genUi) genUi.style.display = compareMode ? 'none' : '';
  const legend = document.getElementById('variant-legend');
  if (legend) legend.style.display = compareMode ? 'none' : '';
  if (compareMode) {
    document.getElementById('vpanel').style.display = 'block';
    viewer.enterCompareMode();
    await fillCompareJobs();
  } else {
    viewer.enterSingleMode();
    if (selectedJob) renderStage(); else showStage('empty');
  }
};

async function fillCompareJobs() {
  let rows = [];
  try { rows = await api.listJobs(); } catch (err) { console.error('[compareJobs]', err); }
  const done = rows.filter(r => r.state === 'done');
  for (const pane of document.querySelectorAll('.cmp-pane')) {
    const jobSel = pane.querySelector('.cmp-job');
    const varSel = pane.querySelector('.cmp-variant');
    const side = pane.dataset.side;
    jobSel.innerHTML = '<option value="">잡 선택</option>' +
      done.map(r => `<option value="${esc(r.jobId)}">${esc(r.name)}</option>`).join('');
    varSel.innerHTML = '';
    jobSel.onchange = async () => {
      varSel.innerHTML = '';
      if (!jobSel.value) { viewer.setPane(side, null, null); return; }
      let s; try { s = await api.getStatus(jobSel.value); } catch { return; }
      varSel.innerHTML = (s.variants || []).map((v, i) =>
        `<option value="${esc(v.variantId)}">${i + 1}. ${esc(v.variantId)}</option>`).join('');
      if (varSel.value) viewer.setPane(side, jobSel.value, varSel.value);
    };
    varSel.onchange = () => {
      if (jobSel.value && varSel.value) viewer.setPane(side, jobSel.value, varSel.value);
    };
  }
}
```

- [ ] **Step 4: Syntax check**

Run: `node --check mri2mesh/web/static/app.js`
Expected: 통과.

- [ ] **Step 5: Regression + 수동 확인**

Run: `uv run pytest -q`
Expected: 기존 통과.

수동(도커 `--build` 후 브라우저):
1. "에셋 비교" 클릭 → 캔버스 2열 분할, 상단 좌우 드롭다운·가운데 정규화 토글, vpanel에 생성 폼·범례 사라지고 반투명·빅뱅·영역만.
2. 좌/우 각각 잡→변형 선택 → 그 창에 뇌. 드래그하면 두 창 동시 회전.
3. 정규화 토글 → 크기 다른 두 뇌가 같은 크기로(끄면 실제 크기차).
4. 반투명·빅뱅·영역 토글이 두 창 동시 적용.
5. "에셋 비교" 다시 클릭 → 단일 모드 복귀, 캔버스 전체 뷰포트·기존 잡 뷰 정상.

- [ ] **Step 6: Commit**

```bash
git add mri2mesh/web/static/index.html mri2mesh/web/static/app.js
git commit -m "feat(web): 에셋 비교 모드 토글·드롭다운·정규화 배선"
```
