// 手順書 Task 7 Step 2 の確認項目をヘッドレス Chromium で確かめる
const { chromium } = require("playwright-core");
const assert = require("assert/strict");

const URL = "http://localhost:18080/";
const HERE = { latitude: 32.5072, longitude: 130.6016 };
const SHOTS = process.env.SHOTS ?? "/tmp/ui-shots";
const base = { viewport: { width: 390, height: 844 }, locale: "ja-JP", geolocation: HERE };
const errors = [];
const results = [];
const ok = (name) => { results.push(`OK  ${name}`); console.log(`OK  ${name}`); };

async function open(browser, opts = {}) {
  const ctx = await browser.newContext({ ...base, permissions: ["geolocation"], ...opts });
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") errors.push(`[console] ${m.text()}`); });
  page.on("pageerror", (e) => errors.push(`[pageerror] ${e.message}`));
  await page.goto(URL);
  return { ctx, page };
}

async function consent(page) {
  await page.locator("#consent-ok").click();
  await page.waitForFunction(() => !document.querySelector("#consent").open);
}

async function nudgeMap(page) {
  // 地図を少し動かして moveend → 地図の更新を起こす
  await page.mouse.move(195, 350);
  await page.mouse.down();
  await page.mouse.move(205, 360, { steps: 5 });
  await page.mouse.up();
  await page.waitForTimeout(1500);
}

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.HOME + "/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome",
  });
  require("fs").mkdirSync(SHOTS, { recursive: true });

  // 1. 同意
  const A = await open(browser);
  assert.equal(await A.page.locator("#consent").evaluate((d) => d.open), true);
  await A.page.keyboard.press("Escape");
  assert.equal(await A.page.locator("#consent").evaluate((d) => d.open), true);
  await A.page.screenshot({ path: `${SHOTS}/01-consent.png` });
  await consent(A.page);
  ok("1 同意の画面が出て、Esc で閉じず、同意で閉じる");

  // 2. 現在地と地図
  await A.page.locator(".me").waitFor();
  await A.page.waitForFunction(() => [...document.querySelectorAll("#dock-kinds button")].every((b) => !b.disabled));
  await A.page.waitForTimeout(3000); // タイルと地名の読み込み
  await A.page.screenshot({ path: `${SHOTS}/02-map.png` });
  assert.equal(await A.page.locator("#replay").isVisible(), false);
  ok("2 現在地の点が出て、知らせるボタンが押せる。デモの再生バーは出ない（地名は 02-map.png で目視）");

  // 現在地ボタン: 地図をずらしてから押すと、現在地が画面の真ん中に戻る
  await nudgeMap(A.page);
  await A.page.locator("#locate").click();
  await A.page.waitForTimeout(1000);
  const me = await A.page.locator(".me").boundingBox();
  assert.ok(Math.abs(me.x + me.width / 2 - 195) < 3 && Math.abs(me.y + me.height / 2 - 422) < 3, JSON.stringify(me));
  ok("15 現在地ボタンで現在地が真ん中に戻る");

  // 5 → 3. 報告（URL は弾かれ、正しい備考なら送れる）
  await A.page.locator("#dock-kinds .kind[data-kind=water]").click();
  assert.equal(await A.page.locator("#report").evaluate((d) => d.open), true);
  assert.equal(await A.page.locator("#report-kinds .kind[data-kind=water]").getAttribute("aria-pressed"), "true");
  await A.page.locator("#report-note").fill("https://example.com");
  await A.page.locator("#report-form button[type=submit]").click();
  await A.page.waitForFunction(() => document.querySelector("#report-error").textContent.includes("URL"));
  ok("5 備考に URL を書くと理由が出る: " + (await A.page.locator("#report-error").textContent()));
  await A.page.locator("#report-note").fill("12時まで");
  await A.page.screenshot({ path: `${SHOTS}/03-report.png` });
  await A.page.locator("#report-form button[type=submit]").click();
  await A.page.waitForFunction(() => !document.querySelector("#report").open);
  const pending = A.page.locator(".pin--pending", { hasText: "水?" }).first();
  await pending.waitFor();
  // 以後は、この報告でできた候補のピンだけを追う（近くに検知の候補の「水」があっても混ざらない）
  const cid = await pending.getAttribute("data-candidate-id");
  const mine = (page) => page.locator(`.pin--confirmed[data-candidate-id="${cid}"]`);
  await A.page.screenshot({ path: `${SHOTS}/04-pending-pin.png` });
  ok("3 給水を報告するとオレンジの「水?」のピンが立つ");

  // 4. ピンの詳細（地図を暗くしないシート。押したピンが大きくなる）
  await pending.click();
  const detail = await A.page.locator("#detail").innerText();
  assert.match(detail, /給水（未確認）/);
  assert.match(detail, /ここから \d+m/);
  assert.match(detail, /回答: 給水 1人/);
  assert.match(detail, /12時まで/);
  assert.equal(await A.page.locator("#detail").evaluate((d) => d.matches(":modal")), false);
  assert.equal(await A.page.locator(`.pin--selected[data-candidate-id="${cid}"]`).count(), 1);
  await A.page.waitForTimeout(400); await A.page.screenshot({ path: `${SHOTS}/05-detail.png` });
  await A.page.locator("#detail [data-close]").click();
  await A.page.waitForFunction(() => !document.querySelector(".pin--selected")); // close イベントは後から届く
  ok("4 ピンの詳細に種類・距離・回答数・備考が出て、地図は暗くならず、押したピンが大きくなる");

  // 6. 別のセッションに通知 → 回答 → 紺のピン
  const B = await open(browser);
  await consent(B.page);
  await B.page.locator("#question").waitFor({ state: "visible", timeout: 20000 });
  await B.page.screenshot({ path: `${SHOTS}/06-question.png` });
  await B.page.locator("#question-kinds .kind[data-kind=water]").click();
  await B.page.locator("#question").waitFor({ state: "hidden" });
  await mine(B.page).waitFor();
  await nudgeMap(A.page);
  await mine(A.page).waitFor();
  await A.page.screenshot({ path: `${SHOTS}/07-confirmed.png` });
  ok("6 別のセッションに質問が出て、回答すると両方で紺の「水」になる");

  // 7. もう終わっていた → 消える
  await mine(A.page).click();
  await A.page.locator("#detail button", { hasText: "もう終わっていた" }).click();
  await A.page.waitForFunction(() => !document.querySelector("#detail").open);
  await mine(A.page).waitFor({ state: "detached" });
  ok("7 「もう終わっていた」で1対1になり、ピンが消える");

  // 8. 絞り込み（公式の避難所で確かめる）
  const official = () => A.page.locator(".pin--official").count();
  await A.page.locator(".chip[data-filter=all]").click();
  const nAll = await official();
  await A.page.locator(".chip[data-filter=food]").click();
  const nFood = await official();
  await A.page.locator(".chip[data-filter=shelter]").click();
  const nShelter = await official();
  await A.page.locator(".chip[data-filter=all]").click();
  assert.equal(nFood, 0);
  assert.equal(nShelter, nAll);
  ok(`8 絞り込み: 公式の避難所 すべて=${nAll} / 食=${nFood} / 避難=${nShelter}`);
  if (nAll > 0) {
    await A.page.locator(".pin--official").first().click();
    const text = await A.page.locator("#detail").innerText();
    assert.match(text, /公式の避難所/);
    assert.match(text, /開設:/);
    assert.match(text, /出典: BODIK/);
    await A.page.screenshot({ path: `${SHOTS}/08-official.png` });
    await A.page.locator("#detail [data-close]").click();
    ok("14 公式の避難所のピンに名前・開設日時・出典が出る");
  } else {
    results.push("--  14 表示範囲に、今開いている公式の避難所がない");
  }

  // 12. 通信できないとき
  await A.ctx.setOffline(true);
  await nudgeMap(A.page);
  await A.page.locator("#banner").waitFor({ state: "visible" });
  await A.page.screenshot({ path: `${SHOTS}/09-offline.png` });
  await A.ctx.setOffline(false);
  await nudgeMap(A.page);
  await A.page.locator("#banner").waitFor({ state: "hidden" });
  ok("12 通信できないと帯が出て、戻ると消える");

  // 11. 参加をやめる
  A.page.on("dialog", (d) => d.accept());
  await A.page.locator("#menu-btn").click();
  await A.page.screenshot({ path: `${SHOTS}/10-menu.png` });
  await A.page.locator("#leave").click();
  await A.page.waitForLoadState("load");
  await A.page.waitForFunction(() => document.querySelector("#consent").open);
  ok("11 参加をやめると同意の画面に戻る");

  // 9. ダークモード
  const D = await open(browser, { colorScheme: "dark" });
  await consent(D.page);
  await D.page.waitForTimeout(3000);
  const accent = await D.page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--accent").trim());
  assert.equal(accent, "#60a5fa");
  await D.page.screenshot({ path: `${SHOTS}/11-dark.png` });
  ok("9 ダークモードで配色が切り替わる（地図は 11-dark.png で目視）");

  // 10. 位置を許可しない
  const N = await open(browser, { permissions: [] });
  await consent(N.page);
  await N.page.waitForFunction(() => document.querySelector("#location-hint").textContent.includes("今いる場所が必要"), null, { timeout: 15000 });
  const disabled = await N.page.evaluate(() => [...document.querySelectorAll("#dock-kinds button")].every((b) => b.disabled));
  assert.equal(disabled, true);
  await N.page.screenshot({ path: `${SHOTS}/12-denied.png` });
  ok("10 位置を許可しないと、知らせるボタンが押せず理由が出る");

  await browser.close();
  console.log("\n" + results.join("\n"));
  console.log("\n13 Console のエラー: " + (errors.length ? "\n" + errors.join("\n") : "なし"));
})().catch(async (e) => {
  console.error("FAILED:", e.message);
  console.log(results.join("\n"));
  console.log("Console のエラー: " + (errors.length ? "\n" + errors.join("\n") : "なし"));
  process.exit(1);
});
