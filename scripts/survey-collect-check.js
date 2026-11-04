// scripts/survey-collect.gs（Google Apps Script）を、Google のサービスを差し替えて Node で動かして確かめる。
// 使い方: node scripts/survey-collect-check.js
const fs = require("fs");
const vm = require("vm");
const assert = require("assert/strict");

const rows = [];
const sheet = {
  getLastRow: () => rows.length,
  getRange: (r, c, n, w) => ({
    getValues: () => rows.slice(r - 1, r - 1 + n).map((row) => row.slice(c - 1, c - 1 + w)),
    setValues: (vals) => { rows[r - 1] = vals[0]; },
  }),
  appendRow: (row) => rows.push(row),
  setFrozenRows: () => {},
};
let made = false;
const ctx = {
  SpreadsheetApp: { getActiveSpreadsheet: () => ({
    getSheetByName: () => (made ? sheet : null),
    insertSheet: () => { made = true; return sheet; },
  }) },
  LockService: { getScriptLock: () => ({ waitLock: () => {}, releaseLock: () => {} }) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: () => "secret" }) },
  ContentService: { MimeType: { JSON: "json" }, createTextOutput: (t) => ({ setMimeType: () => JSON.parse(t) }) },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(__dirname + "/survey-collect.gs", "utf8") + "\nthis.doPost = doPost;", ctx);
const post = (o) => ctx.doPost({ postData: { contents: typeof o === "string" ? o : JSON.stringify(o) } });

const plain = (x) => JSON.parse(JSON.stringify(x)); // vm の中で作った配列を、こちらの配列にして比べる
const cid = "0f8fad5b-d9cb-469f-a165-70867728950e";
const answer = (name) => ({ v: 1, cid, name, home: [32.5, 130.6], days: [{ label: "地震の日", date: "2026-07-27", stays: [
  { type: "home" }, { type: "water" }, { type: "home" },
] }] });

assert.deepEqual(post(answer("A")), { ok: true });
assert.equal(rows.length, 2); // 見出し＋1行
assert.deepEqual(plain(rows[1].slice(0, 2)), [cid, "A"]);
assert.deepEqual(plain(rows[1].slice(3, 5)), [1, 1]); // 1日、場所1か所（家は数えない）
assert.deepEqual(post(answer("B")), { ok: true });
assert.equal(rows.length, 2); // 同じ端末の送り直しは上書き
assert.equal(rows[1][1], "B");
assert.deepEqual(post({ ...answer("C"), cid: "../x" }), { ok: false, error: "bad_shape" });
assert.deepEqual(post("{"), { ok: false, error: "bad_json" });
assert.deepEqual(post("x".repeat(50000)), { ok: false, error: "too_large" });
assert.deepEqual(post({ list: true, key: "ちがう" }), { ok: false, error: "key" });
const got = post({ list: true, key: "secret" });
assert.equal(got.ok, true);
assert.equal(got.answers.length, 1);
assert.equal(got.answers[0].answer.name, "B");
console.log("OK  survey-collect.gs: 送信・上書き・形の確認・大きさの上限・合言葉");
