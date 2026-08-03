import * as api from './api.js';
import { Viewer } from './viewer.js';

const viewer = new Viewer(document.getElementById('canvas'));
let selectedJob = null;
let pollTimer = null;

// innerHTML에 꽂히는 사용자·메타데이터 문자열은 전부 이걸 거친다. 우리가 직접
// 만든 숫자/고정 라벨은 대상 아님.
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// ---------- 사이드바 ----------
async function refreshJobs() {
  const el = document.getElementById('joblist');
  let rows;
  try {
    rows = await api.listJobs();
  } catch (err) {
    console.error('[refreshJobs]', err);
    // #joblist는 .panel 밖이라 '.sub' 스코프가 안 먹는다 — 인라인으로.
    el.innerHTML = '<div style="color:#888;font-size:12px;padding:8px">목록을 불러오지 못했습니다 — 재시도 중…</div>';
    scheduleList(); // 계속 재시도 — 죽은 스피너로 안 남기고
    return;
  }
  el.innerHTML = '';
  for (const r of rows) {
    const div = document.createElement('div');
    div.className = 'job' + (r.jobId === selectedJob ? ' active' : '');
    div.onclick = () => selectJob(r.jobId);
    div.innerHTML =
      `<div class="name">${esc(r.name)}</div>` +
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
  let s;
  try {
    s = await api.getStatus(selectedJob);
  } catch (err) {
    console.error('[renderStage]', err);
    const el = document.getElementById('stage-progress');
    // 진행 패널이 떠 있을 때만 알리고, 계속 재시도해서 스피너가 죽지 않게 한다.
    if (el && el.style.display !== 'none' && !el.querySelector('.poll-err')) {
      el.insertAdjacentHTML('beforeend', '<div class="sub poll-err">상태 조회 실패 — 재시도 중…</div>');
    }
    poll();
    return;
  }
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
      `<div class="meta"><div class="title">${c.description != null ? esc(c.description) : '(설명 없음)'}</div>` +
      `<div class="facts">${c.slices}슬 · ${(c.voxelSizeMm||[]).join('×')}mm · ${esc(c.acquisitionType)}</div>` +
      `<div class="reasons">${(c.reasons||[]).map(esc).join(' · ')}</div></div>` +
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
  el.innerHTML = `<h2>실패 · ${esc(s.step)}</h2><div class="sub">${esc((s.error&&s.error.message)||'')}</div>`;
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
  let jobId;
  try {
    ({ jobId } = await api.upload(picked, name));
  } catch (err) {
    console.error('[upload]', err);
    showModalError(err.message || '업로드 실패');
    return; // 모달은 열어둔 채로 — 재시도할 수 있게
  }
  overlay.classList.remove('on'); picked = []; document.getElementById('name').value = '';
  document.getElementById('file-input').value = '';
  await refreshJobs(); selectJob(jobId);
};
function showModalError(msg) {
  const modal = document.getElementById('modal');
  let el = modal.querySelector('.modal-err');
  if (!el) {
    el = document.createElement('div');
    el.className = 'modal-err';
    // '.sub'는 .panel 안에서만 먹는 스코프라 여기선 안 걸린다 — 인라인으로.
    el.style.cssText = 'color:#f87171;font-size:12px;margin-top:8px;';
    modal.insertBefore(el, modal.querySelector('.modal-actions'));
  }
  el.textContent = msg; // textContent라 이스케이프 불필요
}

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
      // Chromium은 한 번 호출에 ~100개까지만 준다 — 빈 배열이 올 때까지 반복 호출해야
      // 큰 DICOM 폴더(슬라이스 수백 장)를 안 흘린다.
      const readBatch = () => new Promise(res => rd.readEntries(res, () => res([])));
      (async () => {
        for (;;) {
          const es = await readBatch();
          if (!es.length) break;
          for (const e of es) await walkEntry(e, out);
        }
        resolve();
      })();
    } else resolve();
  });
}

// ---------- 부트 ----------
refreshJobs();
