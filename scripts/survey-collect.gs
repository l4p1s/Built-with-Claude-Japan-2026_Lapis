// 記入フォーム（docs/survey/）の回答を、Google スプレッドシートに集める Google Apps Script。
// 設定のしかたは docs/survey-design.md §8。回答はこのスプレッドシートの中だけに残り、リポジトリには入らない。
//
// POST の本文（JSON）:
//   回答を送る: { v: 1, cid, name, home, days }  → 同じ cid の行があれば上書き、なければ足す
//   回答を見る: { list: true, key }               → key がスクリプトのプロパティ VIEW_KEY と同じなら全員分を返す

const SHEET = "回答";
const HEADER = ["端末", "呼び名", "受け取った日時", "日数", "場所の数", "回答"];
const MAX = 45000; // スプレッドシートの1つのセルに入る文字数（5万）より少なく

function doPost(e) {
  const body = (e.postData && e.postData.contents) || "";
  if (body.length > MAX) return out({ ok: false, error: "too_large" });
  let a;
  try { a = JSON.parse(body); } catch (err) { return out({ ok: false, error: "bad_json" }); }
  if (a && a.list) return list(a.key);
  if (!valid(a)) return out({ ok: false, error: "bad_shape" });

  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sh = sheet();
    const places = a.days.reduce((n, d) => n + d.stays.filter((s) => s.type !== "home").length, 0);
    const row = [a.cid, String(a.name).slice(0, 20), new Date(), a.days.length, places, body];
    const ids = sh.getLastRow() > 1 ? sh.getRange(2, 1, sh.getLastRow() - 1, 1).getValues().flat() : [];
    const i = ids.indexOf(a.cid);
    if (i >= 0) sh.getRange(i + 2, 1, 1, row.length).setValues([row]); // 送り直しは上書き
    else sh.appendRow(row);
  } finally {
    lock.releaseLock();
  }
  return out({ ok: true });
}

// 形だけ確かめる（いたずらで大きなものや別の形のものが入らないように）
function valid(a) {
  return a && a.v === 1 && /^[0-9a-f-]{36}$/.test(a.cid) && typeof a.name === "string"
    && Array.isArray(a.days) && a.days.length >= 1 && a.days.length <= 10
    && a.days.every((d) => d && Array.isArray(d.stays) && d.stays.length <= 40);
}

function list(key) {
  const want = PropertiesService.getScriptProperties().getProperty("VIEW_KEY");
  if (!want || key !== want) return out({ ok: false, error: "key" });
  const sh = sheet();
  const rows = sh.getLastRow() > 1 ? sh.getRange(2, 1, sh.getLastRow() - 1, HEADER.length).getValues() : [];
  return out({
    ok: true,
    answers: rows.map((r) => ({ received: new Date(r[2]).toISOString(), answer: JSON.parse(r[5]) })),
  });
}

function sheet() {
  const book = SpreadsheetApp.getActiveSpreadsheet();
  let sh = book.getSheetByName(SHEET);
  if (!sh) {
    sh = book.insertSheet(SHEET);
    sh.appendRow(HEADER);
    sh.setFrozenRows(1);
  }
  return sh;
}

const out = (o) => ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
