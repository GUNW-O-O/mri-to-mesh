// 조건 프리셋(노화/치매/알콜) 영역 세트 로더. 정적 JSON(단일 원천)을 fetch해
// 조건명 → 영역명 Set 맵으로 만든다. 재질 적용은 viewer가 한다(setPreset).

let _cache = null;   // Map<string, Set<string>>

export async function loadPresets() {
  if (_cache) return _cache;
  try {
    const res = await fetch('/static/condition-presets.json');
    if (!res.ok) throw new Error(`${res.status}`);
    const obj = await res.json();
    _cache = new Map(Object.entries(obj).map(([k, v]) => [k, new Set(v)]));
  } catch (err) {
    console.error('[loadPresets]', err);
    _cache = new Map();   // 프리셋 없이도 뷰어는 동작
  }
  return _cache;
}

export function presetNames(presets) {
  return [...presets.keys()];
}
