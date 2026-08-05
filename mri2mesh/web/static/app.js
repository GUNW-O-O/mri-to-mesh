import * as api from './api.js';
import { Viewer } from './viewer.js';
import { createVariant, deleteJob, getDicomMeta } from './api.js';
import { buildOptionsForm } from './options.js';
import { loadPresets, presetNames } from './presets.js';

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
    // 폴더명은 jobId다(레이아웃). 이름을 따로 준 경우 jobId를 함께 보여줘야
    // jobs/<jobId> 폴더를 찾을 수 있다 — 이름 == jobId면 중복이라 생략.
    div.innerHTML =
      `<div class="name">${esc(r.name)}</div>` +
      (r.name !== r.jobId ? `<div class="jid" title="jobs/ 폴더명">${esc(r.jobId)}</div>` : '') +
      `<div class="row"><span class="chip ${chipClass(r)}">${chipText(r)}</span></div>`;
    const del = document.createElement('button');
    del.className = 'job-del'; del.textContent = '×'; del.title = '삭제';
    del.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm('이 작업과 산출물을 삭제할까요?')) return;
      try {
        await deleteJob(r.jobId);
        if (selectedJob === r.jobId) { selectedJob = null; showStage('empty'); }
        await refreshJobs();
      } catch (err) { console.error('[deleteJob]', err); }
    };
    const info = document.createElement('button');
    info.className = 'job-info'; info.textContent = 'ⓘ'; info.title = '메타 정보';
    info.onclick = async (e) => {
      e.stopPropagation();
      try { showDicomInfo(await getDicomMeta(r.jobId)); }
      catch (err) { console.error('[dicomMeta]', err); }
    };
    const actions = document.createElement('div');
    actions.className = 'job-actions';
    actions.append(info, del);
    div.append(actions);
    el.append(div);
  }
  // 진행 중인 잡이 있으면 목록도 계속 갱신
  if (rows.some(r => r.state === 'running')) scheduleList();
}
function chipClass(r){ return r.state==='done'?'done':r.state==='error'?'err':(r.state==='awaiting_series'||r.step==='queued')?'await':'run'; }
function chipText(r){
  if (r.state==='done') return '완료';
  if (r.state==='error') return '실패';
  if (r.state==='awaiting_series') return '시리즈 선택';
  if (r.step==='queued') return '대기 중';
  return `${r.step} 중`;
}

let listTimer = null;
function scheduleList(){ clearTimeout(listTimer); listTimer = setTimeout(refreshJobs, 2000); }

// ---------- 잡 선택 + 폴링 ----------
async function selectJob(jobId) {
  selectedJob = jobId;
  clearTimeout(pollTimer);
  // 옵션 큐·생성 상태는 잡별이다 — 다른 잡 고르면 비운다(전 잡 큐가 새 잡에
  // 적용되는 혼선 방지).
  variantQueue.length = 0; renderQueue();
  const genSt = document.getElementById('gen-status');
  if (genSt) genSt.textContent = '';
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
  const map = { empty:'stage-empty', awaiting_series:'stage-select', running:'stage-progress', error:'stage-error' };
  for (const id of ['stage-empty','stage-select','stage-progress','stage-error'])
    document.getElementById(id).style.display = 'none';
  if (state !== 'running') clearInterval(progressTimer);   // 진행 이탈 시 경과 틱 정지
  document.getElementById('vpanel').style.display = state==='done' ? 'block' : 'none';
  if (state !== 'done') {
    viewer.clear();
    document.getElementById('variant-bar').style.display = 'none';
  }
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

  // 메쉬 옵션도 여기서 함께 고른다 — 세그멘테이션은 옵션과 무관하게 항상 동일하게
  // 돌고, 옵션은 세그 결과에 의존하지 않는다. 안 건드리면 baseline(프로덕션 기준값).
  const optWrap = document.createElement('div');
  optWrap.style.cssText = 'margin-top:18px;border-top:1px solid #2a2a2a;padding-top:14px';
  optWrap.innerHTML = '<div class="opt-title">메쉬 생성 옵션 (기본값 = 프로덕션 기준)</div>';
  const form = buildOptionsForm();
  optWrap.append(form.root);
  el.append(optWrap);

  // orig defacing 토글 — 코드/UI 틀만. 구현 전까지 비활성(체크 불가). 활성화 시
  // deface=true로 전달되지만 파이프라인이 아직 미구현이라 명시 실패한다.
  const defWrap = document.createElement('label');
  defWrap.className = 'sub';
  defWrap.style.cssText = 'display:block;margin-top:12px';
  defWrap.innerHTML = '<input type="checkbox" id="opt-deface" disabled> orig 얼굴 마스킹(defacing) <span style="color:#666">— 구현 예정</span>';
  el.append(defWrap);

  const go = document.createElement('button');
  go.className = 'primary'; go.textContent = '세그 시작 →';
  go.style.marginTop = '16px';
  go.onclick = async () => {
    if (go.disabled) return;
    go.disabled = true; go.textContent = '시작 중…';
    try {
      const deface = document.getElementById('opt-deface')?.checked ?? false;
      await api.selectSeries(selectedJob, Number(el.dataset.pick), form.collect(), deface);
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
// 경과 초를 "M:SS"(1시간 넘으면 "H:MM:SS")로.
function fmtElapsed(sec) {
  sec = Math.max(0, Math.floor(sec));
  const s = sec % 60, m = Math.floor(sec / 60) % 60, h = Math.floor(sec / 3600);
  const pad = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

let progressTimer = null;   // 경과 시간 라이브 틱(폴링과 별개로 매초 갱신)

function renderProgress(s) {
  clearInterval(progressTimer);
  const order = ['io','segment','remap','mesh'];
  // "queued"는 세그 슬롯(세마포어) 대기 — 아직 세그 시작 전이다. 세그 단계를
  // 현재로 잡되, 실행(now)과 구분되는 대기(queued) 표시를 준다.
  const queued = s.step === 'queued';
  const cur = queued ? order.indexOf('segment') : order.indexOf(s.step);
  const el = document.getElementById('stage-progress');
  el.innerHTML = `<h2>처리 중</h2><div class="steps">` +
    ['업로드·dcm2niix','세그멘테이션','라벨 리맵','메시 생성'].map((label, i) => {
      let cls, mark, suffix = '';
      if (i < cur) { cls = 'ok'; mark = '✓'; }
      else if (i === cur && queued) {
        cls = 'queued'; mark = '⏳';
        suffix = ' <span style="color:#38bdf8">(세그 슬롯 대기 중)</span>';
      } else if (i === cur) { cls = 'now'; mark = '●'; }
      else { cls = 'wait'; mark = '○'; }
      // 현재 단계에는 그 단계 경과 시간을 붙인다(대기면 대기 시간, 라이브 갱신).
      const timeSpan = i===cur ? ' <span class="step-time" data-since="'+esc(s.updatedAt||'')+'"></span>' : '';
      return `<div class="step"><span class="dot ${cls}">${mark}</span> ${label}${timeSpan}${suffix}</div>`;
    }).join('') + `</div>` +
    `<div class="sub total-time" data-since="${esc(s.createdAt||'')}" style="margin-top:14px"></div>`;

  // 세그멘테이션은 수 분 걸리고 그 사이 상태 갱신이 없어 멈춘 듯 보인다 —
  // createdAt/updatedAt 기준으로 매초 경과를 다시 계산해 살아 있음을 보인다.
  const tick = () => {
    const now = Date.now();
    for (const node of el.querySelectorAll('[data-since]')) {
      const since = Date.parse(node.dataset.since);
      if (isNaN(since)) { node.textContent = ''; continue; }
      const sec = (now - since) / 1000;
      node.textContent = node.classList.contains('total-time')
        ? `총 경과 ${fmtElapsed(sec)}`
        : `· ${fmtElapsed(sec)}`;
    }
  };
  tick();
  progressTimer = setInterval(tick, 1000);
}

// ---------- 에러 ----------
function renderError(s) {
  const el = document.getElementById('stage-error');
  el.innerHTML = `<h2>실패 · ${esc(s.step)}</h2><div class="sub">${esc((s.error&&s.error.message)||'')}</div>`;
}

// ---------- 뷰어 (다중 변형 일렬 배치) ----------
async function showViewer(s) {
  const variants = s.variants || [];
  if (!variants.length) {
    // 세그는 됐는데 변형(메쉬)이 없는 잡 — 빈 캔버스로 헷갈리지 않게 안내.
    viewer.clear();
    document.getElementById('variant-bar').style.display = 'none';
    const m = document.getElementById('metrics');
    if (m) m.textContent = '메쉬(변형) 없음 — 아래 "변형 생성"으로 만드세요.';
    return;
  }
  const jobId = selectedJob;
  // status.variants에 params가 없는 잡(서버 변경 전 생성)은 디스크의 params.json에서
  // 채운다 — 범례가 해시(variantId) 대신 옵션 요약을 보이게.
  await Promise.all(variants.map(async (v) => {
    if (!v.params) v.params = await api.getVariantParams(jobId, v.variantId).catch(() => null);
  }));
  if (selectedJob !== jobId) return;    // 그새 다른 잡으로 넘어갔으면 버림
  const bb = document.getElementById('bigbang');
  if (bb) bb.value = 0;                 // 새 로드는 빅뱅 0에서 시작
  renderVariantBar(variants);
  viewer.loadVariants(jobId, variants.map(v => v.variantId)).catch(err => {
    console.error('[loadVariants]', err);
    if (selectedJob !== jobId) return;
    const metrics = document.getElementById('metrics');
    if (metrics) metrics.textContent = '메시 로드 실패';
  });
}

// 변형 파라미터 한 줄 요약 — 어떤 옵션으로 뽑았는지 바로 보이게.
function summaryOf(p) {
  if (!p) return '';
  const sm = p.smoothing || {};
  const smTxt = sm.method === 'none' ? 'none'
    : `${sm.method}${sm.iterations != null ? `×${sm.iterations}` : ''}`;
  const dec = p.decimation || {};
  const decTxt = dec.method === 'quadric' ? `quadric ${dec.targetRatio}` : 'none';
  return `전처리 ${p.preprocess?.method ?? '?'} · 추출 ${p.extractor?.name ?? '?'}`
       + ` · 스무딩 ${smTxt} · 감면 ${decTxt} · minVox ${p.minVoxel ?? '?'}`;
}

// 하단 변형 토글 바(1 2 3 … on/off) + vpanel 범례(번호 → 옵션 요약).
function renderVariantBar(variants) {
  const bar = document.getElementById('variant-bar');
  bar.innerHTML = '';
  bar.style.display = variants.length ? 'flex' : 'none';
  const legend = document.getElementById('variant-legend');
  if (legend) legend.innerHTML = '';
  variants.forEach((v, i) => {
    const summary = summaryOf(v.params);
    const b = document.createElement('button');
    b.className = 'vbtn on'; b.textContent = String(i + 1);
    b.title = `${v.variantId}\n${summary}`;
    b.onclick = () => {
      const on = !b.classList.contains('on');
      b.classList.toggle('on', on);
      viewer.setVisible(v.variantId, on);
    };
    bar.append(b);

    if (legend) {
      const row = document.createElement('div');
      row.className = 'leg-row';
      row.innerHTML = `<span class="leg-n">${i + 1}</span>` +
        `<span class="leg-s">${esc(summary || v.variantId)}</span>`;
      const del = document.createElement('button');
      del.className = 'leg-del'; del.textContent = '×'; del.title = '이 변형(메쉬) 삭제';
      del.onclick = async () => {
        if (!confirm(`변형 ${i + 1}을(를) 삭제할까요?`)) return;
        const jobId = selectedJob;
        try {
          await api.deleteVariant(jobId, v.variantId);
          if (selectedJob !== jobId) return;
          showViewer(await api.getStatus(jobId));   // 남은 변형으로 다시 그림
        } catch (err) { console.error('[deleteVariant]', err); }
      };
      row.append(del);
      legend.append(row);
    }
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

// ---------- 옵션 폼 → 변형 생성 (뷰어패널, done 이후 비교용) ----------
const vpanelForm = buildOptionsForm();
document.getElementById('vpanel-opts').append(vpanelForm.root);

// 변형 생성 시작 + 진행 폴링. statusEl에 "메쉬 생성 done/total" 라이브 표시.
// 완료 시 variantId 반환, 실패 시 throw. 생성은 백그라운드(토큰 폴링).
async function generateWithProgress(jobId, params, statusEl, prefix = '') {
  const { token } = await createVariant(jobId, params);
  for (;;) {
    await new Promise(r => setTimeout(r, 700));
    let p;
    try { p = await api.getGenProgress(jobId, token); }
    catch { continue; }   // 잠깐의 404 등은 재시도
    if (statusEl) statusEl.textContent = p.total ? `${prefix}메쉬 생성 ${p.done}/${p.total}` : `${prefix}생성 중…`;
    if (p.finished) {
      if (p.error) throw new Error(p.error);
      return p.variantId;
    }
  }
}

document.getElementById('gen-variant').onclick = async () => {
  const btn = document.getElementById('gen-variant');
  const st = document.getElementById('gen-status');
  if (btn.disabled || !selectedJob) return;
  btn.disabled = true; st.textContent = '생성 중…';
  const jobId = selectedJob;
  try {
    const variantId = await generateWithProgress(jobId, vpanelForm.collect(), st);
    await refreshJobs();
    if (selectedJob === jobId) showViewer(await api.getStatus(jobId));
    st.textContent = variantId;
  } catch (err) {
    st.textContent = err.message || '생성 실패';
  } finally {
    btn.disabled = false;
  }
};

// ---------- 옵션 큐 → 일괄 생성 ----------
// 여러 옵션 세트를 큐에 쌓아 한 번에 변형 생성(서버 POST /variants 순차 호출, dedup됨).
const variantQueue = [];
function renderQueue() {
  const el = document.getElementById('queue-list');
  el.innerHTML = '';
  const batchBtn = document.getElementById('gen-batch');
  batchBtn.style.display = variantQueue.length ? 'block' : 'none';
  batchBtn.textContent = `큐 일괄 생성 (${variantQueue.length})`;
  variantQueue.forEach((p, i) => {
    const chip = document.createElement('div');
    chip.className = 'queue-chip';
    chip.innerHTML = `<span>${esc(summaryOf(p))}</span>`;
    const x = document.createElement('button');
    x.textContent = '×'; x.title = '큐에서 제거';
    x.onclick = () => { variantQueue.splice(i, 1); renderQueue(); };
    chip.append(x);
    el.append(chip);
  });
}
document.getElementById('queue-add').onclick = () => {
  variantQueue.push(vpanelForm.collect());
  renderQueue();
};
document.getElementById('gen-batch').onclick = async () => {
  const btn = document.getElementById('gen-batch');
  const st = document.getElementById('gen-status');
  if (btn.disabled || !selectedJob || !variantQueue.length) return;
  const jobId = selectedJob;
  btn.disabled = true;
  const total = variantQueue.length;
  let ok = 0;
  for (let i = 0; i < variantQueue.length; i++) {
    try { await generateWithProgress(jobId, variantQueue[i], st, `일괄 ${i + 1}/${total} · `); ok++; }
    catch (err) { console.error('[batch]', err); }
  }
  variantQueue.length = 0; renderQueue();
  if (selectedJob === jobId) {
    await refreshJobs();
    showViewer(await api.getStatus(jobId));
  }
  st.textContent = `일괄 생성 완료 (${ok}/${total})`;
  btn.disabled = false;
};

// ---------- 뷰어 표시 컨트롤 (반투명·빅뱅) ----------
const transparentEl = document.getElementById('transparent');
if (transparentEl) transparentEl.onchange = (e) => viewer.setTransparent(e.target.checked);
const bigbangEl = document.getElementById('bigbang');
if (bigbangEl) bigbangEl.oninput = (e) => viewer.setBigbang(Number(e.target.value));

// ---------- DICOM 메타 info 패널 ----------
const dicomOverlay = document.getElementById('dicom-info-overlay');
dicomOverlay.onclick = (e) => { if (e.target === dicomOverlay) dicomOverlay.classList.remove('on'); };
function showDicomInfo(m) {
  const box = document.getElementById('dicom-info');
  const names = (m.originalFilenames || []).map(esc).join('\n') || '(없음)';
  let html = `<h3>DICOM 메타 · ${esc(m.source)}</h3>`;
  html += `<div class="sub">원본 파일명</div><div class="kv">${names}</div>`;
  if (m.source === 'nifti' || !m.before) {
    html += `<div class="sub" style="margin-top:10px">NIfTI 입력 — DICOM 메타 없음</div>`;
  } else {
    html += `<div class="sub" style="margin-top:10px">before (원본 헤더)</div>`;
    html += `<div class="kv">${esc(JSON.stringify(m.before, null, 1))}</div>`;
    html += `<div class="sub" style="margin-top:10px">removed (제거됨)</div>`;
    html += `<div class="kv removed">${(m.removed||[]).map(esc).join('\n')}</div>`;
  }
  html += `<div class="sub" style="margin-top:10px">after (NIfTI 기하)</div>`;
  html += `<div class="kv">${esc(JSON.stringify(m.after && m.after.nifti, null, 1))}</div>`;
  html += `<div class="sub" style="margin-top:10px">after (사이드카)</div>`;
  html += `<div class="kv">${esc(JSON.stringify(m.after && m.after.sidecar, null, 1))}</div>`;
  box.innerHTML = html;
  dicomOverlay.classList.add('on');
}

// ---------- 에셋 비교 모드 ----------
let compareMode = false;
document.getElementById('mode-compare').onclick = async () => {
  compareMode = !compareMode;
  document.getElementById('mode-compare').classList.toggle('active-mode', compareMode);
  document.getElementById('compare-bar').style.display = compareMode ? 'flex' : 'none';
  document.getElementById('scissor-divider').style.display = compareMode ? 'block' : 'none';
  document.getElementById('variant-bar').style.display = 'none';
  // compare: 생성 UI·범례 숨기고 표기 컨트롤만. vpanel은 열어 둔다.
  const genUi = document.getElementById('mesh-options');
  if (genUi) genUi.style.display = compareMode ? 'none' : '';
  const legend = document.getElementById('variant-legend');
  if (legend) legend.style.display = compareMode ? 'none' : '';
  if (compareMode) {
    // compare 진입: 스테이지 패널(빈 화면 안내·시리즈·진행·에러)을 전부 숨긴다.
    for (const id of ['stage-empty','stage-select','stage-progress','stage-error'])
      document.getElementById(id).style.display = 'none';
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
      let s; try { s = await api.getStatus(jobSel.value); } catch { viewer.setPane(side, null, null); return; }
      varSel.innerHTML = (s.variants || []).map((v, i) =>
        `<option value="${esc(v.variantId)}">${i + 1}. ${esc(v.variantId)}</option>`).join('');
      if (varSel.value) viewer.setPane(side, jobSel.value, varSel.value);
      else viewer.setPane(side, null, null);
    };
    varSel.onchange = () => {
      if (jobSel.value && varSel.value) viewer.setPane(side, jobSel.value, varSel.value);
    };
  }
}

document.getElementById('normalize').onchange = (e) => viewer.setNormalize(e.target.checked);

// ---------- 조건 프리셋 (노화/치매/알콜) ----------
// 정적 JSON에서 영역 세트를 읽어 셀렉트를 채우고, 선택 시 뷰어에 적용(두 모드 공통).
let presetMap = new Map();
loadPresets().then(m => {
  presetMap = m;
  const sel = document.getElementById('preset');
  for (const name of presetNames(m)) {
    const o = document.createElement('option'); o.value = name; o.textContent = name; sel.append(o);
  }
});
document.getElementById('preset').onchange = (e) => {
  viewer.setPreset(e.target.value ? presetMap.get(e.target.value) : null);
};

// ---------- 부트 ----------
refreshJobs();
