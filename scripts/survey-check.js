// 行動の記入フォーム（docs/survey/）を、ヘッドレス Chromium で確かめる。設計: docs/survey-design.md §7
// 使い方: python3 -m http.server 18090 -d docs & → NODE_PATH=<playwright-core のある node_modules> node scripts/survey-check.js
const { chromium } = require("playwright-core");
const assert = require("assert/strict");

const URL = process.env.SURVEY_URL ?? "http://localhost:18090/survey/";
const SHOTS = process.env.SHOTS ?? "/tmp/survey-shots";
const errors = [];
const ok = (name) => console.log(`OK  ${name}`);

async function open(browser, opts = {}) {
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, locale: "ja-JP",
    permissions: ["clipboard-read", "clipboard-write"], ...opts,
  });
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") errors.push(`[console] ${m.text()}`); });
  page.on("pageerror", (e) => errors.push(`[pageerror] ${e.message}`));
  page.on("dialog", (d) => d.accept());
  await page.goto(URL);
  await page.locator("#map canvas").waitFor();
  return { ctx, page };
}

// 地図の中心から (dx, dy) ずらした位置をタップする
async function tapMap(page, dx, dy) {
  const box = await page.locator("#map").boundingBox();
  await page.mouse.click(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy);
}

// 地図の中心から (dx, dy) ずらした位置を長押しする
async function longPress(page, dx, dy) {
  const box = await page.locator("#map").boundingBox();
  await page.mouse.move(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy);
  await page.mouse.down();
  await page.waitForTimeout(700);
  await page.mouse.up();
}

async function setTime(page, which, t) {
  const [h, m] = t.split(":");
  await page.locator(`#f-${which}-h`).selectOption(h);
  await page.locator(`#f-${which}-m`).selectOption(m);
}

// from: 何時ごろ着いた / dur: どのくらいいた（ボタンの文字） / to: 出た時刻（「それ以上…」から）
async function fillStay(page, { from, dur, to, type, usual }) {
  if (type) await page.locator("#f-type").selectOption(type);
  if (usual) await page.locator("#f-usual").selectOption(usual);
  if (from) await setTime(page, "from", from);
  if (dur) await page.locator("#f-dur button", { hasText: dur }).click();
  if (to) {
    if (await page.locator("#t-to").isHidden()) await page.locator("#f-dur button", { hasText: "それ以上" }).click();
    await setTime(page, "to", to);
  }
}

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.HOME + "/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome",
  });
  require("fs").mkdirSync(SHOTS, { recursive: true });

  // 1. 開いてすぐの使い方の案内 → 説明と呼び名
  const { ctx, page } = await open(browser);
  await page.locator("#guide").waitFor();
  assert.equal(await page.locator("#intro").evaluate((d) => d.open), false);
  const steps = await page.evaluate(() => document.querySelector("#guide-count").textContent.split(" / ")[1]);
  for (let i = 1; i <= Number(steps); i++) {
    assert.equal(await page.locator("#guide-count").textContent(), `${i} / ${steps}`);
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${SHOTS}/01-guide-${i}.png` });
    await page.locator("#guide-next").click();
  }
  await page.locator("#guide").waitFor({ state: "hidden" });
  assert.equal(await page.locator("#intro").evaluate((d) => d.open), true);
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#intro").evaluate((d) => d.open), true);
  await page.screenshot({ path: `${SHOTS}/01-intro.png` });
  await page.locator("#name").fill("テスト");
  await page.locator("#intro-form button").click();
  assert.equal(await page.locator("#intro").evaluate((d) => d.open), false);
  await page.locator("#help").click();
  await page.locator("#guide").waitFor();
  await page.locator("#guide-skip").click();
  await page.locator("#guide").waitFor({ state: "hidden" });
  ok(`1 開いてすぐ案内が${steps}段で進み、そのあと説明と呼び名。? から見直せて、とばせる`);

  // 1b. 場所の検索（Photon と国土地理院は、決まった答えを返すように差し替える）
  await page.route("https://photon.komoot.io/api/**", (r) => r.fulfill({ json: { features: [
    { geometry: { coordinates: [130.585695, 32.5057637] }, properties: { name: "ゆめタウン八代", city: "八代市", state: "熊本県" } },
  ] } }));
  await page.route("https://msearch.gsi.go.jp/**", (r) => r.fulfill({ json: [
    { geometry: { coordinates: [130.6016, 32.5072] }, properties: { title: "熊本県八代市松江城町" } },
  ] }));
  assert.match(await page.locator("#hint").textContent(), /家はどのへん/);
  await page.locator("#q").fill("ゆめタウン");
  await page.locator("#results button").first().waitFor();
  assert.equal(await page.locator("#results button").count(), 2);
  await page.screenshot({ path: `${SHOTS}/01b-search.png` });
  await page.locator("#results button").first().click(); // 選んだ場所にピンが立つ
  await page.locator("#cand-card").waitFor();
  assert.equal(await page.locator("#cand-name").textContent(), "ゆめタウン八代");
  assert.equal(await page.locator("#cand-ok").textContent(), "ここを家にする");
  await page.screenshot({ path: `${SHOTS}/01b-cand.png` });
  await page.locator("#cand-ok").click();
  assert.equal(await page.locator(".pt").count(), 1);
  await page.route("https://photon.komoot.io/api/**", (r) => r.fulfill({ json: { features: [] } }));
  await page.route("https://msearch.gsi.go.jp/**", (r) => r.fulfill({ json: [] }));
  await page.locator("#q").fill("ないところ");
  await page.locator("#results", { hasText: "見つかりませんでした" }).waitFor();
  await page.locator("#q").fill("");
  ok("1b 名前で探すと候補が出て、選ぶとピンが立ち、ここを家にする で登録。見つからないと案内が出る");

  // 2. 家 → 2か所 → 家に帰った
  await page.waitForTimeout(1500); // 地図の移動とタイルの読み込み
  assert.equal(await page.locator("#day-date").isVisible(), true); // ふだんの日にも日付
  await page.locator("#day-date").fill("2026-07-20");
  assert.equal(await page.locator(".tab").first().textContent(), "ふだん 7/20");
  assert.equal(await page.locator("#t-from").isHidden(), true); // 1件目の家は「出た時刻」だけ
  await fillStay(page, { to: "07:45" });
  await page.locator("#add-next").click();
  assert.match(await page.locator("#hint").textContent(), /次に行った場所/);
  await tapMap(page, 80, -60);
  await fillStay(page, { type: "shop", usual: "true", from: "08:00", dur: "30分" });
  await page.locator("#f-mode").selectOption("car");
  await longPress(page, -70, -100); // 長押しでピン → 次の場所にする
  await page.locator("#cand-card").waitFor();
  assert.equal(await page.locator("#cand-ok").textContent(), "ここを次の場所にする");
  await page.locator("#cand-ok").click();
  await fillStay(page, { type: "water", usual: "false", from: "09:30", dur: "30分" });
  await page.locator("#f-heard").selectOption("neighbor");
  await page.locator("#f-purpose").fill("水をもらいに");
  await page.locator("#go-home").click();
  await fillStay(page, { from: "10:15", dur: "朝まで" });
  assert.match(await page.locator("#f-note").textContent(), /その日の終わり/);
  assert.equal(await page.locator(".pt").count(), 4);
  ok("2 日付を入れ、家 → タップ → 長押し → 家に帰った（朝まで）でつなぐ");

  // 3. 1日の帯
  assert.equal(await page.locator("#band .block").count(), 4);
  await page.locator("#band .block").nth(1).click();
  assert.match(await page.locator("#card-title").textContent(), /^2\. 店/);
  assert.equal(await page.locator("#f-dur button", { hasText: "30分" }).getAttribute("aria-pressed"), "true");
  await fillStay(page, { to: "08:45" }); // それ以上… で出た時刻を直接選ぶ
  await page.locator("#band .block").nth(2).click();
  await setTime(page, "from", "08:00"); // 前の店（〜8:45）と重ねる
  assert.match(await page.locator("#f-warn").textContent(), /重なって/);
  await setTime(page, "from", "09:30");
  await page.screenshot({ path: `${SHOTS}/02-day.png` });
  ok("3 時刻を入れると帯に出て、帯から場所を選べ、重なりを注意する");

  // 4. 日を足す
  await page.locator("#add-day").click();
  assert.equal(await page.locator(".tab").count(), 3);
  assert.equal(await page.locator(".pt").count(), 1); // 家から始まる
  await page.locator("#day-date").fill("2026-07-30");
  assert.equal(await page.locator(".tab").nth(2).textContent(), "地震後 7/30");
  ok("4 日を足すと家から始まり、日付がタブに出る");

  // 5. 送る
  await page.locator("#send").click();
  const json = await page.locator("#json").inputValue();
  const a = JSON.parse(json);
  assert.equal(a.v, 1);
  assert.equal(a.name, "テスト");
  assert.equal(a.days.length, 3);
  for (const x of a.home) assert.equal(x, Number(x.toFixed(3)));
  const [h, shop, water, back] = a.days[0].stays;
  assert.deepEqual([h.lat, h.lon], a.home);
  assert.deepEqual([back.lat, back.lon], a.home);
  assert.deepEqual([h.type, shop.type, water.type, back.type], ["home", "shop", "water", "home"]);
  assert.equal(shop.mode, "car");
  assert.deepEqual([shop.from, shop.to, water.from, water.to, back.to], ["08:00", "08:45", "09:30", "10:00", "00:00"]);
  assert.equal(a.days[0].date, "2026-07-20");
  assert.equal(water.usual, false);
  assert.equal(water.heard, "neighbor");
  assert.equal(water.purpose, "水をもらいに");
  assert.equal(a.days[2].date, "2026-07-30");
  await page.locator("#copy").click();
  await page.locator("#copy-msg", { hasText: "コピーしました" }).waitFor();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), json);
  await page.screenshot({ path: `${SHOTS}/03-send.png` });
  await page.locator("#send-close").click();
  ok(`5 送る内容が設計の形で、家が小数3桁、コピーできる（${json.length}文字）`);

  // 6. 書きかけの保存と消去
  await page.reload();
  await page.locator("#map canvas").waitFor();
  assert.equal(await page.locator("#intro").evaluate((d) => d.open), false);
  assert.equal(await page.locator("#guide").isHidden(), true); // 案内は2回目から出ない
  assert.equal(await page.locator(".tab").count(), 3);
  assert.equal(await page.locator(".pt").count(), 4);
  await page.locator("#send").click();
  await page.locator("#clear").click();
  await page.waitForLoadState("load");
  await page.locator("#guide").waitFor(); // 最初に戻ると、案内からやり直し
  ok("6 開き直すと復元され、この端末から消す で最初に戻る");
  await ctx.close();

  // ダークモードの見た目（目で確かめる用）
  const dark = await open(browser, { colorScheme: "dark" });
  await dark.page.locator("#guide-skip").click();
  await dark.page.locator("#name").fill("夜");
  await dark.page.locator("#intro-form button").click();
  await dark.page.waitForTimeout(1500);
  await tapMap(dark.page, 0, 0);
  await dark.page.waitForTimeout(1000);
  await dark.page.screenshot({ path: `${SHOTS}/04-dark.png` });

  await browser.close();
  assert.deepEqual(errors, []);
  ok("7 Console にエラーなし");
})().catch((e) => {
  console.error("FAILED:", e.message);
  console.log("Console のエラー: " + (errors.length ? "\n" + errors.join("\n") : "なし"));
  process.exit(1);
});
