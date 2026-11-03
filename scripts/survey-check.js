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

async function fillStay(page, { from, to, type, usual }) {
  if (from) await page.locator("#f-from").fill(from);
  if (to) await page.locator("#f-to").fill(to);
  if (type) await page.locator("#f-type").selectOption(type);
  if (usual) await page.locator("#f-usual").selectOption(usual);
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
  await page.locator("#results button").first().click(); // 家を置くところなので、選んだ場所に家が置かれる
  assert.equal(await page.locator(".pt").count(), 1);
  await page.route("https://photon.komoot.io/api/**", (r) => r.fulfill({ json: { features: [] } }));
  await page.route("https://msearch.gsi.go.jp/**", (r) => r.fulfill({ json: [] }));
  await page.locator("#q").fill("ないところ");
  await page.locator("#results", { hasText: "見つかりませんでした" }).waitFor();
  await page.locator("#q").fill("");
  ok("1b 名前で探すと候補が出て、選ぶと家が置かれる。見つからないと案内が出る");

  // 2. 家 → 2か所 → 家に帰った
  await page.waitForTimeout(1500); // 地図の移動とタイルの読み込み
  await fillStay(page, { to: "07:45" });
  await page.locator("#add-next").click();
  assert.match(await page.locator("#hint").textContent(), /次に行った場所/);
  await tapMap(page, 80, -60);
  await fillStay(page, { to: "08:30", type: "shop", usual: "true" });
  await page.locator("#f-mode").selectOption("car");
  await page.locator("#add-next").click();
  await tapMap(page, -70, -100);
  await fillStay(page, { from: "09:30", to: "10:00", type: "water", usual: "false" });
  await page.locator("#f-heard").selectOption("neighbor");
  await page.locator("#f-purpose").fill("水をもらいに");
  await page.locator("#go-home").click();
  await fillStay(page, { from: "10:15", to: "00:00" });
  assert.match(await page.locator("#f-note").textContent(), /翌日/);
  assert.equal(await page.locator(".pt").count(), 4);
  ok("2 家を置き、2か所つなぎ、家に帰った で閉じる（夜をまたぐと「翌日」）");

  // 3. 1日の帯
  assert.equal(await page.locator("#band .block").count(), 4);
  await page.locator("#band .block").nth(1).click();
  assert.match(await page.locator("#card-title").textContent(), /^2\. 店/);
  await page.locator("#band .block").nth(2).click();
  await page.locator("#f-from").fill("08:00"); // 前の店（〜8:30）と重ねる
  assert.match(await page.locator("#f-warn").textContent(), /重なって/);
  await page.locator("#f-from").fill("09:30");
  await page.screenshot({ path: `${SHOTS}/02-day.png` });
  ok("3 時刻を入れると帯に出て、帯から場所を選べ、重なりを注意する");

  // 4. 日を足す
  await page.locator("#add-day").click();
  assert.equal(await page.locator(".tab").count(), 3);
  assert.equal(await page.locator(".pt").count(), 1); // 家から始まる
  await page.locator("#day-date").fill("2026-07-30");
  assert.equal(await page.locator(".tab").nth(2).textContent(), "7/30");
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
