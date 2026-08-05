// 엔드포인트 fetch 래퍼. 전부 same-origin 상대경로 — 외부 통신 0(전역 제약).
async function j(res) {
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export async function listJobs() {
  return j(await fetch('/api/jobs'));
}

export async function upload(files, name) {
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  if (name) fd.append('name', name);
  return j(await fetch('/api/jobs', { method: 'POST', body: fd }));
}

export async function getStatus(jobId) {
  return j(await fetch(`/api/jobs/${encodeURIComponent(jobId)}`));
}

export async function deleteJob(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
  return j(res);
}

export async function selectSeries(jobId, index, params = null, deface = false) {
  return j(await fetch(`/api/jobs/${encodeURIComponent(jobId)}/series`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ seriesIndex: index, params, deface }),
  }));
}

// 변형 생성을 시작하고 진행 폴링용 토큰을 받는다({token}). 실제 생성은 백그라운드.
export async function createVariant(jobId, params) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/variants`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  return j(res);   // {token}. 비정상 응답이면 throw
}

export async function getGenProgress(jobId, token) {
  const res = await fetch(
    `/api/jobs/${encodeURIComponent(jobId)}/variants-progress/${encodeURIComponent(token)}`);
  return j(res);   // {done,total,variantId,deduped,finished,error}
}

export async function deleteVariant(jobId, variantId) {
  const res = await fetch(
    `/api/jobs/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}`,
    { method: 'DELETE' });
  return j(res);
}

export function glbUrl(jobId, variantId) {
  return `/api/jobs/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}/regions.glb`;
}

export function metaUrl(jobId, variantId) {
  return `/api/jobs/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}/regions-meta.json`;
}

export async function getVariantParams(jobId, variantId) {
  const res = await fetch(
    `/api/jobs/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}/params.json`);
  if (!res.ok) return null;   // 없으면 요약 생략(범례가 variantId로 폴백)
  return res.json();
}

export async function getDicomMeta(jobId) {
  const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/dicom-meta`);
  return j(res);
}
