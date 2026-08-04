// 옵션 폼 → params(camelCase) 수집. baseline로 미리 채워져 있고(HTML value),
// 사용자가 바꾼 축만 반영된다. 관계없는 하위 수치는 서버가 baseline로 채운다.
export function collectParams() {
  const val = (id) => document.getElementById(id).value;
  const num = (id) => Number(val(id));
  return {
    preprocess: { method: val('opt-preprocess') },
    extractor:  { name: val('opt-extractor') },
    smoothing:  { method: val('opt-smoothing'), iterations: num('opt-iterations') },
    decimation: { method: val('opt-decimation'), targetRatio: num('opt-ratio') },
    minVoxel:   num('opt-minvoxel'),
  };
}
