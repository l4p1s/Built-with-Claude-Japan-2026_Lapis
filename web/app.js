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
const REPORT_KINDS = ["water", "food", "goods", "car_sleeping", "shelter", "other"];
const QUESTION_KINDS = ["water", "food", "goods", "car_sleeping", "shelter", "none"];

const START = { center: [130.6016, 32.5072], zoom: 13 }; // 八代市役所
// デモ（/?demo=1）: API を使わず、合成データの 1 日（web/demo/、scripts/make_demo_assets.py）を早回しで再生する。
// 地図の中心を自分の位置にする。at=lat,lon で最初の場所、from=HH:MM で最初の時刻を決められる
const PARAMS = new URLSearchParams(location.search);
const DEMO = PARAMS.has("demo");
const AT = PARAMS.get("at")?.split(",").map(Number);
if (AT?.length === 2 && AT.every(Number.isFinite)) START.center = [AT[1], AT[0]];
const SIM = {
  t: hm(PARAMS.get("from") ?? "08:00"), // 再生中の時刻（その日の 0:00 からの分）
  start: hm(PARAMS.get("from") ?? "08:00"), // 再生の始まり。これより前に見つかった候補は出さない（最初は何もない状態から始める）
  speed: Number(PARAMS.get("speed")) || 5, // 1 秒に進む分
  reports: [], // デモ中に「知らせる」で立てたピン
  playing: false,
  tl: null, // timeline.json
  frames: null, // people.bin（5 分おきの全員の位置）
  answered: new Map(), // 候補 id → 答えた種類
  ended: new Set(), // 「もう終わっていた」と答えた候補
  drawn: "", // 最後に描いたピンの状態（変わったときだけ描き直す）
};
const SIM_HIDE_AFTER = 120; // 集まりが終わってからピンを消すまで（分）
const SIM_QUESTION_M = 500; // この距離の未確認の候補について聞く（API の QUESTION_RADIUS_M と同じ）

function hm(s) {
  const [h, m] = String(s).split(":").map(Number);
  return Number.isFinite(h) && Number.isFinite(m) ? h * 60 + m : 8 * 60;
}
const MAP_STYLE = {
  light: "https://tiles.openfreemap.org/styles/liberty", // 公園・建物・施設名まで載っている
  dark: "https://tiles.openfreemap.org/styles/dark",
};
// 地図の塗り直し。[レイヤー ID に合う正規表現, 色] で、先に合ったものを使う。
// 色はレイヤーの種類に合わせて fill-color / line-color / text-color などに入れる
const MAP_PAINT = {
  // Apple マップのような柔らかい配色: 温かい地面、緑の公園、青い水、白い道、幹線は淡い黄色
  light: [
    [/^background$/, "#f5f2eb"],
    [/^landuse_residential$/, "#efebe2"],
    [/^(park|landcover_(wood|grass))/, "#cfe7b6"],
    [/^water$|^waterway_(tunnel|river|other)$/, "#a8d4f4"],
    [/^building$/, "#e9e4da"],
    [/motorway.*casing/, "#e8c070"],
    [/motorway/, "#fcd987"],
    [/(trunk_primary|secondary_tertiary|link).*casing/, "#e7dcbd"],
    [/trunk_primary|secondary_tertiary|link/, "#fff5d1"],
    [/casing/, "#e2ddd3"],
    [/^label_|^airport$/, "#3a3a3c"],
    [/^highway-name/, "#6e6e73"],
    [/^poi_r/, "#7c7c80"],
  ],
  // 真っ黒ではなく、少し明るい灰色の地面に、紺の水と深い緑の公園
  dark: [
    [/^background$/, "#1f2023"],
    [/^landuse_residential$/, "#242529"],
    [/^water$|^waterway$/, "#1d3349"],
    [/^(landuse_park|landcover_wood)$/, "#1f3326"],
    [/^building$/, "#2b2c30"],
    [/^highway_(minor|path)$/, "#38393d"],
    [/inner$|subtle$/, "#4a4b50"],
    [/^place_/, "#c7c7cc"],
    [/^highway_name/, "#8e8e93"],
    [/^water_name$/, "#7fa6cc"],
  ],
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
let selected = null; // 詳細を開いているピン（pinKey）
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
  if (DEMO) {
    $("#consent").close();
    start();
    return;
  }
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
    if (DEMO) startDemo();
    else startTracking();
  }
  refreshSpots();
}

// デモの見た目は通常と同じにする（動画を撮るため）。再生バーは controls=1 のときだけ出し、ふだんはキーで操作する:
// スペース = 再生/一時停止、← → = 10 分戻す/進める、[ ] = 遅く/速く、c = 再生バーを出す/隠す
if (DEMO) $("#replay").hidden = !PARAMS.has("controls");

// ---- 地図とピン ----

const darkMode = matchMedia("(prefers-color-scheme: dark)");
const theme = () => (darkMode.matches ? "dark" : "light");

const map = new maplibregl.Map({ container: "map", style: MAP_STYLE[theme()], ...START, attributionControl: false });
map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-left");
darkMode.addEventListener("change", () => map.setStyle(MAP_STYLE[theme()]));
map.on("style.load", useJapaneseLabels);
map.on("style.load", paintMap);
map.on("moveend", refreshSpots);
map.on("zoomend", declutterNames);
// 地図を押すと詳細を閉じる（ピンを押したときは閉じない）
map.on("click", (e) => {
  if (!e.originalEvent.target.closest(".pin")) $("#detail").close();
});

function paintMap() {
  for (const layer of map.getStyle().layers) {
    // 立体の建物は柔らかい見た目に合わず、古いスマホでは重いので出さない。平らな建物も、街全体を見る縮尺では出さない
    if (layer.type === "fill-extrusion") map.setLayoutProperty(layer.id, "visibility", "none");
    if (layer.id === "building") map.setLayerZoomRange(layer.id, 15, 24);
    const rule = MAP_PAINT[theme()].find(([re]) => re.test(layer.id));
    if (rule) map.setPaintProperty(layer.id, layer.type === "symbol" ? "text-color" : `${layer.type}-color`, rule[1]);
  }
}

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
  if (DEMO) return render();
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
  // 公式のピンを先に（下に）描く。同じ場所の検知・報告のピンを隠さない
  const official = (f) => f.properties.source === "official";
  markers = spots
    .filter(matchesFilter)
    .sort((a, b) => official(b) - official(a))
    .map((f) =>
      new maplibregl.Marker({ element: pinElement(f) })
        .setLngLat(f.geometry.coordinates)
        .addTo(map),
    );
  declutterNames();
}

// ピンの名前が、ほかのピンの丸や先に出した名前と重なったら隠す（Apple マップのように）。種類の名前を施設名より先に出す
function declutterNames() {
  const taken = [...document.querySelectorAll(".pin__shape")].map((el) => el.getBoundingClientRect());
  const names = [...document.querySelectorAll(".pin:not(.pin--official) .pin__name"), ...document.querySelectorAll(".pin--official .pin__name")];
  for (const el of names) {
    el.style.visibility = "";
    const r = el.getBoundingClientRect();
    const hit = taken.some((t) => r.left < t.right && t.left < r.right && r.top < t.bottom && t.top < r.bottom);
    el.style.visibility = hit ? "hidden" : "";
    if (!hit) taken.push(r);
  }
}

function matchesFilter({ properties: p }) {
  if (filter === "all") return true;
  if (filter === "shelter") {
    return p.source === "official" || Boolean(p.kinds?.shelter || p.kinds?.car_sleeping);
  }
  return Boolean(p.kinds?.[filter]);
}

function pinLabel(p) {
  if (p.source === "official") return KIND_CHAR[p.kind] ?? "避";
  const char = KIND_CHAR[p.top_kind] ?? "";
  return p.status === "confirmed" ? char : `${char}?`;
}

function pinTitle(p) {
  if (p.source === "official") return `${p.name}（公式の${p.kind === "water" ? "給水" : "避難所"}）`;
  const kind = KIND_LABEL[p.top_kind] ?? "人の集まり";
  return `${kind}（${p.status === "confirmed" ? "確認済み" : "未確認"}）`;
}

const pinKind = (p) => (p.source === "official" ? "official" : p.status);
const pinKey = (p) => String(p.candidate_id ?? p.official_id ?? p.name);

function pinElement(feature) {
  const p = feature.properties;
  const el = button("", () => openDetail(feature), `pin pin--${pinKind(p)}`);
  el.dataset.key = pinKey(p);
  if (el.dataset.key === selected) el.classList.add("pin--selected");
  if (p.faded) el.classList.add("pin--faded");
  if (p.fresh) el.classList.add("pin--new"); // 出たばかり（デモ）
  if (p.candidate_id) el.dataset.candidateId = p.candidate_id;
  el.setAttribute("aria-label", pinTitle(p));
  const shape = document.createElement("span");
  shape.className = "pin__shape";
  shape.textContent = pinLabel(p);
  // 丸の下に名前（Apple マップのように）。公式は施設名、それ以外は種類。デモの正解の名前は出さない
  const name = document.createElement("span");
  name.className = "pin__name";
  name.setAttribute("aria-hidden", "true");
  name.textContent = p.source === "official" ? p.name : (KIND_LABEL[p.top_kind] ?? "人の集まり");
  el.append(shape, name);
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

function selectPin(key) {
  selected = key;
  for (const el of document.querySelectorAll(".pin")) el.classList.toggle("pin--selected", el.dataset.key === key);
}

function openDetail(feature) {
  const p = feature.properties;
  selectPin(pinKey(p));
  $("#detail-tag").className = `tag tag--${pinKind(p)}`;
  $("#detail-tag").textContent = pinLabel(p);
  $("#detail-title").textContent = pinTitle(p);
  $("#detail-distance").textContent = distanceText(feature);
  $("#detail-error").textContent = "";
  const body = $("#detail-body");
  body.replaceChildren();
  if (p.source === "official" && p.detected) {
    body.append(para(`${formatTime(p.opened_at)} に ${p.observed} 人の集まりを検知。公式の拠点なので、聞かずに確認済みにしました`));
  } else if (p.source === "official") {
    body.append(
      para(p.address ?? ""),
      para(`開設: ${formatTime(p.opened_at)}`),
      para("出典: BODIK 令和8年熊本地震オープンデータポータル"),
    );
  } else {
    body.append(para(`最後の確認: ${ago(p.last_activity_at)}`));
    if (p.observed) body.append(para(`検知: ${p.observed} 人の「いつもと違う滞在」`));
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
  $("#detail").show(); // モーダルにしない: 地図を見たまま、ほかのピンも押せる
}

$("#detail").addEventListener("close", () => selectPin(null));
addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("#detail").close(); // モーダルでないシートは Esc で閉じないので
});

function distanceText(feature) {
  if (!here) return "";
  const [lon, lat] = feature.geometry.coordinates;
  const m = distanceM(here, { lat, lon });
  return `ここから ${m < 1000 ? `${Math.max(10, Math.round(m / 10) * 10)}m` : `${(m / 1000).toFixed(1)}km`}`;
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
  if (DEMO) {
    if (kind === "ended") SIM.ended.add(candidateId);
    else if (kind !== "none") SIM.answered.set(candidateId, kind);
    if ($("#detail").open) $("#detail").close();
    hideQuestion();
    render();
    return;
  }
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

function placeMe(lat, lon) {
  here = { lat, lon };
  setLocationState("on");
  $("#locate").disabled = false;
  if (meMarker) {
    meMarker.setLngLat([lon, lat]);
  } else {
    const dot = document.createElement("div");
    dot.className = "me";
    dot.setAttribute("role", "img");
    dot.setAttribute("aria-label", "現在地");
    meMarker = new maplibregl.Marker({ element: dot }).setLngLat([lon, lat]).addTo(map);
  }
}

// ---- デモ（再生）----

async function startDemo() {
  const [tl, bin] = await Promise.all([
    fetch("demo/timeline.json").then((r) => r.json()),
    fetch("demo/people.bin").then((r) => r.arrayBuffer()),
  ]);
  SIM.tl = tl;
  SIM.frames = new Int16Array(bin);
  if (!AT && tl.bounds) map.fitBounds(tl.bounds, { padding: 24, animate: false }); // 台本の範囲を最初に見せる
  const c = map.getCenter();
  placeMe(c.lat, c.lng);
  map.on("move", () => meMarker.setLngLat(map.getCenter()));
  map.on("moveend", () => {
    const m = map.getCenter();
    placeMe(m.lat, m.lng);
    checkQuestions();
  });
  map.on("style.load", addPeopleLayer);
  if (map.isStyleLoaded()) addPeopleLayer();
  else map.once("load", addPeopleLayer);
  $("#replay-play").addEventListener("click", () => setPlaying(!SIM.playing));
  $("#replay-speed").addEventListener("change", (e) => (SIM.speed = Number(e.target.value)));
  $("#replay-speed").value = String([1, 5, 15].includes(SIM.speed) ? SIM.speed : 5);
  addEventListener("keydown", (e) => {
    if (e.target.closest("input, select, textarea, dialog[open]")) return;
    const speeds = [1, 5, 15];
    const i = speeds.indexOf(SIM.speed);
    if (e.key === " ") setPlaying(!SIM.playing);
    else if (e.key === "ArrowRight") SIM.t = Math.min(1439, SIM.t + 10);
    else if (e.key === "ArrowLeft") SIM.t = Math.max(0, SIM.t - 10);
    else if (e.key === "]") SIM.speed = speeds[Math.min(2, i + 1)];
    else if (e.key === "[") SIM.speed = speeds[Math.max(0, i - 1)];
    else if (e.key === "c") $("#replay").hidden = !$("#replay").hidden;
    else return;
    e.preventDefault();
    $("#replay-speed").value = String(SIM.speed);
    updateDockHeight();
    render();
  });
  $("#replay-slider").addEventListener("input", (e) => {
    SIM.t = Number(e.target.value);
    render();
  });
  setInterval(() => {
    if (!SIM.playing) return;
    SIM.t = Math.min(1439, SIM.t + SIM.speed / 2);
    if (SIM.t >= 1439) setPlaying(false);
    render();
  }, 500);
  render();
  setPlaying(!PARAMS.has("paused"));
}

function setPlaying(on) {
  SIM.playing = on;
  $("#replay-play").textContent = on ? "一時停止" : "再生";
}

function simNow() {
  return Date.parse(`${SIM.tl.date}T00:00:00+09:00`) + SIM.t * 60_000;
}

const simIso = (minutes) => new Date(Date.parse(`${SIM.tl.date}T00:00:00+09:00`) + minutes * 60_000).toISOString();

// 再生中の時刻に地図に出す候補。公式の拠点に当たったものは聞かずに確認済み（公式のピン）にする
function demoFeatures() {
  const t = SIM.t;
  const reports = SIM.reports.map((r) =>
    feature(r, {
      source: "report",
      status: "pending",
      candidate_id: r.id,
      kinds: { [r.kind]: 1 },
      top_kind: r.kind,
      last_activity_at: simIso(r.t),
      faded: false,
      notes: r.note ? [{ answer_id: r.id, kind: r.kind, note: r.note, answered_at: simIso(r.t) }] : [],
      name: "",
    }),
  );
  return reports.concat(SIM.tl.candidates
    .filter((c) => c.detect > SIM.start && c.detect <= t && t < c.end + SIM_HIDE_AFTER && !SIM.ended.has(c.id))
    .map((c) => {
      const base = { fresh: t - c.detect < 20, observed: c.observed, name: c.name, faded: t > c.end };
      if (c.official) {
        return feature(c, { ...base, source: "official", status: "open", detected: true, kind: c.kind, opened_at: simIso(c.detect) });
      }
      const kind = SIM.answered.get(c.id) ?? null;
      return feature(c, {
        ...base,
        source: "detected",
        status: kind ? "confirmed" : "pending",
        candidate_id: c.id,
        kinds: kind ? { [kind]: 1 } : {},
        top_kind: kind,
        last_activity_at: simIso(Math.min(t, c.end)),
        notes: [],
      });
    }));
}

function feature(c, properties) {
  return { type: "Feature", geometry: { type: "Point", coordinates: [c.lon, c.lat] }, properties };
}

function demoQuestions() {
  return spots
    .filter((f) => f.properties.source === "detected" && f.properties.status === "pending")
    .map((f) => ({ f, d: distanceM(here, { lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0] }) }))
    .filter(({ d }) => d <= SIM_QUESTION_M)
    .sort((x, y) => x.d - y.d)
    .map(({ f }) => ({ candidate_id: f.properties.candidate_id, source: "detected" }));
}

function distanceM(p, q) {
  return Math.hypot((p.lat - q.lat) * 110574, (p.lon - q.lon) * 111320 * Math.cos((p.lat * Math.PI) / 180));
}

function render() {
  if (!SIM.tl) return;
  const t = Math.floor(SIM.t);
  $("#replay-clock").textContent = `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
  $("#replay-slider").value = t;
  drawPeople();
  const feats = demoFeatures();
  const key = feats.map((f) => [f.properties.candidate_id ?? f.properties.name, f.properties.status, f.properties.faded, f.properties.fresh].join()).join(";");
  if (key !== SIM.drawn) {
    SIM.drawn = key;
    spots = feats;
    drawSpots();
  }
  checkQuestions();
}

// 人の位置（5 分おき）。集まりができていく様子を見せる
function addPeopleLayer() {
  if (map.getSource("people")) return;
  map.addSource("people", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "people",
    type: "circle",
    source: "people",
    paint: { "circle-radius": 3, "circle-color": "#2563eb", "circle-opacity": 0.4 },
  });
  drawPeople();
}

function drawPeople() {
  const src = map.getSource("people");
  if (!src || !SIM.frames) return;
  const { origin, scale, step, people } = SIM.tl;
  const fi = Math.min(Math.floor(SIM.t / step), 1440 / step - 1);
  const features = [];
  for (let i = 0; i < people; i++) {
    const o = (fi * people + i) * 2;
    features.push({ type: "Feature", geometry: { type: "Point", coordinates: [origin[1] + SIM.frames[o + 1] / scale, origin[0] + SIM.frames[o] / scale] } });
  }
  src.setData({ type: "FeatureCollection", features });
}

function onPosition(pos) {
  const { latitude: lat, longitude: lon, accuracy } = pos.coords;
  const first = here === null;
  placeMe(lat, lon);
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
  if (DEMO) {
    list = demoQuestions();
  } else {
    try {
      list = await api(`/questions?lat=${here.lat}&lon=${here.lon}`);
    } catch {
      return;
    }
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
  if (DEMO) {
    SIM.reports.push({ id: 1000 + SIM.reports.length, ...body, t: SIM.t });
    $("#report").close();
    SIM.drawn = "";
    render();
    return;
  }
  if (await run(() => api("/reports", { method: "POST", body }), "#report-error")) {
    $("#report").close();
    refreshSpots();
  }
});

$("#locate").addEventListener("click", () => {
  if (DEMO && SIM.tl?.bounds) return map.fitBounds(SIM.tl.bounds, { padding: 24 }); // デモは台本の範囲へ
  map.easeTo({ center: [here.lon, here.lat] });
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
    // 地図のピンと同じ「漢字1文字の札」。わからないは「？」
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.setAttribute("aria-hidden", "true");
    tag.textContent = KIND_CHAR[kind] ?? "？";
    b.append(tag, KIND_LABEL[kind]);
    grid.append(b);
  }
  return grid;
}

function ago(iso) {
  const now = DEMO && SIM.tl ? simNow() : Date.now();
  const min = Math.max(0, Math.round((now - Date.parse(iso)) / 60_000));
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
// つまみを下に引くと閉じる
for (const g of document.querySelectorAll(".grabber")) {
  const sheet = g.closest("dialog");
  let y0 = null;
  const end = (e) => {
    if (y0 === null) return;
    const pulled = e.clientY - y0 > 80;
    y0 = null;
    if (pulled) sheet.close();
    sheet.style.transform = "";
    sheet.classList.remove("dragging");
  };
  g.addEventListener("pointerdown", (e) => {
    y0 = e.clientY;
    g.setPointerCapture(e.pointerId);
    sheet.classList.add("dragging");
  });
  g.addEventListener("pointermove", (e) => {
    if (y0 !== null) sheet.style.transform = `translateY(${Math.max(0, e.clientY - y0)}px)`;
  });
  g.addEventListener("pointerup", end);
  g.addEventListener("pointercancel", end);
}

// ---- 起動 ----

$("#dock-kinds").replaceChildren(kindButtons(REPORT_KINDS, openReport));
setLocationState("waiting");
addEventListener("resize", updateDockHeight);
if (token && !DEMO) start();
else showConsent();
