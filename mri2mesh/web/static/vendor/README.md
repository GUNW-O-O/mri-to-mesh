# three.js (vendored)

three.js r0.160.0 — https://unpkg.com/three@0.160.0/

런타임에 CDN을 타지 않으려고 저장소에 넣었다(로컬 단독 툴, 환자 데이터).
`index.html`의 importmap이 `three` → `three.module.js`, `three/addons/` →
`addons/`로 매핑한다.

받은 파일:
- `three.module.js` (build)
- `addons/loaders/GLTFLoader.js`
- `addons/controls/OrbitControls.js`
- `addons/utils/BufferGeometryUtils.js` (GLTFLoader가 상대 import)

갱신하려면 계획 문서 Task 6 Step 0의 스크립트에서 버전만 바꿔 다시 받는다.
MIT License (three.js).
