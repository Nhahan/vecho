"use strict";

/* ================================================================ setup */

const TOKEN = document.querySelector('meta[name="vecho-token"]').content;
const LANG = (new URLSearchParams(location.search).get("lang") || navigator.language || "ko").toLowerCase();
const KO = LANG.startsWith("ko");
const LOCALE = KO ? "ko-KR" : undefined;

const T = KO ? {
  start: "녹음 시작", stop: "중지하고 정리하기", recording: "녹음 중", withRemote: "상대방 소리도 녹음",
  untitled: "제목 없음", untitledAt: (t) => `${t}의 대화`, titlePh: "제목을 입력하세요", systemAudio: "시스템 소리 (모든 앱)", search: "검색", importTip: "음성 파일 가져오기",
  dropTitle: "놓으면 바로 정리를 시작합니다", dropSub: "mp3 · m4a · wav · flac · ogg · webm",
  me: "나", remote: "상대방", today: "오늘", yesterday: "어제", week: "지난 7일", earlier: "이전",
  empty: "아직 녹음이 없습니다.", noMatch: "일치하는 녹음이 없습니다.",
  st_recording: "녹음 중", st_processing: "정리 중", st_error: "실패", st_recorded: "정리 전",
  st_transcribed: "요약 전", st_empty: "말소리 없음",
  ready: "모든 준비가 끝났습니다", notReady: (n) => `${n}개 항목 확인 필요`, checking: "준비 상태 확인 중",
  homeEyebrow: "로컬에서만 동작하는 대화 기록",
  homeTitle: "대화에만 집중하세요.\n정리는 vecho가 합니다.",
  homeLede: "녹음을 시작하면 내 목소리와 상대방 목소리를 따로 담고, 끝나면 이 컴퓨터 안에서 글로 옮겨 핵심 내용과 할 일을 정리합니다. 음성도, 대화 내용도 밖으로 나가지 않습니다.",
  homeHint: "Discord, Zoom, Meet, 전화 — 어떤 앱이든 됩니다.",
  readiness: "준비 상태", recheck: "다시 확인", tips: "알아두면 좋은 것",
  tip1t: "이어폰 사용", tip1: "스피커로 들어도 되지만, 이어폰을 쓰면 내 목소리와 상대방 소리가 가장 깨끗하게 나뉩니다.",
  tip2t: "파일로 요약", tip2: "이미 녹음한 파일을 창에 끌어다 놓으면 바로 정리합니다.",
  tip3t: "단축키", tip3: "녹음 시작·중지 · 검색 · 재생",
  chk_microphone: "마이크", chk_system_audio: "상대방 소리", chk_whisper: "음성 인식", chk_llm: "요약 모델",
  fixLabel: "해결 방법",
  summary: "요약", transcript: "대화", copy: "복사", export: "내보내기", more: "더 보기",
  resummarize: "요약 다시 만들기", redo: "처음부터 다시 정리", del: "삭제",
  delTitle: "이 녹음을 삭제할까요?", delBody: "녹음 파일, 대화 내용, 요약이 모두 삭제되며 되돌릴 수 없습니다.",
  cancel: "취소", confirmDel: "삭제",
  queued: "차례를 기다리는 중", transcribing: "음성을 글로 옮기는 중", summarizing: "핵심 내용을 정리하는 중",
  firstRun: "처음 한 번은 음성 인식 모델을 내려받느라 몇 분 걸릴 수 있습니다.",
  failed: "정리하지 못했습니다", retry: "다시 시도", kept: "녹음 파일은 그대로 보관되어 있습니다.",
  pending: "아직 정리하지 않은 녹음입니다.", processNow: "지금 정리하기",
  noSpeech: "인식된 말소리가 없습니다.", noSummary: "아직 요약이 없습니다.",
  nothingIn: (list) => `${list} — 없음`,
  copied: "요약을 복사했습니다", saved: "제목을 바꿨습니다", deleted: "삭제했습니다",
  uploading: "파일을 올리는 중…", imported: "가져왔습니다. 곧 정리를 시작합니다.",
  saving: "저장하는 중…", starting: "준비하는 중…", offline: "vecho와 연결이 끊겼습니다",
  leave: "녹음 중입니다. 창을 닫으면 여기까지 저장하고 끝냅니다.",
  seconds: "초", minutes: "분", hours: "시간", summarizedWith: (m) => `${m}로 요약`,
  is_silent_remote: "상대방 소리가 녹음되지 않았습니다",
  is_silent_remote_d: "녹음하는 동안 소리가 재생되고 있었는지 확인하세요. macOS에서는 시스템 설정 → 개인정보 보호 및 보안 → 화면 및 시스템 오디오 녹음에서 이 앱(또는 터미널)을 허용해야 합니다.",
  is_silent_me: "내 목소리가 녹음되지 않았습니다",
  is_silent_me_d: "마이크가 연결되어 있고 이 앱의 마이크 사용이 허용되어 있는지 확인하세요.",
  is_stopped: "녹음이 중간에 끊겼습니다", is_stopped_d: "끊기기 전까지의 녹음은 저장되어 있습니다.",
  is_dropped: "소리가 잠깐 끊긴 구간이 있습니다", is_dropped_d: "컴퓨터가 바빴을 수 있습니다.",
  is_too_short: "녹음이 너무 짧아 정리하지 않았습니다", is_too_short_d: "",
  is_routing: "상대방 소리 녹음 설정에 문제가 있습니다", is_routing_d: "",
} : {
  start: "New recording", stop: "Stop and summarize", recording: "Recording", withRemote: "Include the other side",
  untitled: "Untitled", untitledAt: (t) => `Conversation at ${t}`, titlePh: "Add a title", systemAudio: "System audio (all apps)", search: "Search", importTip: "Import an audio file",
  dropTitle: "Drop to summarize", dropSub: "mp3 · m4a · wav · flac · ogg · webm",
  me: "Me", remote: "Them", today: "Today", yesterday: "Yesterday", week: "Previous 7 days", earlier: "Earlier",
  empty: "No recordings yet.", noMatch: "No recordings match.",
  st_recording: "Recording", st_processing: "Working", st_error: "Failed", st_recorded: "Not processed",
  st_transcribed: "No summary", st_empty: "No speech",
  ready: "Everything is ready", notReady: (n) => `${n} item${n > 1 ? "s" : ""} need attention`, checking: "Checking readiness",
  homeEyebrow: "Conversation notes that never leave your computer",
  homeTitle: "Stay in the conversation.\nvecho takes the notes.",
  homeLede: "vecho records your voice and the other side on separate tracks, then transcribes and summarizes everything on this computer — key points, decisions and to-dos. Nothing is uploaded.",
  homeHint: "Works with Discord, Zoom, Meet, phone apps — anything.",
  readiness: "Readiness", recheck: "Check again", tips: "Good to know",
  tip1t: "Use headphones", tip1: "Speakers work too, but headphones keep your voice and theirs cleanly apart.",
  tip2t: "Summarize a file", tip2: "Drop an existing recording onto the window to summarize it.",
  tip3t: "Shortcuts", tip3: "record/stop · search · play",
  chk_microphone: "Microphone", chk_system_audio: "Other side's audio", chk_whisper: "Speech recognition", chk_llm: "Summary model",
  fixLabel: "Fix",
  summary: "Summary", transcript: "Transcript", copy: "Copy", export: "Export", more: "More",
  resummarize: "Summarize again", redo: "Reprocess from scratch", del: "Delete",
  delTitle: "Delete this recording?", delBody: "The audio, transcript and summary will be removed. This cannot be undone.",
  cancel: "Cancel", confirmDel: "Delete",
  queued: "Waiting in line", transcribing: "Transcribing", summarizing: "Writing the summary",
  firstRun: "The first run downloads the speech model and can take a few minutes.",
  failed: "Could not process this recording", retry: "Try again", kept: "The recording itself is safe.",
  pending: "This recording has not been processed yet.", processNow: "Process now",
  noSpeech: "No speech was recognized.", noSummary: "No summary yet.",
  nothingIn: (list) => `${list} — none`,
  copied: "Summary copied", saved: "Renamed", deleted: "Deleted",
  uploading: "Uploading…", imported: "Imported. Processing will start shortly.",
  saving: "Saving…", starting: "Getting ready…", offline: "Lost connection to vecho",
  leave: "A recording is running. Closing will save it up to now and stop.",
  seconds: "s", minutes: "min", hours: "h", summarizedWith: (m) => `Summarized with ${m}`,
  is_silent_remote: "The other side was not recorded",
  is_silent_remote_d: "Check that sound was playing while recording. On macOS, allow this app (or your terminal) under System Settings → Privacy & Security → Screen & System Audio Recording.",
  is_silent_me: "Your voice was not recorded",
  is_silent_me_d: "Check that a microphone is connected and that this app may use it.",
  is_stopped: "Recording stopped partway through", is_stopped_d: "Everything up to that point was saved.",
  is_dropped: "Audio briefly dropped out", is_dropped_d: "The computer may have been busy.",
  is_too_short: "Too short to process", is_too_short_d: "",
  is_routing: "Problem setting up the other side's audio", is_routing_d: "",
};

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (name) => `<svg><use href="#i-${name}"/></svg>`;
const saved = {
  get(key, fallback) { try { const v = localStorage.getItem("vecho:" + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem("vecho:" + key, JSON.stringify(value)); } catch { /* private mode */ } },
};

document.documentElement.lang = KO ? "ko" : "en";
document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = T[el.dataset.i18n]; });
$("search").placeholder = T.search;
$("liveTitle").placeholder = T.titlePh;
$("importBtn").title = T.importTip;
$("importBtn").setAttribute("aria-label", T.importTip);

/* ================================================================ api */

async function api(path, { method = "GET", body, headers = {} } = {}) {
  const init = { method, headers: { "X-Vecho-Token": TOKEN, ...headers } };
  if (body instanceof Blob) init.body = body;
  else if (body !== undefined) { init.body = JSON.stringify(body); init.headers["Content-Type"] = "application/json"; }
  let response;
  try { response = await fetch("/api/" + path, init); } catch { throw new Error(T.offline); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

let toastTimer = 0;
function toast(text, bad = false) {
  const el = $("toast");
  el.textContent = text; el.className = "toast" + (bad ? " bad" : ""); el.hidden = false;
  el.style.animation = "none"; void el.offsetWidth; el.style.animation = "";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 2400);
}

/* ================================================================ format */

function clock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
function spoken(sec) {
  if (sec == null) return "";
  sec = Math.round(sec);
  if (sec < 60) return `${sec}${T.seconds}`;
  const h = Math.floor(sec / 3600), m = Math.round((sec % 3600) / 60);
  return h ? `${h}${T.hours} ${m}${T.minutes}` : `${m}${T.minutes}`;
}
const dayOf = (iso) => new Date(iso).toLocaleDateString(LOCALE, { month: "long", day: "numeric", weekday: "long" });
const timeOf = (iso) => new Date(iso).toLocaleTimeString(LOCALE, { hour: "numeric", minute: "2-digit" });
function groupOf(iso) {
  const d = new Date(iso), now = new Date();
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
  if (days <= 0) return T.today;
  if (days === 1) return T.yesterday;
  if (days < 7) return T.week;
  return d.toLocaleDateString(LOCALE, { year: d.getFullYear() === now.getFullYear() ? undefined : "numeric", month: "long" });
}
const nameOf = (s) => s.title || T.untitledAt(timeOf(s.created_at));
const localDetail = (text) => String(text || "").replace(/^System audio \(all apps\)/, T.systemAudio);
const inline = (text) => esc(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

/* ================================================================ summary model */

const NONE = /^(none|없음|n\/a|해당 없음)\.?$/i;
const TASK = /^[-*]\s*(?:[-*]\s*)?\[([ xX])\]\s*/;

function parseSummary(md) {
  const sections = [];
  let current = null;
  for (const raw of md.split("\n")) {
    const line = raw.trim();
    const heading = line.match(/^#{1,4}\s+(.*)$/);
    if (heading) { current = { title: heading[1].trim(), lines: [] }; sections.push(current); continue; }
    if (!line) continue;
    if (!current) { current = { title: "", lines: [] }; sections.push(current); }
    current.lines.push(line);
  }
  return sections.filter((s) => s.lines.length);
}

function splitTask(text) {
  let task = text, owner = "", due = "";
  const dash = text.match(/^(.*?)\s+[—–]\s+(.*)$/) || text.match(/^(.*?)\s+-{1,2}\s+(.*)$/);
  if (dash) { task = dash[1]; owner = dash[2]; }
  const paren = owner.match(/^(.*?)\s*\(([^()]+)\)\s*$/);
  if (paren) { owner = paren[1]; due = paren[2]; }
  else { const tail = task.match(/^(.*?)\s*\(([^()]+)\)\s*$/); if (tail && !owner) { task = tail[1]; due = tail[2]; } }
  return { task: task.trim(), owner: owner.trim(), due: due.trim() };
}

function renderSummary(md, id) {
  const sections = parseSummary(md);
  if (!sections.length) return "";
  const checks = saved.get("checks:" + id, {});
  const nothing = [];
  let html = "";
  sections.forEach((section, index) => {
    const items = section.lines.map((l) => l.replace(/^[-*]\s+(?!\[)/, ""));
    if (items.every((l) => NONE.test(l.replace(/^[-*]\s*/, "")))) { if (section.title) nothing.push(section.title); return; }
    const isLead = index === 0 && section.lines.length <= 3 && !section.lines.some((l) => TASK.test(l));
    if (isLead) { html += `<p class="lead">${items.map(inline).join(" ")}</p>`; return; }
    let body;
    if (section.lines.every((l) => TASK.test(l))) {
      body = '<ul class="todo">' + section.lines.map((line, i) => {
        const key = `${index}:${i}`;
        const done = key in checks ? checks[key] : /\[[xX]\]/.test(line);
        const { task, owner, due } = splitTask(line.replace(TASK, ""));
        const meta = [owner && `<b>${esc(owner)}</b>`, due && esc(due)].filter(Boolean).join(" · ");
        return `<li class="${done ? "done" : ""}"><input type="checkbox" data-check="${key}" ${done ? "checked" : ""} aria-label="${esc(task)}">
          <span class="task">${inline(task)}</span><span class="meta">${meta}</span></li>`;
      }).join("") + "</ul>";
    } else if (section.lines.every((l) => /^[-*]\s+/.test(l))) {
      const tag = /결정|decision/i.test(section.title) ? "ol" : "ul";
      body = `<${tag}>` + items.map((l) => `<li>${inline(l)}</li>`).join("") + `</${tag}>`;
    } else {
      body = items.map((l) => `<p>${inline(l)}</p>`).join("");
    }
    html += `<section class="sec"><div class="sec-label">${esc(section.title)}</div><div>${body}</div></section>`;
  });
  if (nothing.length) html += `<p class="nothing">${esc(T.nothingIn(nothing.join(", ")))}</p>`;
  return html;
}

/* ================================================================ state */

let state = { recording: null, jobs: {} };
let sessions = [];
let selected = saved.get("selected", null);
let detail = null;
let tab = saved.get("tab", "summary");
let health = null;
let jobSignature = "";

/* ================================================================ list */

async function refreshList() {
  try { sessions = await api("sessions"); } catch { return; }
  renderList();
}

function statusHtml(status) {
  switch (status) {
    case "recording": return `<span class="state rec">${T.st_recording}</span>`;
    case "processing": return `<span class="state work"><span class="mini-spin"></span>${T.st_processing}</span>`;
    case "error": return `<span class="state warn">${T.st_error}</span>`;
    case "recorded": case "transcribed": case "empty": return `<span class="state">${T["st_" + status]}</span>`;
    default: return "";
  }
}

function renderList() {
  const q = $("search").value.trim().toLowerCase();
  const shown = sessions.filter((s) => !q || `${nameOf(s)} ${s.tldr}`.toLowerCase().includes(q));
  if (!sessions.length) { $("list").innerHTML = `<p class="list-empty">${esc(T.empty)}</p>`; return; }
  if (!shown.length) { $("list").innerHTML = `<p class="list-empty">${esc(T.noMatch)}</p>`; return; }
  let html = "", group = "";
  for (const s of shown) {
    const g = groupOf(s.created_at);
    if (g !== group) { html += `<div class="group">${esc(g)}</div>`; group = g; }
    const bits = [`<span class="num">${esc(timeOf(s.created_at))}</span>`];
    if (s.duration) bits.push(`<span class="num">${esc(spoken(s.duration))}</span>`);
    const status = statusHtml(s.status);
    html += `<button class="item ${s.id === selected ? "on" : ""}" data-id="${esc(s.id)}" type="button">
      <div class="t">${esc(nameOf(s))}</div>
      <div class="s">${bits.join("<span>·</span>")}${status ? "<span>·</span>" + status : ""}</div>
      ${s.tldr && s.status === "summarized" ? `<div class="p">${esc(s.tldr)}</div>` : ""}
    </button>`;
  }
  $("list").innerHTML = html;
}

$("list").addEventListener("click", (e) => { const item = e.target.closest(".item"); if (item) select(item.dataset.id); });
$("search").addEventListener("input", renderList);
$("homeLink").addEventListener("click", (e) => { e.preventDefault(); select(null); });

async function select(id, { keepScroll = false } = {}) {
  if (id !== selected) closeMenu();
  selected = id; saved.set("selected", id); renderList();
  if (!id) { detail = null; render(); return; }
  try { detail = await api("sessions/" + encodeURIComponent(id)); }
  catch { selected = null; saved.set("selected", null); detail = null; }
  render({ keepScroll, newSession: true });
}

/* ================================================================ recorder */

const waveHistory = {};
function drawWave(canvas, values, color) {
  const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w) return;
  if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const step = 3, bars = Math.floor(w / step);
  const recent = values.slice(-bars);
  const offset = bars - recent.length;
  for (let i = 0; i < bars; i++) {
    const v = i < offset ? 0 : recent[i - offset];
    const bh = Math.max(1.5, Math.min(h, Math.sqrt(v) * h * 1.6));
    ctx.fillStyle = i < offset ? cssVar("--rule") : color;
    ctx.globalAlpha = i < offset ? 1 : 0.35 + 0.65 * ((i - offset) / Math.max(1, recent.length));
    ctx.fillRect(i * step, (h - bh) / 2, 2, bh);
  }
  ctx.globalAlpha = 1;
}

function renderRecorder() {
  const rec = state.recording;
  $("idleView").hidden = !!rec;
  $("liveView").hidden = !rec;
  if (!rec) { for (const k in waveHistory) delete waveHistory[k]; return; }
  $("timer").textContent = clock(rec.elapsed);
  const titleInput = $("liveTitle");
  if (document.activeElement !== titleInput && !titleInput.dataset.dirty) {
    titleInput.value = rec.title;
  }
  titleInput.dataset.session = rec.session_id;
  const waves = $("waves");
  const roles = Object.keys(rec.levels);
  if (waves.dataset.roles !== roles.join()) {
    waves.dataset.roles = roles.join();
    waves.innerHTML = roles.map((r) => `<div class="wave"><span>${esc(rec.sources[r] ? rec.sources[r].label : r)}</span><canvas data-role="${esc(r)}"></canvas></div>`).join("");
  }
  for (const role of roles) {
    (waveHistory[role] ||= []).push(rec.levels[role]);
    if (waveHistory[role].length > 400) waveHistory[role].shift();
    drawWave(waves.querySelector(`canvas[data-role="${role}"]`), waveHistory[role], cssVar(role === "me" ? "--me" : "--them"));
  }
  const notes = [...(rec.notes || []), ...(rec.issues || []).map((i) => issueTitle(i))];
  $("liveNote").hidden = !notes.length;
  $("liveNote").textContent = notes.join(" · ");
}

function busy(button, text) {
  const label = button.querySelector(".rec-label"), original = label.textContent;
  button.disabled = true; label.textContent = text;
  return () => { button.disabled = false; label.textContent = original; };
}

async function startRecording() {
  if ($("startBtn").disabled || state.recording) return;
  const done = busy($("startBtn"), T.starting);
  try {
    state = await api("record/start", { method: "POST", body: { mic_only: !$("withRemote").checked } });
    $("liveTitle").value = ""; delete $("liveTitle").dataset.dirty;
    renderRecorder();
    await refreshList();
  } catch (e) { toast(e.message, true); }
  finally { done(); }
}

async function stopRecording() {
  if ($("stopBtn").disabled || !state.recording) return;
  await saveLiveTitle();
  const done = busy($("stopBtn"), T.saving);
  try {
    const result = await api("record/stop", { method: "POST" });
    state.recording = null; renderRecorder();
    await refreshList();
    await select(result.session_id);
  } catch (e) { toast(e.message, true); }
  finally { done(); }
}

async function saveLiveTitle() {
  const input = $("liveTitle");
  if (!input.dataset.dirty || !input.dataset.session) return;
  delete input.dataset.dirty;
  const title = input.value.trim();
  if (!title) return;
  try { await api("sessions/" + encodeURIComponent(input.dataset.session), { method: "PATCH", body: { title } }); refreshList(); }
  catch (e) { toast(e.message, true); }
}

$("startBtn").addEventListener("click", startRecording);
$("stopBtn").addEventListener("click", stopRecording);
$("liveTitle").addEventListener("input", () => { $("liveTitle").dataset.dirty = "1"; });
$("liveTitle").addEventListener("blur", saveLiveTitle);
$("liveTitle").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.blur(); });
$("withRemote").checked = saved.get("withRemote", true);
$("withRemote").addEventListener("change", () => saved.set("withRemote", $("withRemote").checked));
window.addEventListener("beforeunload", (e) => { if (state.recording) { e.preventDefault(); e.returnValue = T.leave; } });

/* ================================================================ issues */

function issueTitle(issue) {
  if (issue.code === "silent") return issue.role === "me" ? T.is_silent_me : T.is_silent_remote;
  return T["is_" + issue.code] || issue.hint;
}
function issueDetail(issue) {
  if (issue.code === "silent") return issue.role === "me" ? T.is_silent_me_d : T.is_silent_remote_d;
  if (issue.code === "stopped") return `${T.is_stopped_d} (${issue.hint})`;
  return T["is_" + issue.code + "_d"] || "";
}

/* ================================================================ views */

function render(opts = {}) {
  const main = $("main"), scroll = main.scrollTop;
  if (!detail) renderHome(); else renderSession(opts);
  if (opts.keepScroll) main.scrollTop = scroll;
  else if (opts.newSession) main.scrollTop = 0;
  updateTopbarRule();
}

function renderHome() {
  $("topbar").innerHTML = "";
  $("player").hidden = true;
  stopAudio();
  const checks = health === null
    ? `<li class="pending"><span class="spin"></span>${esc(T.checking)}…</li>`
    : health.map((c) => `<li class="${c.status === "ok" ? "ok" : "fail"}">
        <span class="glyph">${icon(c.status === "ok" ? "check" : "alert")}</span>
        <span class="name">${esc(T["chk_" + c.key] || c.key)}</span>
        <span class="detail">${esc(localDetail(c.detail))}</span>
        ${c.fix ? `<span class="fix">${esc(T.fixLabel)} · <code>${esc(c.fix)}</code></span>` : ""}
      </li>`).join("");
  $("view").className = "doc home";
  $("view").innerHTML = `
    <p class="eyebrow">${esc(T.homeEyebrow)}</p>
    <h1>${esc(T.homeTitle).replace("\n", "<br>")}</h1>
    <p class="lede">${esc(T.homeLede)}</p>
    <div class="cta">
      <button class="btn solid big" id="homeStart" type="button"><span class="rec-dot"></span>${esc(T.start)}<kbd>R</kbd></button>
      <span class="hint">${esc(T.homeHint)}</span>
    </div>
    <h2>${esc(T.readiness)}<button type="button" id="recheck">${esc(T.recheck)}</button></h2>
    <ul class="checks">${checks}</ul>
    <h2>${esc(T.tips)}</h2>
    <ul class="tips">
      <li><b>${esc(T.tip1t)}</b><span>${esc(T.tip1)}</span></li>
      <li><b>${esc(T.tip2t)}</b><span>${esc(T.tip2)}</span></li>
      <li><b>${esc(T.tip3t)}</b><span><kbd>R</kbd> <kbd>/</kbd> <kbd>Space</kbd> — ${esc(T.tip3)}</span></li>
    </ul>`;
  $("homeStart").hidden = !!state.recording;
  $("homeStart").addEventListener("click", startRecording);
  $("recheck").addEventListener("click", () => { health = null; renderHome(); checkHealth(); });
}

function progressHtml(job) {
  const pct = Math.round((job.progress || 0) * 100);
  const loose = job.stage === "queued" || (job.stage === "summarizing" && pct === 0);
  const showPct = job.stage === "transcribing";
  const skeleton = `<div class="skeleton"><i class="head w40"></i><i class="gap"></i><i></i><i class="w90"></i><i class="w60"></i><i class="gap"></i><i class="w25"></i><i class="w75"></i><i class="w60"></i></div>`;
  return `<div class="progress-row">
      <div class="progress-label"><span class="spin"></span>${esc(T[job.stage])}${showPct ? `<span class="pct">${pct}%</span>` : ""}</div>
      ${job.stage === "transcribing" && pct === 0 ? `<p class="progress-sub">${esc(T.firstRun)}</p>` : ""}
      <div class="bar ${loose ? "loose" : ""}"><i data-pct="${pct}"></i></div>
    </div>${tab === "summary" ? skeleton : ""}`;
}

function noticeHtml(kind, title, text, action) {
  return `<div class="notice ${kind}">${icon("alert")}<div><strong>${esc(title)}</strong>${text ? `<small>${esc(text)}</small>` : ""}</div>
    ${action ? `<button class="btn" type="button" data-act="${action[0]}">${esc(action[1])}</button>` : "<span></span>"}</div>`;
}

function renderSession(opts) {
  const d = detail;
  const job = d.job;
  const working = job && ["queued", "transcribing", "summarizing"].includes(job.stage);
  const recording = d.status === "recording";
  const locked = working || recording;

  const crumbs = [`<b>${esc(dayOf(d.created_at))}</b>`, esc(timeOf(d.created_at))];
  if (d.duration) crumbs.push(esc(spoken(d.duration)));
  $("topbar").innerHTML = `
    <div class="crumbs num">${crumbs.join(" · ")}</div>
    <button class="tb-btn" type="button" data-act="copy" ${d.summary ? "" : "disabled"}>${icon("copy")}${esc(T.copy)}</button>
    <button class="tb-btn" type="button" data-act="export" ${d.summary || d.segments.length ? "" : "disabled"}>${icon("export")}${esc(T.export)}</button>
    <button class="tb-btn icon" type="button" data-act="menu" aria-label="${esc(T.more)}" aria-haspopup="menu">${icon("more")}</button>`;

  const people = d.tracks.filter((r) => r === "me" || r === "remote")
    .map((r) => `<span class="who ${r}">${esc(d.labels[r] || T[r])}</span>`);
  const meta = [...people];
  if (d.llm_model && d.summary) meta.push(`<span class="sep"></span><span>${esc(T.summarizedWith(d.llm_model))}</span>`);

  let status = "";
  if (working) status = progressHtml(job);
  else if (job && job.stage === "error") status = noticeHtml("error", T.failed, `${job.error} ${T.kept}`, ["retry", T.retry]);
  else if (d.status === "recorded") status = noticeHtml("", T.pending, "", ["retry", T.processNow]);
  else if (d.status === "transcribed") status = noticeHtml("", T.noSummary, "", ["resummarize", T.processNow]);
  const issues = (d.issues || []).filter((i) => i.code !== "too_short" || d.status !== "summarized")
    .map((i) => noticeHtml("", issueTitle(i), issueDetail(i))).join("");

  let body = "";
  if (tab === "summary") {
    body = d.summary ? renderSummary(d.summary, d.id)
      : working ? "" : `<p class="empty-doc">${esc(d.status === "empty" ? T.noSpeech : T.noSummary)}</p>`;
  } else {
    body = d.segments.length
      ? `<div class="script">${d.segments.map((s, i) => `
          <div class="turn ${esc(s.role)}" data-i="${i}" data-start="${s.start}">
            <button class="stamp" type="button" data-seek="${s.start}">${icon("play")}${clock(s.start)}</button>
            <div>${s.label ? `<div class="speaker">${esc(s.label)}</div>` : ""}<div class="said">${esc(s.text)}</div></div>
          </div>`).join("")}</div>`
      : working ? "" : `<p class="empty-doc">${esc(T.noSpeech)}</p>`;
  }

  $("view").className = "doc";
  $("view").innerHTML = `
    <h1 class="title" id="title" contenteditable="plaintext-only" spellcheck="false" data-placeholder="${esc(nameOf(d))}">${esc(d.title)}</h1>
    <div class="people">${meta.join("")}</div>
    ${status}${issues}
    <nav class="tabs" role="tablist">
      <button class="tab ${tab === "summary" ? "on" : ""}" data-tab="summary" type="button" role="tab">${esc(T.summary)}</button>
      <button class="tab ${tab === "transcript" ? "on" : ""}" data-tab="transcript" type="button" role="tab">${esc(T.transcript)}${d.segments.length ? `<span class="count">${d.segments.length}</span>` : ""}</button>
    </nav>
    ${body}`;

  $("view").querySelectorAll(".bar i[data-pct]").forEach((el) => { el.style.width = el.dataset.pct + "%"; });
  bindTitle(d);
  menuLocked = locked;

  const hasAudio = d.tracks.length > 0 && !recording;
  $("player").hidden = !hasAudio;
  if (hasAudio) loadAudio(d, opts.newSession);
  highlightTurn();
}

function bindTitle(d) {
  const title = $("title");
  title.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); title.blur(); }
    if (e.key === "Escape") { title.textContent = d.title; title.blur(); }
  });
  title.addEventListener("blur", async () => {
    const value = title.textContent.replace(/\s+/g, " ").trim();
    if (!value || value === d.title) { title.textContent = d.title; return; }
    try {
      detail = await api("sessions/" + encodeURIComponent(d.id), { method: "PATCH", body: { title: value } });
      toast(T.saved); refreshList();
    } catch (e) { toast(e.message, true); title.textContent = d.title; }
  });
}

function updateTopbarRule() { $("topbar").classList.toggle("scrolled", $("main").scrollTop > 4); }
$("main").addEventListener("scroll", updateTopbarRule, { passive: true });

/* ================================================================ actions */

let menuLocked = false;

async function act(name) {
  const d = detail;
  if (!d) return;
  const id = encodeURIComponent(d.id);
  try {
    if (name === "copy") { await copyText(d.summary); toast(T.copied); }
    else if (name === "export") exportMarkdown(d);
    else if (name === "menu") openMenu();
    else if (name === "resummarize") { detail = await api(`sessions/${id}/process`, { method: "POST", body: { step: "summarize" } }); render({ keepScroll: true }); refreshList(); }
    else if (name === "retry") { detail = await api(`sessions/${id}/process`, { method: "POST", body: { step: "all" } }); render({ keepScroll: true }); refreshList(); }
    else if (name === "delete") {
      if (!(await confirmDialog(T.delTitle, T.delBody, T.confirmDel))) return;
      await api(`sessions/${id}`, { method: "DELETE" });
      stopAudio(); toast(T.deleted);
      await refreshList();
      select(sessions.length ? sessions[0].id : null);
    }
  } catch (e) { toast(e.message, true); }
}

document.addEventListener("click", (e) => {
  const actBtn = e.target.closest("[data-act]");
  if (actBtn && !actBtn.disabled) { e.stopPropagation(); act(actBtn.dataset.act); return; }
  const tabBtn = e.target.closest("[data-tab]");
  if (tabBtn) { tab = tabBtn.dataset.tab; saved.set("tab", tab); render({ keepScroll: true }); return; }
  const seek = e.target.closest("[data-seek]");
  if (seek) { const a = $("audio"); a.currentTime = parseFloat(seek.dataset.seek); a.play(); return; }
  if (!e.target.closest("#menu")) closeMenu();
});
$("view").addEventListener("change", (e) => {
  const box = e.target.closest("[data-check]");
  if (!box || !detail) return;
  const checks = saved.get("checks:" + detail.id, {});
  checks[box.dataset.check] = box.checked;
  saved.set("checks:" + detail.id, checks);
  box.closest("li").classList.toggle("done", box.checked);
});

function openMenu() {
  const menu = $("menu"), anchor = $("topbar").querySelector('[data-act="menu"]');
  if (!menu.hidden) { closeMenu(); return; }
  const canSummarize = detail && detail.segments.length > 0;
  menu.innerHTML = `
    <button type="button" data-menu="resummarize" ${menuLocked || !canSummarize ? "disabled" : ""}>${icon("text")}${esc(T.resummarize)}</button>
    <button type="button" data-menu="retry" ${menuLocked ? "disabled" : ""}>${icon("redo")}${esc(T.redo)}</button>
    <hr>
    <button type="button" data-menu="delete" class="danger" ${menuLocked ? "disabled" : ""}>${icon("trash")}${esc(T.del)}</button>
    ${detail && detail.whisper_model ? `<hr><div class="foot">${esc(detail.whisper_model)}${detail.language ? " · " + esc(detail.language) : ""}</div>` : ""}`;
  menu.hidden = false;
  const r = anchor.getBoundingClientRect();
  menu.style.top = `${r.bottom + 6}px`;
  menu.style.left = `${Math.max(8, r.right - menu.offsetWidth)}px`;
  const first = menu.querySelector("button:not(:disabled)");
  if (first) first.focus();
}
function closeMenu() { $("menu").hidden = true; }
$("menu").addEventListener("click", (e) => {
  const item = e.target.closest("[data-menu]");
  if (!item || item.disabled) return;
  closeMenu(); act(item.dataset.menu);
});
$("menu").addEventListener("keydown", (e) => {
  const items = [...$("menu").querySelectorAll("button:not(:disabled)")];
  const i = items.indexOf(document.activeElement);
  if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
  if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
});

function confirmDialog(title, body, ok) {
  const dialog = $("dialog");
  $("dialogTitle").textContent = title; $("dialogBody").textContent = body;
  $("dialogOk").textContent = ok; $("dialogCancel").textContent = T.cancel;
  dialog.returnValue = "";
  dialog.showModal();
  $("dialogCancel").focus();
  return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true }));
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return; } catch { /* fall back below */ }
  const area = document.createElement("textarea");
  area.value = text; document.body.appendChild(area); area.select(); document.execCommand("copy"); area.remove();
}

function exportMarkdown(d) {
  const lines = [`# ${nameOf(d)}`, "", `${dayOf(d.created_at)} ${timeOf(d.created_at)}${d.duration ? " · " + spoken(d.duration) : ""}`, ""];
  if (d.summary) lines.push(d.summary, "");
  if (d.segments.length) {
    lines.push(`## ${T.transcript}`, "");
    for (const s of d.segments) lines.push(`**${clock(s.start)} ${s.label || ""}** ${s.text}`, "");
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${nameOf(d).replace(/[\\/:*?"<>|]+/g, "_")}.md`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/* ================================================================ player */

const audio = $("audio");
const speeds = [1, 1.25, 1.5, 2];
let speedIndex = Math.max(0, speeds.indexOf(saved.get("speed", 1)));
$("speedBtn").textContent = `${speeds[speedIndex]}×`;

function loadAudio(d, fresh) {
  const src = `/api/sessions/${encodeURIComponent(d.id)}/audio?t=${encodeURIComponent(TOKEN)}`;
  if (audio.dataset.src !== src) {
    audio.pause(); audio.src = src; audio.dataset.src = src; audio.playbackRate = speeds[speedIndex];
  }
  drawTimeline();
  updatePlayer();
}
function stopAudio() { audio.pause(); audio.removeAttribute("src"); audio.dataset.src = ""; audio.load(); }
function totalTime() { return audio.duration && isFinite(audio.duration) ? audio.duration : (detail && detail.duration) || 0; }

function drawTimeline() {
  const canvas = $("timelineCanvas");
  const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !detail) return;
  canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const total = totalTime(), now = audio.currentTime || 0;
  const lanes = { me: h / 2 - 7, remote: h / 2 + 2, mixed: h / 2 - 2.5 };
  ctx.fillStyle = cssVar("--rule");
  ctx.fillRect(0, h / 2 - 0.5, w, 1);
  if (total) {
    for (const s of detail.segments) {
      const x = (s.start / total) * w, x2 = (s.end / total) * w;
      ctx.fillStyle = cssVar(s.role === "me" ? "--me" : s.role === "remote" ? "--them" : "--ink-3");
      ctx.globalAlpha = s.end <= now ? 1 : 0.38;
      ctx.beginPath();
      ctx.roundRect(x, lanes[s.role] ?? lanes.mixed, Math.max(2, x2 - x), 5, 2.5);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    const px = Math.min(w - 1, (now / total) * w);
    ctx.fillStyle = cssVar("--ink");
    ctx.fillRect(px - 0.75, 3, 1.5, h - 6);
  }
}

function updatePlayer() {
  const total = totalTime();
  $("timeNow").textContent = clock(audio.currentTime);
  $("timeTotal").textContent = clock(total);
  $("playUse").setAttribute("href", audio.paused ? "#i-play" : "#i-pause");
  $("playBtn").setAttribute("aria-label", audio.paused ? "play" : "pause");
  $("timeline").setAttribute("aria-valuemax", String(Math.round(total)));
  $("timeline").setAttribute("aria-valuenow", String(Math.round(audio.currentTime)));
  drawTimeline();
  highlightTurn();
}

function highlightTurn() {
  const turns = document.querySelectorAll(".turn[data-start]");
  if (!turns.length) return;
  let current = null;
  if (!audio.paused || audio.currentTime > 0) turns.forEach((t) => { if (parseFloat(t.dataset.start) <= audio.currentTime + 0.05) current = t; });
  turns.forEach((t) => t.classList.toggle("now", t === current && !audio.paused));
}

function seekTo(clientX) {
  const rect = $("timeline").getBoundingClientRect(), total = totalTime();
  if (!total) return;
  audio.currentTime = Math.min(total, Math.max(0, ((clientX - rect.left) / rect.width) * total));
  updatePlayer();
}
let scrubbing = false;
$("timeline").addEventListener("pointerdown", (e) => { scrubbing = true; $("timeline").setPointerCapture(e.pointerId); seekTo(e.clientX); });
$("timeline").addEventListener("pointermove", (e) => { if (scrubbing) seekTo(e.clientX); });
$("timeline").addEventListener("pointerup", () => { scrubbing = false; });
$("timeline").addEventListener("keydown", (e) => {
  if (e.key === "ArrowRight") { audio.currentTime = Math.min(totalTime(), audio.currentTime + 5); updatePlayer(); }
  if (e.key === "ArrowLeft") { audio.currentTime = Math.max(0, audio.currentTime - 5); updatePlayer(); }
});
["timeupdate", "play", "pause", "loadedmetadata", "seeked"].forEach((ev) => audio.addEventListener(ev, updatePlayer));
$("playBtn").addEventListener("click", () => { audio.paused ? audio.play() : audio.pause(); });
$("speedBtn").addEventListener("click", () => {
  speedIndex = (speedIndex + 1) % speeds.length;
  audio.playbackRate = speeds[speedIndex];
  $("speedBtn").textContent = `${speeds[speedIndex]}×`;
  saved.set("speed", speeds[speedIndex]);
});
window.addEventListener("resize", () => { drawTimeline(); });
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { drawTimeline(); });

/* ================================================================ import */

async function importFile(file) {
  if (!file) return;
  toast(T.uploading);
  try {
    const result = await api("import", { method: "POST", body: file, headers: { "X-Filename": encodeURIComponent(file.name), "Content-Type": "application/octet-stream" } });
    toast(T.imported);
    await refreshList(); select(result.session_id);
  } catch (e) { toast(e.message, true); }
}
$("importBtn").addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", () => { importFile($("fileInput").files[0]); $("fileInput").value = ""; });
let dragDepth = 0;
const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
window.addEventListener("dragenter", (e) => { if (hasFiles(e)) { dragDepth++; $("drop").hidden = false; } });
window.addEventListener("dragleave", (e) => { if (hasFiles(e) && --dragDepth <= 0) { dragDepth = 0; $("drop").hidden = true; } });
window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener("drop", (e) => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth = 0; $("drop").hidden = true; importFile(e.dataTransfer.files[0]); });

/* ================================================================ health */

async function checkHealth() {
  $("healthText").textContent = T.checking;
  $("healthDot").className = "status-dot";
  try { health = await api("doctor"); } catch { health = []; }
  const failing = health.filter((c) => c.status !== "ok").length;
  $("healthDot").className = "status-dot " + (health.length && !failing ? "ok" : "warn");
  $("healthText").textContent = health.length && !failing ? T.ready : T.notReady(failing || 1);
  if (!detail) renderHome();
}
$("healthBtn").addEventListener("click", () => { select(null); });

/* ================================================================ keyboard */

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeMenu();
  const typing = e.target.closest("input, textarea, [contenteditable]");
  if (typing || e.metaKey || e.ctrlKey || e.altKey || $("dialog").open) return;
  if (e.key === "/") { e.preventDefault(); $("search").focus(); }
  else if (e.key === "r" || e.key === "R") { e.preventDefault(); state.recording ? stopRecording() : startRecording(); }
  else if (e.key === " " && !$("player").hidden) { e.preventDefault(); $("playBtn").click(); }
});

/* ================================================================ polling */

let failures = 0;
async function poll() {
  try {
    state = await api("state");
    failures = 0;
    renderRecorder();
    if ($("homeStart")) $("homeStart").hidden = !!state.recording;
    const jobs = Object.values(state.jobs);
    const signature = JSON.stringify(jobs.map((j) => [j.session_id, j.stage, Math.round(j.progress * 25)]));
    if (signature !== jobSignature) {
      const finished = jobs.filter((j) => j.stage === "done" && !jobSignature.includes(`"${j.session_id}","done"`));
      jobSignature = signature;
      await refreshList();
      if (selected) {
        try { detail = await api("sessions/" + encodeURIComponent(selected)); render({ keepScroll: true }); } catch { /* deleted elsewhere */ }
      }
      if (finished.length && document.hidden && "Notification" in window && Notification.permission === "granted") {
        new Notification("vecho", { body: `${T.summary} ✓` });
      }
    }
  } catch {
    if (++failures === 3) toast(T.offline, true);
  }
  setTimeout(poll, state.recording ? 120 : 1200);
}

(async function init() {
  await refreshList();
  if (selected && !sessions.some((s) => s.id === selected)) selected = null;
  await select(selected);
  checkHealth();
  poll();
  if ("Notification" in window && Notification.permission === "default") {
    document.addEventListener("click", () => Notification.requestPermission().catch(() => {}), { once: true });
  }
})();
