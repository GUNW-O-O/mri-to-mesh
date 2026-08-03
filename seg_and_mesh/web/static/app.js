import * as api from './api.js';
import { Viewer } from './viewer.js';

const viewer = new Viewer(document.getElementById('canvas'));
let selectedJob = null;
let pollTimer = null;

// ---------- 사이드바 ----------
async function refreshJobs() {
  const rows = await api.listJobs();
  const el = document.getElementById('joblist');
  el.innerHTML = '';
  for (const r of rows) {
    const div = document.createElement('div');
    div.className = 'job' + (r.jobId === selectedJob ? ' active' : '');
    div.onclick = () => selectJob(r.jobId);
    div.innerHTML =
      `<div class="name">${r.name}</div>` +
      `<div class="row"><span class="chip ${chipClass(r)}">${chipText(r)}</span></div>`;
    el.append(div);
  }
  // 진행 중인 잡이 있으면 목록도 계속 갱신
  if (rows.some(r => r.state === 'running')) scheduleList();
}
function chipClass(r){ return r.state==='done'?'done':r.state==='error'?'err':r.state==='awaiting_series'?'await':'run'; }
function chipText(r){ return r.state==='done'?'완료':r.state==='error'?'실패':r.state==='awaiting_series'?'시리즈 선택':`${r.step} 중`; }

let listTimer = null;
function scheduleList(){ clearTimeout(listTimer); listTimer = setTimeout(refreshJobs, 2000); }

// ---------- 잡 선택 + 폴링 ----------
async function selectJob(jobId) {
  selectedJob = jobId;
  clearTimeout(pollTimer);
  await refreshJobs();
  await renderStage();
}

async function renderStage() {
  const s = await api.getStatus(selectedJob);
  showStage(s.state);
  if (s.state === 'awaiting_series') renderSelect(s);
  else if (s.state === 'running') { renderProgress(s); poll(); }
  else if (s.state === 'error') renderError(s);
  else if (s.state === 'done') showViewer(s);
}
function poll(){ clearTimeout(pollTimer); pollTimer = setTimeout(renderStage, 1500); }

function showStage(state) {
  const map = { awaiting_series:'stage-select', running:'stage-progress', error:'stage-error' };
  for (const id of ['stage-empty','stage-select','stage-progress','stage-error'])
    document.getElementById(id).style.display = 'none';
  document.getElementById('vpanel').style.display = state==='done' ? 'block' : 'none';
  const show = map[state];
  if (show) document.getElementById(show).style.display = 'block';
}

// ---------- 시리즈 선택 스테이지 ----------
function renderSelect(s) {
  const el = document.getElementById('stage-select');
  el.innerHTML = '<h2>세그할 시리즈 선택</h2><div class="sub">자동선택 안 함 — 하나 고르세요.</div>';
  s.series.forEach((c, i) => {
    const d = document.createElement('div');
    d.className = 'series' + (i===0?' sel':'');
    d.innerHTML =
      `<input type="radio" name="s" ${i===0?'checked':''}>` +
      `<div class="meta"><div class="title">${c.description ?? '(설명 없음)'}</div>` +
      `<div class="facts">${c.slices}슬 · ${(c.voxelSizeMm||[]).join('×')}mm · ${c.acquisitionType??''}</div>` +
      `<div class="reasons">${(c.reasons||[]).join(' · ')}</div></div>` +
      `<span class="rank">${i+1}순위 · ${c.score}</span>`;
    d.onclick = () => { el.querySelectorAll('.series').forEach(x=>x.classList.remove('sel'));
                        d.classList.add('sel'); d.querySelector('input').checked = true;
                        el.dataset.pick = i; };
    el.append(d);
  });
  el.dataset.pick = 0;
  const go = document.createElement('button');
  go.className = 'primary'; go.textContent = '세그 시작 →';
  go.onclick = async () => { await api.selectSeries(selectedJob, Number(el.dataset.pick));
                             await refreshJobs(); renderStage(); };
  el.append(go);
}

// ---------- 진행률 ----------
function renderProgress(s) {
  const order = ['io','segment','remap','mesh'];
  const cur = order.indexOf(s.step);
  const el = document.getElementById('stage-progress');
  el.innerHTML = `<h2>처리 중</h2><div class="steps">` +
    ['업로드·dcm2niix','세그멘테이션','라벨 리맵','메시 생성'].map((label, i) => {
      const cls = i<cur?'ok':i===cur?'now':'wait';
      const mark = i<cur?'✓':i===cur?'●':'○';
      return `<div class="step"><span class="dot ${cls}">${mark}</span> ${label}</div>`;
    }).join('') + `</div>`;
}

// ---------- 에러 ----------
function renderError(s) {
  const el = document.getElementById('stage-error');
  el.innerHTML = `<h2>실패 · ${s.step}</h2><div class="sub">${(s.error&&s.error.message)||''}</div>`;
}

// ---------- 뷰어 ----------
function showViewer(s) {
  const v = s.variants && s.variants[0];
  if (v) viewer.showVariant(selectedJob, v.variantId);
}

// ---------- 업로드 모달 ----------
const overlay = document.getElementById('overlay');
document.getElementById('new-job').onclick = () => overlay.classList.add('on');
document.getElementById('upload-cancel').onclick = () => overlay.classList.remove('on');
document.getElementById('drop').onclick = () => document.getElementById('file-input').click();
let picked = [];
document.getElementById('file-input').onchange = (e) => { picked = [...e.target.files]; };
setupDrop(document.getElementById('drop'), fs => { picked = fs; });
document.getElementById('upload-go').onclick = async () => {
  if (!picked.length) return;
  const name = document.getElementById('name').value.trim();
  const { jobId } = await api.upload(picked, name);
  overlay.classList.remove('on'); picked = []; document.getElementById('name').value = '';
  document.getElementById('file-input').value = '';
  await refreshJobs(); selectJob(jobId);
};

// 폴더 드롭: DataTransfer 항목을 재귀로 훑어 파일만 모은다.
function setupDrop(el, cb) {
  el.ondragover = (e) => { e.preventDefault(); };
  el.ondrop = async (e) => {
    e.preventDefault();
    const items = [...e.dataTransfer.items].map(i => i.webkitGetAsEntry?.()).filter(Boolean);
    const files = [];
    for (const entry of items) await walkEntry(entry, files);
    if (files.length) cb(files);
  };
}
function walkEntry(entry, out) {
  return new Promise((resolve) => {
    if (entry.isFile) entry.file(f => { out.push(f); resolve(); });
    else if (entry.isDirectory) {
      const rd = entry.createReader();
      rd.readEntries(async es => { for (const e of es) await walkEntry(e, out); resolve(); });
    } else resolve();
  });
}

// ---------- 부트 ----------
refreshJobs();
