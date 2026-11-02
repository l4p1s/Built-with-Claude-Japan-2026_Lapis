// 支援拠点マップ（Web 版）。REST API（/v1）のクライアント。設計: docs/web-ui-design.md
import * as maplibregl from "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";

const KIND_LABEL = {
  water: "給水",
  food: "食べ物",
  goods: "物資",
  car_sleeping: "宿泊・車中泊",
  shelter: "避難",
  other: "その他",
  none: "わからない",
  ended: "終わっていた",
};
const KIND_CHAR = { water: "水", food: "食", goods: "物", car_sleeping: "宿", shelter: "避", other: "他" };
const KIND_ICON = { water: "💧", food: "🍙", goods: "📦", car_sleeping: "🚗", shelter: "🏫", other: "…", none: "？" };
const REPORT_KINDS = ["water", "food", "goods", "car_sleeping", "shelter", "other"];
const QUESTION_KINDS = ["water", "food", "goods", "car_sleeping", "shelter", "none"];

const START = { center: [130.6016, 32.5072], zoom: 13 }; // 八代市役所
const MAP_STYLE = {
  light: "https://tiles.openfreemap.org/styles/positron",
  dark: "https://tiles.openfreemap.org/styles/dark",
};
const SEND_EVERY_MS = 60_000; // 位置を送り、質問と地図を更新する間隔
const RECORD_EVERY_MS = 30_000; // 位置を記録する間隔
const MAX_BUFFER = 1000; // 送れないときに端末にためる位置の上限（API の1回の上限と同じ）
const TOKEN_KEY = "aid-spot-token";

const $ = (selector) => document.querySelector(selector);

let token = loadToken();
let here = null; // 最新の現在地 { lat, lon }
let buffer = []; // まだ送っていない位置
let lastRecorded = 0;
let meMarker = null;
let tracking = false;
let spots = []; // 最後に受け取った GeoJSON の features
let markers = [];
let spotsSeq = 0; // 後から出した地図の更新を優先する
let filter = "all";
let reportKind = null;
const dismissed = new Set(); // 閉じた質問の候補 ID。開き直すまで聞かない

// ---- セッションと API ----

function loadToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // 保存できない環境（プライベートモードなど）では、開いている間だけ使う
  }
}

function saveToken(value) {
  token = value;
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // 同上
  }
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status; // 0 は通信できなかったとき
  }
}

async function api(path, { method = "GET", body } = {}) {
  const sent = token;
  const headers = {};
  if (sent) headers.Authorization = `Bearer ${sent}`;
  if (body) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(`/v1${path}`, { method, headers, body: body && JSON.stringify(body) });
  } catch {
    $("#banner").hidden = false;
    throw new ApiError(0, "接続できません");
  }
  $("#banner").hidden = res.status < 500;
  if (res.status === 401) {
    if (sent === token) {
      saveToken(null);
      buffer = []; // 参加していない間の位置は送らない
      showConsent();
    }
    throw new ApiError(401, "参加が切れました。もう一度同意してください");
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return res.status === 204 ? null : res.json();
}

async function errorMessage(res) {
  const data = await res.json().catch(() => ({}));
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail.map((d) => d.msg.replace(/^Value error, /, "")).join(" / ");
  }
  return `エラーが起きました（${res.status}）`;
}

// fn が失敗したら errorSelector の要素に理由を出す。成功したら true
async function run(fn, errorSelector) {
  const el = $(errorSelector);
  el.textContent = "";
  try {
    await fn();
    return true;
  } catch (e) {
    if (e.status !== 401) el.textContent = e.message;
    return false;
  }
}

// ---- 同意 ----

function showConsent() {
  if (!$("#consent").open) $("#consent").showModal();
}

$("#consent").addEventListener("cancel", (e) => e.preventDefault()); // Esc で閉じさせない

$("#consent-ok").addEventListener("click", async () => {
  const ok = await run(async () => {
    const session = await api("/sessions", { method: "POST", body: { consent: true } });
    saveToken(session.token);
  }, "#consent-error");
  if (!ok) return;
  $("#consent").close();
  start();
});

function start() {
  if (!tracking) {
    tracking = true;
    startTracking();
  }
  refreshSpots();
}

// ---- 地図とピン ----

const darkMode = matchMedia("(prefers-color-scheme: dark)");
const mapStyle = () => MAP_STYLE[darkMode.matches ? "dark" : "light"];

const map = new maplibregl.Map({ container: "map", style: mapStyle(), ...START });
darkMode.addEventListener("change", () => map.setStyle(mapStyle()));
map.on("style.load", useJapaneseLabels);
map.on("moveend", refreshSpots);

// OpenFreeMap の地名は「ローマ字＋日本語」の2段なので、日本語だけにする
function useJapaneseLabels() {
  for (const layer of map.getStyle().layers) {
    const field = layer.layout?.["text-field"];
    if (field && JSON.stringify(field).includes('"name')) {
      map.setLayoutProperty(layer.id, "text-field", ["coalesce", ["get", "name:ja"], ["get", "name"]]);
    }
  }
}

async function refreshSpots() {
  if (!token) return;
  const b = map.getBounds();
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(",");
  const seq = ++spotsSeq;
  let features;
  try {
    features = (await api(`/spots?bbox=${bbox}`)).features;
  } catch {
    return; // 通信できないときは、前に受け取ったピンをそのまま出しておく
  }
  if (seq !== spotsSeq) return; // もっと新しい更新が出ている
  spots = features;
  drawSpots();
}

function drawSpots() {
  for (const m of markers) m.remove();
  markers = spots
    .filter(matchesFilter)
    .map((f) =>
      new maplibregl.Marker({ element: pinElement(f), anchor: "bottom" })
        .setLngLat(f.geometry.coordinates)
        .addTo(map),
    );
}

function matchesFilter({ properties: p }) {
  if (filter === "all") return true;
  if (filter === "shelter") {
    return p.source === "official" || Boolean(p.kinds?.shelter || p.kinds?.car_sleeping);
  }
  return Boolean(p.kinds?.[filter]);
}

function pinLabel(p) {
  if (p.source === "official") return "避";
  const char = KIND_CHAR[p.top_kind] ?? "";
  return p.status === "confirmed" ? char : `${char}?`;
}

function pinTitle(p) {
  if (p.source === "official") return `${p.name}（公式の避難所）`;
  const kind = KIND_LABEL[p.top_kind] ?? "人の集まり";
  return `${kind}（${p.status === "confirmed" ? "確認済み" : "未確認"}）`;
}

function pinElement(feature) {
  const p = feature.properties;
  const kind = p.source === "official" ? "official" : p.status;
  const el = button("", () => openDetail(feature), `pin pin--${kind}`);
  if (p.faded) el.classList.add("pin--faded");
  el.setAttribute("aria-label", pinTitle(p));
  const shape = document.createElement("span");
  shape.className = "pin__shape";
  const text = document.createElement("span");
  text.className = "pin__text";
  text.textContent = pinLabel(p);
  shape.append(text);
  el.append(shape);
  return el;
}

for (const chip of document.querySelectorAll(".chip")) {
  chip.addEventListener("click", () => {
    filter = chip.dataset.filter;
    for (const c of document.querySelectorAll(".chip")) c.setAttribute("aria-pressed", String(c === chip));
    drawSpots();
  });
}

// ---- ピンの詳細 ----

function openDetail(feature) {
  const p = feature.properties;
  $("#detail-title").textContent = pinTitle(p);
  $("#detail-error").textContent = "";
  const body = $("#detail-body");
  body.replaceChildren();
  if (p.source === "official") {
    body.append(
      para(p.address ?? ""),
      para(`開設: ${formatTime(p.opened_at)}`),
      para("出典: BODIK 令和8年熊本地震オープンデータポータル"),
    );
  } else {
    body.append(para(`最後の確認: ${ago(p.last_activity_at)}`));
    const counts = Object.entries(p.kinds).map(([k, n]) => `${KIND_LABEL[k]} ${n}人`);
    if (counts.length) body.append(para(`回答: ${counts.join("、")}`));
    if (p.top_kind) {
      body.append(
        actions([
          ["まだやっている", () => answer(p.candidate_id, p.top_kind, "#detail-error")],
          ["もう終わっていた", () => answer(p.candidate_id, "ended", "#detail-error")],
        ]),
      );
    } else {
      body.append(para("近くで人が集まっています。ここに何がありますか？"));
      body.append(kindButtons(QUESTION_KINDS, (kind) => answer(p.candidate_id, kind, "#detail-error")));
    }
    for (const n of p.notes) body.append(noteRow(n));
  }
  $("#detail").showModal();
}

function noteRow(n) {
  const row = document.createElement("div");
  row.className = "note";
  row.append(para(`${KIND_LABEL[n.kind]}・${ago(n.answered_at)}: ${n.note}`));
  const flag = button(
    "不適切",
    async () => {
      if (await run(() => api(`/answers/${n.answer_id}/flags`, { method: "POST" }), "#detail-error")) {
        flag.disabled = true;
        flag.textContent = "通報しました";
      }
    },
    "link",
  );
  row.append(flag);
  return row;
}

async function answer(candidateId, kind, errorSelector) {
  const ok = await run(
    () => api(`/candidates/${candidateId}/answers`, { method: "POST", body: { kind } }),
    errorSelector,
  );
  if (!ok) return;
  if ($("#detail").open) $("#detail").close();
  hideQuestion();
  refreshSpots();
}

// ---- 位置の送信と「ここに何がある？」 ----

function startTracking() {
  setInterval(flush, SEND_EVERY_MS);
  if (!("geolocation" in navigator)) return setLocationState("denied");
  navigator.geolocation.watchPosition(
    onPosition,
    (err) => {
      if (err.code === err.PERMISSION_DENIED) setLocationState("denied");
    },
    { enableHighAccuracy: true, maximumAge: 15_000 },
  );
}

function onPosition(pos) {
  const { latitude: lat, longitude: lon, accuracy } = pos.coords;
  const first = here === null;
  here = { lat, lon };
  setLocationState("on");
  if (meMarker) {
    meMarker.setLngLat([lon, lat]);
  } else {
    const dot = document.createElement("div");
    dot.className = "me";
    dot.setAttribute("role", "img");
    dot.setAttribute("aria-label", "現在地");
    meMarker = new maplibregl.Marker({ element: dot }).setLngLat([lon, lat]).addTo(map);
  }
  if (token && pos.timestamp - lastRecorded >= RECORD_EVERY_MS) {
    lastRecorded = pos.timestamp;
    buffer.push({ lat, lon, accuracy_m: accuracy, recorded_at: new Date(pos.timestamp).toISOString() });
    buffer = buffer.slice(-MAX_BUFFER);
  }
  if (first) {
    map.jumpTo({ center: [lon, lat] });
    flush();
  }
}

async function flush() {
  if (!token) return;
  if (buffer.length) {
    const sending = buffer;
    buffer = [];
    try {
      await api("/locations", { method: "POST", body: { points: sending } });
    } catch (e) {
      // 通信できない・サーバーが落ちているときは戻して次に送る。不正な位置（422）などは捨てる
      if (e.status === 0 || e.status >= 500) buffer = [...sending, ...buffer].slice(-MAX_BUFFER);
    }
  }
  await checkQuestions();
  refreshSpots();
}

async function checkQuestions() {
  if (!here) return;
  let list;
  try {
    list = await api(`/questions?lat=${here.lat}&lon=${here.lon}`);
  } catch {
    return;
  }
  const q = list.find((item) => !dismissed.has(item.candidate_id));
  if (!q) return hideQuestion();
  const card = $("#question");
  if (!card.hidden && Number(card.dataset.candidateId) === q.candidate_id) return; // 押している途中で作り直さない
  card.dataset.candidateId = q.candidate_id;
  $("#question-title").textContent =
    q.source === "report" ? "近くで知らせがありました" : "近くで人が集まっています";
  $("#question-error").textContent = "";
  $("#question-kinds").replaceChildren(
    kindButtons(QUESTION_KINDS, (kind) => answer(q.candidate_id, kind, "#question-error")),
  );
  card.hidden = false;
}

function hideQuestion() {
  $("#question").hidden = true;
}

$("#question-close").addEventListener("click", () => {
  dismissed.add(Number($("#question").dataset.candidateId));
  hideQuestion();
});

function setLocationState(state) {
  const hint = {
    waiting: "現在地を確認しています…",
    denied: "知らせるには、今いる場所が必要です（位置の利用を許可してください）",
    on: "",
  }[state];
  $("#location-hint").textContent = hint;
  $("#location-hint").hidden = !hint;
  for (const b of $("#dock-kinds").querySelectorAll("button")) b.disabled = state !== "on";
  updateDockHeight();
}

// ---- 自分から知らせる ----

function openReport(kind) {
  if (!here) return;
  $("#report-kinds").replaceChildren(kindButtons(REPORT_KINDS, selectReportKind));
  selectReportKind(kind);
  $("#report-note").value = "";
  $("#report-error").textContent = "";
  $("#report").showModal();
}

function selectReportKind(kind) {
  reportKind = kind;
  for (const b of $("#report-kinds").querySelectorAll(".kind")) {
    b.setAttribute("aria-pressed", String(b.dataset.kind === kind));
  }
}

$("#report-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const note = $("#report-note").value.trim() || null;
  const body = { lat: here.lat, lon: here.lon, kind: reportKind, note };
  if (await run(() => api("/reports", { method: "POST", body }), "#report-error")) {
    $("#report").close();
    refreshSpots();
  }
});

// ---- メニュー ----

$("#menu-btn").addEventListener("click", () => $("#menu").showModal());

$("#leave").addEventListener("click", async () => {
  if (!confirm("参加をやめて、送ったデータをすべて消します。よろしいですか？")) return;
  if (await run(() => api("/sessions/me", { method: "DELETE" }), "#menu-error")) {
    saveToken(null);
    location.reload();
  }
});

// ---- 共通の部品 ----

function para(text) {
  const p = document.createElement("p");
  p.textContent = text;
  return p;
}

function button(text, onClick, className = "btn") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = className;
  b.textContent = text;
  b.addEventListener("click", onClick);
  return b;
}

function actions(list) {
  const div = document.createElement("div");
  div.className = "actions";
  div.append(...list.map(([text, onClick]) => button(text, onClick)));
  return div;
}

function kindButtons(kinds, onPick) {
  const grid = document.createElement("div");
  grid.className = "kinds";
  for (const kind of kinds) {
    const b = button("", () => onPick(kind), "kind");
    b.dataset.kind = kind;
    const icon = document.createElement("b");
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = KIND_ICON[kind];
    b.append(icon, KIND_LABEL[kind]);
    grid.append(b);
  }
  return grid;
}

function ago(iso) {
  const min = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000));
  if (min < 1) return "たった今";
  if (min < 60) return `${min}分前`;
  return `${Math.floor(min / 60)}時間${min % 60 ? `${min % 60}分` : ""}前`;
}

function formatTime(iso) {
  return new Date(iso).toLocaleString("ja-JP", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function updateDockHeight() {
  document.documentElement.style.setProperty("--dock-h", `${$(".dock").offsetHeight}px`);
}

// シートは外側（暗い部分）を押すと閉じる。「閉じる」「やめる」ボタンも同じ
for (const d of document.querySelectorAll("dialog.sheet")) {
  d.addEventListener("click", (e) => {
    if (e.target === d) d.close();
  });
}
for (const b of document.querySelectorAll("[data-close]")) {
  b.addEventListener("click", () => b.closest("dialog").close());
}

// ---- 起動 ----

$("#dock-kinds").replaceChildren(kindButtons(REPORT_KINDS, openReport));
setLocationState("waiting");
addEventListener("resize", updateDockHeight);
if (token) start();
else showConsent();
