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
  if (state !== 'done') viewer.clear();
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
  go.onclick = async () => {
    if (go.disabled) return;
    go.disabled = true; go.textContent = '시작 중…';
    try {
      await api.selectSeries(selectedJob, Number(el.dataset.pick));
      await refreshJobs(); renderStage();
    } catch (err) {
      console.error('[selectSeries]', err);
      go.disabled = false; go.textContent = '세그 시작 →';
      let msg = el.querySelector('.select-err');
      if (!msg) {
        msg = document.createElement('div');
        msg.className = 'sub select-err';
        msg.style.marginTop = '8px';
        el.append(msg);
      }
      msg.textContent = err.message || '시리즈 선택 실패';
    }
  };
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
  if (!v) return;
  const jobId = selectedJob;
  viewer.showVariant(jobId, v.variantId).catch(err => {
    console.error('[showVariant]', err);
    if (selectedJob !== jobId) return;
    const metrics = document.getElementById('metrics');
    if (metrics) metrics.textContent = '메시 로드 실패';
  });
}

// ---------- 업로드 모달 ----------
// 문서 전체에서 브라우저 기본 드롭 동작을 막는다. 이게 없으면 파일이 #drop 존을
// 조금이라도 벗어나 떨어질 때 브라우저가 그 파일로 네비게이트해(NIfTI가 그냥
// 열리거나 다운로드됨) 업로드가 안 된다. #drop 자체 핸들러는 그대로 처리한다.
addEventListener('dragover', (e) => e.preventDefault());
addEventListener('drop', (e) => e.preventDefault());

const overlay = document.getElementById('overlay');
document.getElementById('new-job').onclick = () => overlay.classList.add('on');
document.getElementById('upload-cancel').onclick = () => { overlay.classList.remove('on'); clearPicked(); };
const drop = document.getElementById('drop');
drop.onclick = () => document.getElementById('file-input').click();
drop.onkeydown = (e) => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); drop.click(); }
};
let picked = [];
const dropStatus = document.getElementById('drop-status');
const fileInput = document.getElementById('file-input');
fileInput.onchange = (e) => {
  const files = [...e.target.files];
  if (!files.length) return;
  try {
    const kind = classifySingleFile(files[0]);
    setPicked(files, kind);
  } catch (err) {
    clearPicked(); showModalError(err.message);
  }
};
setupDrop(drop, (files, kind) => setPicked(files, kind), err => {
  clearPicked(); showModalError(err.message || '입력을 읽지 못했습니다');
});
const uploadGo = document.getElementById('upload-go');
uploadGo.onclick = async () => {
  if (uploadGo.disabled) return;
  if (!picked.length) return;
  uploadGo.disabled = true; uploadGo.textContent = '업로드 중…';
  const name = document.getElementById('name').value.trim();
  let jobId;
  try {
    ({ jobId } = await api.upload(picked, name));
  } catch (err) {
    console.error('[upload]', err);
    showModalError(err.message || '업로드 실패');
    uploadGo.disabled = false; uploadGo.textContent = '업로드';
    return; // 모달은 열어둔 채로 — 재시도할 수 있게
  }
  overlay.classList.remove('on'); clearPicked(); document.getElementById('name').value = '';
  uploadGo.disabled = false; uploadGo.textContent = '업로드';
  await refreshJobs(); selectJob(jobId);
};
function classifySingleFile(file) {
  const name = file.name.toLowerCase();
  if (name.endsWith('.nii') || name.endsWith('.nii.gz')) return 'NIfTI';
  if (name.endsWith('.zip')) return 'ZIP';
  throw new Error('단일 파일은 .zip, .nii, .nii.gz만 지원합니다');
}
function setPicked(files, kind) {
  picked = files;
  dropStatus.textContent = `${kind} · ${files.length}개 파일`;
  document.getElementById('modal').querySelector('.modal-err')?.remove();
}
function clearPicked() {
  picked = [];
  fileInput.value = '';
  dropStatus.textContent = '선택된 입력 없음';
  document.getElementById('modal').querySelector('.modal-err')?.remove();
}
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
function setupDrop(el, cb, onError) {
  el.ondragover = (e) => { e.preventDefault(); };
  el.ondrop = async (e) => {
    e.preventDefault();
    const items = [...e.dataTransfer.items].map(i => i.webkitGetAsEntry?.()).filter(Boolean);
    try {
      if (!items.length) throw new Error('드롭한 항목을 읽지 못했습니다');
      const dirs = items.filter(entry => entry.isDirectory);
      if (dirs.length && (items.length !== 1 || dirs.length !== 1))
        throw new Error('폴더와 파일을 함께 드롭할 수 없습니다');

      const files = [];
      for (const entry of items) await walkEntry(entry, files);
      if (!files.length) throw new Error('폴더에 파일이 없습니다');

      if (dirs.length) cb(files, 'DICOM 폴더');
      else if (files.length === 1) cb(files, classifySingleFile(files[0]));
      else cb(files, 'DICOM 파일 묶음');
    } catch (err) {
      onError(err);
    }
  };
}
function walkEntry(entry, out) {
  return new Promise((resolve, reject) => {
    if (entry.isFile) entry.file(f => { out.push(f); resolve(); }, reject);
    else if (entry.isDirectory) {
      const rd = entry.createReader();
      // Chromium은 한 번 호출에 ~100개까지만 준다 — 빈 배열이 올 때까지 반복 호출해야
      // 큰 DICOM 폴더(슬라이스 수백 장)를 안 흘린다.
      const readBatch = () => new Promise((res, rej) => rd.readEntries(res, rej));
      (async () => {
        for (;;) {
          const es = await readBatch();
          if (!es.length) break;
          for (const e of es) await walkEntry(e, out);
        }
        resolve();
      })().catch(reject);
    } else resolve();
  });
}

// ---------- 부트 ----------
refreshJobs();
