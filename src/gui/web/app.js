"use strict";

// ---- helpers ----
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
let API = null;           // window.pywebview.api
let OPTIONS = null;       // из get_init
let pendingDownload = []; // модели, ожидающие подтверждения скачивания
let dlModalOpen = false;

function api() {
  if (API) return API;
  API = (window.pywebview && window.pywebview.api) || null;
  return API;
}

async function call(method, ...args) {
  const a = api();
  if (!a || typeof a[method] !== "function") return null;
  try {
    return await a[method](...args);
  } catch (e) {
    console.error("api." + method + " failed", e);
    return null;
  }
}

function fmtBytes(n) {
  if (!n) return "0 MB";
  return (n / (1024 * 1024)).toFixed(1) + " MB";
}
function fmtEta(sec) {
  if (!sec || sec <= 0) return "—";
  if (sec < 60) return Math.round(sec) + "с";
  if (sec < 3600) return Math.round(sec / 60) + "м";
  return (sec / 3600).toFixed(1) + "ч";
}

// ---- init ----
async function init() {
  const data = await call("get_init");
  if (!data) return;
  OPTIONS = data.options;
  buildLangs(OPTIONS.langs);
  buildModelSelectors();
  render(data.state);
  wireEvents();
  startPolling();
}

function buildLangs(langs) {
  const sel = $("#langSel");
  sel.innerHTML = "";
  (langs || []).forEach((l) => {
    const o = document.createElement("option");
    o.value = l.code;
    o.textContent = `${l.name} (${l.code})`;
    sel.appendChild(o);
  });
}

function buildModelSelectors() {
  // селекторы моделей открываются по кнопке "Сменить модель" — тут храним данные
  // (в этой версии выбор моделей делаем через тир; смена конкретной модели —
  //  через set_models, если понадобится расширять UI)
}

// ---- events ----
function wireEvents() {
  $$('[data-pick]').forEach((b) =>
    b.addEventListener("click", () => call("pick_folder", b.dataset.pick).then(render))
  );
  $("#inputPath").addEventListener("change", (e) => call("set_input", e.target.value).then(render));
  $("#outputPath").addEventListener("change", (e) => call("set_output", e.target.value).then(render));
  $("#dryRun").addEventListener("change", (e) => call("set_dry", e.target.checked).then(render));
  $("#langSel").addEventListener("change", (e) => call("set_lang", e.target.value).then(render));
  $("#tierSel").addEventListener("change", (e) => call("set_tier", e.target.value).then(render));
  $("#rescanBtn").addEventListener("click", () => call("rescan_hardware").then(render));
  $("#saveKey").addEventListener("click", () => call("set_key", $("#apiKey").value).then(render));

  $$(".mode").forEach((m) =>
    m.addEventListener("click", () => call("set_mode", m.dataset.mode).then(render))
  );

  $("#startBtn").addEventListener("click", onStart);
  $("#pauseBtn").addEventListener("click", () => call("pause").then(render));
  $("#resumeBtn").addEventListener("click", () => call("resume").then(render));
  $("#stopBtn").addEventListener("click", () => call("stop").then(render));

  $("#dlStart").addEventListener("click", onDownloadConfirm);
  $("#dlCancel").addEventListener("click", () => { closeDlModal(); });

  $("#helpBtn").addEventListener("click", () => { $("#helpOverlay").hidden = false; });
  $("#helpClose").addEventListener("click", () => { $("#helpOverlay").hidden = true; });
  $("#helpOverlay").addEventListener("click", (e) => {
    if (e.target === $("#helpOverlay")) $("#helpOverlay").hidden = true;
  });
}

async function onStart() {
  const res = await call("start");
  if (!res) return;
  if (res.need_download && res.need_download.length) {
    openDlModal(res.need_download);
  }
  render(res);
}

async function onDownloadConfirm() {
  const ids = pendingDownload.map((m) => m.id);
  $("#dlStart").disabled = true;
  await call("download_models", ids);
  // дальше прогресс покажет polling; кнопки скрываем
}

// ---- download modal ----
function openDlModal(models) {
  pendingDownload = models || [];
  const total = pendingDownload.reduce((s, m) => s + (m.size_mb || 0), 0);
  $("#dlTitle").textContent = "Нужно скачать модель";
  $("#dlDesc").textContent =
    pendingDownload.map((m) => m.label).join(", ") + ` · ~${total} MB`;
  $("#dlStart").hidden = false;
  $("#dlStart").disabled = false;
  $("#dlCancel").hidden = false;
  $("#dlBar").style.width = "0%";
  $("#dlBytes").textContent = "—";
  $("#dlOverlay").hidden = false;
  dlModalOpen = true;
}
function closeDlModal() {
  $("#dlOverlay").hidden = true;
  dlModalOpen = false;
  pendingDownload = [];
}
function updateDlModal(state) {
  const d = state.download || {};
  if (d.active) {
    if (!dlModalOpen) { $("#dlOverlay").hidden = false; dlModalOpen = true; }
    $("#dlStart").hidden = true;
    $("#dlCancel").hidden = true;
    $("#dlTitle").textContent = "Скачивание модели";
    $("#dlDesc").textContent = d.name || "";
    const pct = d.total ? Math.min(100, (d.downloaded / d.total) * 100) : 0;
    $("#dlBar").style.width = pct.toFixed(1) + "%";
    $("#dlBytes").textContent = `${fmtBytes(d.downloaded)} / ${d.total ? fmtBytes(d.total) : "?"}`;
  } else if (dlModalOpen && $("#dlStart").hidden) {
    // скачивание завершилось (кнопки были скрыты = шёл прогресс)
    closeDlModal();
  }
}

// ---- render ----
function render(state) {
  if (!state) return;

  // поля
  if (document.activeElement !== $("#inputPath")) $("#inputPath").value = state.input || "";
  if (document.activeElement !== $("#outputPath")) $("#outputPath").value = state.output || "";
  $("#dryRun").checked = !!state.dry;
  if ($("#langSel").value !== state.lang) $("#langSel").value = state.lang;
  if ($("#tierSel").value !== state.tier) $("#tierSel").value = state.tier;

  // режим
  $$(".mode").forEach((m) => m.classList.toggle("active", m.dataset.mode === state.mode));
  const showKey = state.mode === "external" || state.mode === "hybrid";
  $("#keyBlock").hidden = !showKey;
  $("#keyHint").textContent = state.key_present ? "✅ Ключ сохранён" : "⚠️ Ключ не задан";

  // фаза
  const pill = $("#phasePill");
  pill.textContent = state.phase;
  pill.className = "phase " + state.phase;

  // железо
  $("#hwSummary").textContent = state.hardware ? state.hardware.summary : "проба ещё не выполнялась";

  // модель
  const m = state.model || {};
  const parts = [];
  if (m.light) parts.push(modelLine("light", m.light));
  if (m.standard) parts.push(modelLine("standard", m.standard));
  $("#modelStatus").innerHTML = parts.join("<br>");

  // статистика
  $("#stTotal").textContent = state.total;
  $("#stDone").textContent = state.done;
  $("#stOk").textContent = state.ok;
  $("#stSkip").textContent = state.skip;
  $("#stErr").textContent = state.err;
  $("#stSpeed").textContent = state.speed ? state.speed + "/с" : "—";
  $("#stEta").textContent = fmtEta(state.eta);
  const pct = state.total ? Math.min(100, (state.done / state.total) * 100) : 0;
  $("#progBar").style.width = pct.toFixed(1) + "%";

  // управление
  $("#startBtn").disabled = !!state.running;
  $("#pauseBtn").disabled = !state.can_pause;
  $("#stopBtn").disabled = !state.running;
  $("#pauseBtn").hidden = !!state.paused;
  $("#resumeBtn").hidden = !state.paused;
  $("#resumeBtn").disabled = !state.can_resume;

  // лог
  const log = $("#log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  log.textContent = (state.logs || []).join("\n");
  if (atBottom) log.scrollTop = log.scrollHeight;

  // модалка скачивания
  updateDlModal(state);
}

function modelLine(tier, s) {
  const badge = s.downloaded
    ? '<span class="badge yes">скачана</span>'
    : '<span class="badge no">не скачана</span>';
  return `<b>${tier}</b>: ${s.label} ${badge}`;
}

// ---- polling ----
function startPolling() {
  setInterval(async () => {
    const s = await call("get_status");
    if (s) render(s);
  }, 400);
}

// pywebview готов
window.addEventListener("pywebviewready", init);
// на случай, если событие уже было
if (window.pywebview && window.pywebview.api) init();
