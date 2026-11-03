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

// 地図をタップして真ん中のピンをそこへ動かし、登録ボタンを押す
async function placeAt(page, dx, dy) {
  await tapMap(page, dx, dy);
  await page.waitForTimeout(800); // 地図が動き終わるのを待つ
  await page.locator("#place-btn").click();
}

// 選んでいるタブを押して日付を入れる
async function setDate(page, date) {
  await page.locator('.tab[aria-pressed="true"]').click();
  await page.locator("#date-dlg").waitFor();
  await page.locator("#day-date").fill(date);
  await page.locator("#date-dlg button.btn--primary").click();
  await page.locator("#date-dlg").waitFor({ state: "hidden" });
}

async function setTime(page, which, t) {
  const [h, m] = t.split(":");
  await page.locator(`#f-${which}-h`).selectOption(h);
  await page.locator(`#f-${which}-m`).selectOption(m);
}

// 「くわしく（任意）」の中の項目を選ぶ
async function pickMore(page, id, v) {
  if (!(await page.locator(".more").evaluate((d) => d.open))) await page.locator(".more summary").click();
  await page.locator(`${id} [data-v="${v}"]`).click();
}

// from: 何時ごろ着いた / dur: どのくらいいた（ボタンの文字） / to: 出た時刻（「それ以上…」から）
async function fillStay(page, { from, dur, to, type, usual }) {
  if (type) await page.locator(`#f-type [data-v="${type}"]`).click();
  if (usual) await page.locator(`#f-usual [data-v="${usual}"]`).click();
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
  assert.equal(await page.locator("#place-btn").textContent(), "ここを家にする");
  assert.equal(await page.locator("#center-pin").isVisible(), true);
  await page.locator("#q").fill("ゆめタウン");
  await page.locator("#results button").first().waitFor();
  assert.equal(await page.locator("#results button").count(), 2);
  await page.screenshot({ path: `${SHOTS}/01b-search.png` });
  await page.locator("#results button").first().click(); // 地図がそこへ動き、真ん中のピンが合う
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${SHOTS}/01b-pin.png` });
  await page.locator("#place-btn").click();
  assert.equal(await page.locator(".pt").count(), 1);
  assert.equal(await page.locator("#center-pin").isHidden(), true); // 入力中はピンとボタンを隠す
  await page.route("https://photon.komoot.io/api/**", (r) => r.fulfill({ json: { features: [] } }));
  await page.route("https://msearch.gsi.go.jp/**", (r) => r.fulfill({ json: [] }));
  await page.locator("#q").fill("ないところ");
  await page.locator("#results", { hasText: "見つかりませんでした" }).waitFor();
  await page.locator("#q").fill("");
  ok("1b 名前で探すと地図がそこへ動き、ここを家にする で真ん中のピンの位置が家になる");

  // 2. 家 → 2か所 → 家に帰った
  assert.equal(await page.locator("#t-from").isHidden(), true); // 1件目の家は「出た時刻」だけ
  assert.equal(await page.locator("#t-to").evaluate((e) => e.classList.contains("field--need")), true); // 未入力は赤
  await fillStay(page, { to: "07:45" });
  assert.equal(await page.locator("#t-to").evaluate((e) => e.classList.contains("field--need")), false);
  const mapOpen = (await page.locator("#map").boundingBox()).height;
  await page.locator("#card-done").click(); // 決定すると入力欄が閉じ、地図が広くなる
  assert.equal(await page.locator("#card").isHidden(), true);
  const mapClosed = (await page.locator("#map").boundingBox()).height;
  assert.ok(mapClosed > mapOpen + 150, `地図の高さ ${mapOpen} → ${mapClosed}`);
  assert.equal(await page.locator("#next-tip").isVisible(), true); // 家を決めたら、次にすることを示す
  await page.screenshot({ path: `${SHOTS}/02-closed.png` });
  assert.equal(await page.locator(".tab").first().evaluate((e) => e.classList.contains("tab--need")), true);
  await setDate(page, "2026-07-20"); // ふだんの日にも日付
  assert.equal(await page.locator(".tab").first().textContent(), "ふだん 7/20");
  assert.equal(await page.locator(".tab").first().evaluate((e) => e.classList.contains("tab--need")), false);
  assert.equal(await page.locator("#place-btn").textContent(), "ここを次の場所にする");
  await placeAt(page, 80, -60);
  assert.equal(await page.locator(".pt--need").count(), 1); // 入れていない場所の点は赤
  assert.equal(await page.locator("#next-tip").isHidden(), true);
  await fillStay(page, { type: "shop", usual: "true", from: "08:00", dur: "30分" });
  await pickMore(page, "#f-mode", "car");
  await placeAt(page, -80, -50); // 入力中に地図をタップすると、入力欄が閉じてピンが動く
  await fillStay(page, { type: "water", usual: "false", from: "09:30", dur: "30分" });
  await pickMore(page, "#f-heard", "neighbor");
  await page.locator("#f-purpose").fill("水をもらいに");
  await page.locator("#go-home").click();
  await fillStay(page, { from: "10:15", dur: "朝まで" });
  assert.match(await page.locator("#f-note").textContent(), /その日の終わり/);
  assert.equal(await page.locator(".pt").count(), 4);
  ok("2 タブで日付、真ん中のピンで 家 → 2か所 → 家に帰った（朝まで）");

  // 3. 1日の帯
  assert.equal(await page.locator("#band .block").count(), 4);
  await page.locator("#band .block").nth(1).click();
  assert.match(await page.locator("#card-title").textContent(), /^2か所目: 店/);
  assert.equal(await page.locator("#need-type").isHidden(), true); // 入れた項目には「未入力」が出ない
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
  await setDate(page, "2026-07-30");
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
  assert.deepEqual(a.home, [32.506, 130.586]); // 検索で動かした真ん中のピンの位置（約100mに丸め）
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
