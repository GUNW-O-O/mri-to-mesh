// 메쉬 생성 옵션 폼 빌더. 시리즈 선택 화면과 done 뷰어 양쪽에서 같은 폼을 쓴다.
// 각 축에 설명(help)을 달아 무엇을·왜 고르는지 보이게 한다. baseline 기본값은
// brainds 프로덕션 기준값 — 안 건드리면 그대로 나온다.
//
// buildOptionsForm() -> { root: HTMLElement, collect: () => params }
//   collect()가 내는 params(camelCase, 중첩)는 서버 parse_mesh_params와 짝이다.
//   관계없는 하위 수치(smoothing.relaxation 등)는 서버가 baseline로 채운다.

const AXES = [
  {
    key: 'preprocess', label: '전처리 (preprocess)',
    help: '라벨 볼륨을 메쉬 추출 전에 다듬는다. none=원본 그대로(계단현상 가능), ' +
          'gaussian=블러로 매끈하나 얇은 구조가 뭉개짐, distance=거리변환 기반 부드러운 경계.',
    controls: [{ kind: 'select', name: 'method', opts: ['none', 'gaussian', 'distance'], def: 'none' }],
  },
  {
    key: 'extractor', label: '등가면 추출 (extractor)',
    help: '라벨 볼륨에서 표면을 뽑는 알고리즘. vtk_contour_perlabel=라벨별 컨투어(경계 깔끔, 기본), ' +
          'skimage_mc·pymcubes=마칭큐브, vtk_flyingedges=빠름, vtk_surfacenets=계단(각짐)을 줄임.',
    controls: [{ kind: 'select', name: 'name',
      opts: ['vtk_contour_perlabel', 'skimage_mc', 'pymcubes', 'vtk_flyingedges', 'vtk_surfacenets'],
      def: 'vtk_contour_perlabel' }],
  },
  {
    key: 'smoothing', label: '스무딩 (smoothing)',
    help: '표면 평탄화. laplacian=기본 평탄화(iter만큼 반복), none=원본 각짐, ' +
          'taubin=수축 없는 평탄화, humphrey=디테일 보존 평탄화. iter=반복수(클수록 매끈, 과하면 뭉개짐).',
    controls: [
      { kind: 'select', name: 'method', opts: ['laplacian', 'none', 'taubin', 'humphrey'], def: 'laplacian' },
      { kind: 'number', name: 'iterations', def: 30, min: 0, max: 100, step: 1, unit: 'iter' },
    ],
  },
  {
    key: 'decimation', label: '감면 (decimation)',
    help: '삼각형 수를 줄여 파일을 가볍게. none=정점 유지(고품질·무거움), ' +
          'quadric=목표 비율까지 감면(가벼움·디테일 손실). ratio=남길 비율(0.35=35%만 유지).',
    controls: [
      { kind: 'select', name: 'method', opts: ['none', 'quadric'], def: 'none' },
      { kind: 'number', name: 'targetRatio', def: 0.35, min: 0.05, max: 1, step: 0.05, unit: 'ratio' },
    ],
  },
  {
    key: 'minVoxel', label: '최소 복셀 (minVoxel)',
    help: '이 복셀 수 미만인 라벨 조각을 버려 노이즈를 없앤다. 클수록 작은 영역이 사라진다.',
    controls: [{ kind: 'number', name: '', def: 100, min: 0, max: 5000, step: 1 }],
  },
  {
    key: 'cleanup', label: '파편 정리 (cleanup)',
    help: '라벨을 연결요소로 쪼개, 지정 복셀 수 미만 조각을 버린다. 뇌실 주변에 흩어진 ' +
          '파편(스파이크의 원인)을 없앤다. none=끔. minComponentVox=이 값 미만 조각 제거 ' +
          '(WM-hypointensities 등 다초점 구조는 낮게 잡아야 실제 병변이 남는다).',
    controls: [
      { kind: 'select', name: 'method', opts: ['none', 'drop_small_components'], def: 'none' },
      { kind: 'number', name: 'minComponentVox', def: 30, min: 0, max: 5000, step: 1, unit: 'vox' },
    ],
  },
];

function makeControl(c) {
  let el;
  if (c.kind === 'select') {
    el = document.createElement('select');
    for (const o of c.opts) {
      const opt = document.createElement('option');
      opt.value = o; opt.textContent = o;
      if (o === c.def) opt.selected = true;
      el.append(opt);
    }
  } else {
    el = document.createElement('input');
    el.type = 'number'; el.value = c.def;
    if (c.min != null) el.min = c.min;
    if (c.max != null) el.max = c.max;
    if (c.step != null) el.step = c.step;
  }
  el.dataset.name = c.name;
  return el;
}

// 하나의 폼 인스턴스를 만든다. root와, 그 root 안에서만 값을 읽는 collect()를 준다.
// (전역 id를 안 쓰므로 여러 폼이 한 페이지에 공존해도 충돌하지 않는다.)
export function buildOptionsForm() {
  const root = document.createElement('div');
  root.className = 'mesh-opts';
  for (const ax of AXES) {
    const row = document.createElement('div');
    row.className = 'opt-axis';
    row.dataset.key = ax.key;

    const head = document.createElement('div');
    head.className = 'opt-head';
    const lab = document.createElement('span');
    lab.className = 'opt-label'; lab.textContent = ax.label;
    head.append(lab);
    for (const c of ax.controls) {
      if (c.unit) {
        const u = document.createElement('span');
        u.className = 'opt-unit'; u.textContent = c.unit;
        head.append(u);
      }
      head.append(makeControl(c));
    }
    const help = document.createElement('div');
    help.className = 'opt-help'; help.textContent = ax.help;

    row.append(head, help);
    root.append(row);
  }

  const collect = () => {
    const get = (key, name) =>
      root.querySelector(`.opt-axis[data-key="${key}"] [data-name="${name}"]`);
    const numOf = (key, name) => Number(get(key, name).value);
    const strOf = (key, name) => get(key, name).value;
    return {
      preprocess: { method: strOf('preprocess', 'method') },
      extractor:  { name: strOf('extractor', 'name') },
      smoothing:  { method: strOf('smoothing', 'method'), iterations: numOf('smoothing', 'iterations') },
      decimation: { method: strOf('decimation', 'method'), targetRatio: numOf('decimation', 'targetRatio') },
      cleanup:    { method: strOf('cleanup', 'method'), minComponentVox: numOf('cleanup', 'minComponentVox') },
      minVoxel:   numOf('minVoxel', ''),
    };
  };

  return { root, collect };
}
