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

export async function selectSeries(jobId, index) {
  return j(await fetch(`/api/jobs/${encodeURIComponent(jobId)}/series`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ seriesIndex: index }),
  }));
}

export function glbUrl(jobId, variantId) {
  return `/api/jobs/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}/regions.glb`;
}

export function metaUrl(jobId, variantId) {
  return `/api/jobs/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}/regions-meta.json`;
}
