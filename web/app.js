// あるばい（支援拠点マップの Web 版）。REST API（/v1）のクライアント。設計: docs/web-ui-design.md
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
const MAP_STYLE = {
  light: "https://tiles.openfreemap.org/styles/liberty", // 公園・建物まで載っている（施設名は places に一本化）
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
  const ok = await run(async () => {
    const pos = await currentPosition(); // 対象地域の中にいる人だけが参加できる
    const session = await api("/sessions", { method: "POST", body: { consent: true, ...pos } });
    saveToken(session.token);
  }, "#consent-error");
  if (!ok) return;
  $("#consent").close();
  start();
});

function currentPosition() {
  return new Promise((resolve, reject) => {
    const fail = () => reject(new ApiError(0, "参加には今いる場所が必要です。位置の利用を許可してください"));
    if (!("geolocation" in navigator)) return fail();
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude }),
      fail,
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 60_000 },
    );
  });
}

function start() {
  if (!tracking) {
    tracking = true;
    startTracking();
  }
  refreshSpots();
}

// ---- 地図とピン ----

const darkMode = matchMedia("(prefers-color-scheme: dark)");
const theme = () => (darkMode.matches ? "dark" : "light");

const map = new maplibregl.Map({ container: "map", style: MAP_STYLE[theme()], ...START, attributionControl: false });
map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-left");
darkMode.addEventListener("change", () => map.setStyle(MAP_STYLE[theme()]));
map.on("style.load", useJapaneseLabels);
map.on("style.load", paintMap);
map.on("style.load", addPlaces);
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
    if (/^poi_r/.test(layer.id)) map.setLayoutProperty(layer.id, "visibility", "none"); // 施設は places に一本化（二重に出さない）
    const rule = MAP_PAINT[theme()].find(([re]) => re.test(layer.id));
    if (rule) map.setPaintProperty(layer.id, layer.type === "symbol" ? "text-color" : `${layer.type}-color`, rule[1]);
  }
}

// 八代市の施設（web/places.geojson、scripts/make_places.py）。Google マップのように、種類の色の丸に白い印（lucide の形）と同じ系統の色の名前で出す。
// ピンが主役なので、丸はピン（32px）より小さい 22px で影を付けない。名前が重なると丸ごと出さない。z = 出し始めるズーム（店・病院・役所・給油は 14、それ以外は 15）
const PLACE = {
  // 種類: [丸の色, 名前の色（明るい地図。暗い地図では丸の色）, 白い印]
  shop: ["#3478f6", "#1d5fc9", '<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/>'],
  food: ["#f2784b", "#c2532a", '<path d="M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2"/><path d="M7 2v20"/><path d="M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3Zm0 0v7"/>'],
  medical: ["#e5484d", "#c0262c", '<path fill="#fff" d="M4 9a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h4a1 1 0 0 1 1 1v4a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2v-4a1 1 0 0 1 1-1h4a2 2 0 0 0 2-2v-2a2 2 0 0 0-2-2h-4a1 1 0 0 1-1-1V4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4a1 1 0 0 1-1 1z"/>'],
  public: ["#6b7a90", "#4f5d73", '<path d="M3 22h18M6 18v-7M10 18v-7M14 18v-7M18 18v-7"/><path d="M12 2l8 5H4z"/>'],
  fuel: ["#1aa3b8", "#0d7f90", '<path d="M3 22h12M4 9h10"/><path d="M14 22V4a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v18"/><path d="M14 13h2a2 2 0 0 1 2 2v2a2 2 0 0 0 2 2a2 2 0 0 0 2-2V9.83a2 2 0 0 0-.59-1.42L18 5"/>'],
  school: ["#9a7356", "#755239", '<path d="M21.42 10.922a1 1 0 0 0-.019-1.838L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.832l8.57 3.908a2 2 0 0 0 1.66 0z"/><path d="M22 10v6"/><path d="M6 12.5V16a6 3 0 0 0 12 0v-3.5"/>'],
  park: ["#3fa45b", "#277a3e", '<path d="M10 10v.2A3 3 0 0 1 8.9 16H5a3 3 0 0 1-1-5.8V10a3 3 0 0 1 6 0Z"/><path d="M7 16v6"/><path d="M13 19v3"/><path d="M12 19h8.3a1 1 0 0 0 .7-1.7L18 14h.3a1 1 0 0 0 .7-1.7L16 9h.2a1 1 0 0 0 .8-1.7L13 3l-1.4 1.5"/>'],
  sight: ["#8e5bd6", "#6c3db5", '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>'],
};
async function addPlaces() {
  if (map.getSource("places")) return;
  const dark = theme() === "dark";
  await Promise.all(
    Object.entries(PLACE).map(async ([kind, [color, , glyph]]) => {
      const img = new Image(48, 48);
      img.src = "data:image/svg+xml," + encodeURIComponent(
        `<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><circle cx="24" cy="24" r="21.5" fill="${color}" stroke="${dark ? "#1f2023" : "#fff"}" stroke-width="3"/>` +
          `<g transform="translate(12 12)" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">${glyph}</g></svg>`,
      );
      await img.decode();
      if (!map.hasImage(`place-${kind}`)) map.addImage(`place-${kind}`, img, { pixelRatio: 2 });
    }),
  );
  if (map.getSource("places")) return; // 印を読む間に、別の style.load が足した
  map.addSource("places", { type: "geojson", data: "places.geojson", attribution: "施設: © OpenStreetMap / 八代市 CC BY 4.0" });
  map.addLayer({
    id: "places",
    type: "symbol",
    source: "places",
    minzoom: 14,
    filter: [">=", ["zoom"], ["get", "z"]],
    layout: {
      "icon-image": ["concat", "place-", ["get", "kind"]],
      "text-field": ["get", "name"],
      "text-font": ["Noto Sans Regular"],
      "text-size": 12,
      "text-max-width": 8,
      "text-variable-anchor": ["left", "right", "top", "bottom"],
      "text-radial-offset": 1.1,
      "text-justify": "auto",
    },
    paint: {
      "text-color": ["match", ["get", "kind"], ...Object.entries(PLACE).flatMap(([kind, [color, text]]) => [kind, dark ? color : text]), "#6e6e73"],
      "text-halo-color": dark ? "#1f2023" : "#f5f2eb",
      "text-halo-width": 1.2,
    },
  });
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
  if (p.source === "official") {
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
  if (!here) return;
  const ok = await run(
    () => api(`/candidates/${candidateId}/answers`, { method: "POST", body: { kind, lat: here.lat, lon: here.lon } }),
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

$("#locate").addEventListener("click", () => map.easeTo({ center: [here.lon, here.lat] }));

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
  const min = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000));
  if (min < 1) return "たった今";
  if (min < 60) return `${min}分前`;
  return `${Math.floor(min / 60)}時間${min % 60 ? `${min % 60}分` : ""}前`;
}

// 2 点の距離（m）。八代の緯度での近似
function distanceM(p, q) {
  return Math.hypot((p.lat - q.lat) * 110574, (p.lon - q.lon) * 93900);
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
if (token) start();
else showConsent();
