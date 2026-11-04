// 行動の記入フォーム（docs/survey/）を、ヘッドレス Chromium で確かめる。設計: docs/survey-design.md §7
// 使い方: python3 -m http.server 18090 -d docs & → NODE_PATH=<playwright-core のある node_modules> node scripts/survey-check.js
const { chromium } = require("playwright-core");
const assert = require("assert/strict");

const URL = process.env.SURVEY_URL ?? "http://localhost:18090/survey/";
const SHOTS = process.env.SHOTS ?? "/tmp/survey-shots";
const errors = [];
const ok = (name) => console.log(`OK  ${name}`);

async function open(browser, opts = {}, url = URL) {
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, locale: "ja-JP",
    ...opts,
  });
  const page = await ctx.newPage();
  page.on("console", (m) => { if (m.type() === "error") errors.push(`[console] ${m.text()}`); });
  page.on("pageerror", (e) => errors.push(`[pageerror] ${e.message}`));
  page.on("dialog", (d) => d.accept());
  await page.goto(url);
  if (url === URL) await page.locator("#map canvas").waitFor({ state: "attached" });
  return { ctx, page };
}

const title = (page) => page.locator("#qt").textContent();
// 初めて出る画面では紹介が出るので、最後まで進めて閉じる。紹介した画面の数を数える
let guided = 0;
async function dismiss(page) {
  await page.waitForTimeout(50);
  if (await page.locator("#guide").isVisible()) guided++;
  while (await page.locator("#guide").isVisible()) await page.locator("#guide-next").click();
}
async function press(page, sel) {
  await page.locator(sel).click();
  await dismiss(page);
}
const next = (page) => press(page, "#next");
const needs = (page) => page.locator("main > section:not([hidden]) .field--need").evaluateAll((els) => els.map((e) => e.dataset.need));

// 地図をタップして真ん中のピンをそこへ動かし、「ここに行った」を押す
async function placeAt(page, dx, dy) {
  const box = await page.locator("#map").boundingBox();
  await page.mouse.click(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy);
  await page.waitForTimeout(800); // 地図が動き終わるのを待つ
  await next(page);
}

async function setTime(page, h, m, t) {
  const [hh, mm] = t.split(":");
  await page.locator(h).selectOption(hh);
  await page.locator(m).selectOption(mm);
}
const setHM = (page, t) => setTime(page, "#h-h", "#h-m", t);

// 「そこは何？」→「何時ごろ着いて、どのくらい？」
async function fillPlace(page, { type, usual, from, dur, to }) {
  await page.locator(`#f-type [data-v="${type}"]`).click();
  await page.locator(`#f-usual [data-v="${usual}"]`).click();
  await next(page);
  assert.match(await title(page), /^何時ごろ着いて、どのくらい/);
  await setTime(page, "#f-from-h", "#f-from-m", from);
  if (dur) await page.locator("#f-dur button", { hasText: dur }).click();
  if (to) {
    await page.locator("#f-dur button", { hasText: "それ以上" }).click();
    await setTime(page, "#f-to-h", "#f-to-m", to);
  }
  await next(page);
}

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.HOME + "/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome",
  });
  require("fs").mkdirSync(SHOTS, { recursive: true });
  const { ctx, page } = await open(browser);
  await page.route("https://photon.komoot.io/api/**", (r) => r.fulfill({ json: { features: [
    { geometry: { coordinates: [130.585695, 32.5057637] }, properties: { name: "ゆめタウン八代", city: "八代市", state: "熊本県" } },
  ] } }));
  await page.route("https://msearch.gsi.go.jp/**", (r) => r.fulfill({ json: [
    { geometry: { coordinates: [130.6016, 32.5072] }, properties: { title: "熊本県八代市松江城町" } },
  ] }));

  // 0. 開いてすぐ、画面の紹介が順に出る。? で見直せて、とばせる
  await page.locator("#guide").waitFor();
  assert.equal(await page.locator("#guide-count").textContent(), "1 / 4");
  for (let i = 1; i <= 4; i++) {
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${SHOTS}/00-guide-${i}.png` });
    await page.locator("#guide-next").click();
  }
  assert.equal(await page.locator("#guide").isHidden(), true);
  await page.locator("#help").click();
  await page.locator("#guide").waitFor();
  await page.locator("#guide-skip").click();
  assert.equal(await page.locator("#guide").isHidden(), true);
  ok("0 開いてすぐ紹介が4段で進む。? で見直せて、とばせる");

  // 1. はじめに（呼び名がないと進めない）
  assert.equal(await page.locator("#back").isHidden(), true);
  await page.screenshot({ path: `${SHOTS}/01-intro.png` });
  await next(page);
  assert.deepEqual(await needs(page), ["name"]);
  await page.locator("#name").fill("テスト");
  await next(page);
  ok("1 はじめに: 呼び名がないと赤で知らせ、入れると進む");

  // 2. 家（検索して真ん中のピン）
  assert.equal(await title(page), "家はどこですか？");
  assert.equal(await page.locator("#next").textContent(), "ここが家");
  assert.equal(await page.locator("#go-home").isHidden(), true);
  const mapH = (await page.locator("#map").boundingBox()).height;
  assert.ok(mapH > 450, `地図の高さ ${mapH}`);
  await page.locator("#layer").click(); // 航空写真に切り替えて、地図に戻す
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/02-photo.png` });
  assert.equal(await page.locator("#layer").textContent(), "地図");
  await page.locator("#layer").click();
  assert.equal(await page.locator("#layer").textContent(), "写真");
  await page.locator("#q").fill("ゆめタウン");
  await page.locator("#results button").first().waitFor();
  assert.equal(await page.locator("#results button").count(), 2);
  await page.screenshot({ path: `${SHOTS}/02-search.png` });
  assert.equal(guided, 1); // 家の画面の紹介
  await page.locator("#results button").first().click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${SHOTS}/02-home.png` });
  await next(page);
  ok(`2 家: 名前で探して真ん中のピンで決める。航空写真に切り替えられる（地図の高さ ${Math.round(mapH)}px）`);

  // 3. いつもの平日（日付は聞かない）: 家を出た → 2か所 → 家に帰った
  assert.equal(await title(page), "いつもの平日は、何時ごろ家を出ますか？");
  assert.equal(await page.locator("#where").textContent(), "いつもの平日");
  await next(page);
  assert.deepEqual(await needs(page), ["hm"]);
  await setHM(page, "07:45");
  await next(page);

  assert.equal(await title(page), "最初に行く場所はどこですか？");
  assert.equal(await page.locator("#next").textContent(), "ここに行った");
  assert.equal(await page.locator(".pt").count(), 1); // 家の点
  await placeAt(page, 80, -60);
  assert.equal(await title(page), "そこは何ですか？");
  assert.match(await page.locator("#where").textContent(), /1か所目$/);
  await next(page);
  assert.deepEqual(await needs(page), ["type", "usual"]);
  assert.equal(await page.locator("#fd-detail").isHidden(), true); // 種類を選ぶまでは出さない
  await page.locator('#f-type [data-v="shop"]').click();
  assert.deepEqual(await needs(page), ["usual"]); // 入れた項目から赤が消える
  assert.equal(await page.locator("#fd-detail").isVisible(), true);
  await page.locator("#f-detail").fill("○○スーパー");
  await page.locator(".more summary").click();
  await page.locator('#f-mode [data-v="car"]').click();
  await page.screenshot({ path: `${SHOTS}/03-type.png` });
  await fillPlace(page, { type: "shop", usual: "true", from: "08:00", dur: "30分" });

  assert.equal(await title(page), "次に行く場所はどこですか？");
  assert.equal(await page.locator(".pt").count(), 2);
  await page.screenshot({ path: `${SHOTS}/03-place.png` });
  await placeAt(page, -80, -50);
  // どの順番でも入る: 分 → 長さ → 時 の順に選ぶ
  await page.locator('#f-type [data-v="water"]').click();
  await page.locator('#f-usual [data-v="false"]').click();
  await page.locator(".more summary").click();
  await page.locator('#f-heard [data-v="neighbor"]').click();
  await page.locator("#f-purpose").fill("水をもらいに");
  await page.locator("#f-purpose").blur();
  await next(page);
  await page.locator("#f-from-m").selectOption("30");
  await page.locator("#f-dur button", { hasText: "30分" }).click();
  assert.equal(await page.locator("#f-from-m").inputValue(), "30");
  await page.locator("#f-from-h").selectOption("09");
  await page.screenshot({ path: `${SHOTS}/03-time.png` });
  await next(page);

  await press(page, "#go-home");
  assert.equal(await title(page), "何時ごろ家に帰りますか？");
  await setHM(page, "10:15");
  await next(page);
  ok("3 いつもの平日: 日付なしで、家を出た → 2か所（種類・時刻）→ 家に帰った");

  // 4. 見直し: 帯と一覧、押すと直せる。戻るで戻れる
  assert.equal(await title(page), "この日の動きを確かめてください");
  assert.equal(await page.locator("#band .block").count(), 4);
  assert.equal(await page.locator("#list .row").count(), 4);
  assert.equal(await page.locator(".pt").count(), 4);
  await page.screenshot({ path: `${SHOTS}/04-review.png` });
  await page.locator("#list .row").nth(1).click();
  await dismiss(page); // 店を直す → 場所 → 種類 → 時刻 → 見直しに戻る
  assert.equal(await title(page), "この場所はどこですか？");
  assert.equal(await page.locator("#next").textContent(), "ここに決める");
  assert.equal(await page.locator("#go-home").isHidden(), true); // 直すときは「家に帰った」を出さない
  await next(page);
  assert.equal(await title(page), "そこは何ですか？");
  await next(page);
  await page.locator("#f-dur button", { hasText: "それ以上" }).click();
  await setTime(page, "#f-to-h", "#f-to-m", "08:45");
  await next(page);
  assert.equal(await title(page), "この日の動きを確かめてください");
  await press(page, "#back");
  assert.equal(await title(page), "何時ごろ家に帰りますか？");
  await press(page, "#back");
  assert.equal(await title(page), "次に行く場所はどこですか？"); // 家に帰った、の手前
  await press(page, "#go-home"); // 帰った時刻は残っている
  assert.equal(await page.locator("#h-h").inputValue(), "10");
  await next(page);
  ok("4 見直し: 帯と一覧が出て、押すと直せ、戻るで1問ずつ戻れる");

  // 5. いつもの休日 → 地震の日（いつもの平日と同じ。写して1か所足す）→ 地震の後の日（違った。家に帰らない日）
  await next(page);
  assert.equal(await title(page), "いつもの休日は、何時ごろ家を出ますか？");
  await setHM(page, "09:00");
  await next(page);
  // 「家に帰った」でなく、次の場所を自宅にして「このあとは出かけない」でも1日を終えられる
  await placeAt(page, 0, 0);
  await page.locator('#f-type [data-v="home"]').click();
  assert.equal(await page.locator("#home-note").isVisible(), true);
  await page.locator('#f-usual [data-v="true"]').click();
  await next(page);
  assert.equal(await title(page), "何時ごろ家に帰って、どのくらい家にいますか？");
  await page.locator("#f-dur button", { hasText: "このあとは出かけない" }).click(); // 着いた時刻より先でもよい
  await setTime(page, "#f-from-h", "#f-from-m", "11:00");
  assert.equal(await page.locator("#f-note").textContent(), "その日の終わりまで家にいた");
  await next(page);
  assert.equal(await title(page), "この日の動きを確かめてください");
  assert.match(await page.locator("#list .row").nth(1).textContent(), /家に帰った11:00/);

  await next(page);
  assert.equal(await title(page), "地震の日は、何月何日ですか？");
  assert.equal(await page.locator("#del-day").isHidden(), true); // 最初の4日は消せない
  await next(page);
  assert.deepEqual(await needs(page), ["date"]);
  await page.locator("#day-date").fill("2026-07-27");
  await next(page);
  assert.equal(await title(page), "この日の動きは、いつもと同じでしたか？");
  await page.locator('[data-copy="0"]').click();
  await dismiss(page);
  assert.equal(await title(page), "この日の動きを確かめてください");
  assert.equal(await page.locator("#list .row").count(), 4); // いつもの平日を写した
  await press(page, "#add-place");
  assert.equal(await title(page), "足す場所はどこですか？");
  await placeAt(page, 40, 80);
  await fillPlace(page, { type: "food", usual: "false", from: "07:00", dur: "30分" });
  assert.equal(await title(page), "この日の動きを確かめてください");
  assert.equal(await page.locator("#list .row").count(), 5);
  assert.match(await page.locator("#list .row").nth(1).textContent(), /炊き出し/); // 着いた時刻の順に並ぶ
  await page.screenshot({ path: `${SHOTS}/05-copied.png` });
  await press(page, "#back");
  assert.equal(await title(page), "この日の動きは、いつもと同じでしたか？"); // 写した日の戻るは、この問い
  await page.locator('[data-copy="0"]').click(); // 戻ってきて同じ方を押しても、写し直さない
  assert.equal(await page.locator("#list .row").count(), 5);
  await next(page);
  assert.equal(await title(page), "地震の後の日は、何月何日ですか？");
  await page.locator("#day-date").fill("2026-07-29");
  await next(page);
  await press(page, "#same-no");
  assert.equal(await title(page), "その日、何時ごろ家を出ましたか？");
  await setHM(page, "08:00");
  await next(page);
  await placeAt(page, 60, -60);
  await fillPlace(page, { type: "water", usual: "false", from: "08:30", dur: "1時間" });
  await placeAt(page, -60, 60);
  await page.locator('#f-type [data-v="car_sleeping"]').click();
  await page.locator('#f-usual [data-v="false"]').click();
  await next(page);
  await setTime(page, "#f-from-h", "#f-from-m", "20:00");
  await page.locator("#f-dur button", { hasText: "それ以上" }).click();
  await setTime(page, "#f-to-h", "#f-to-m", "06:00");
  assert.match(await page.locator("#f-note").textContent(), /翌日の 06:00/);
  await next(page);
  await press(page, "#end-day"); // 家に帰らず、この日はここまで
  assert.equal(await title(page), "この日の動きを確かめてください");
  assert.equal(await page.locator("#list .row").count(), 3);
  assert.equal(await page.locator(".row--need").count(), 0);
  await next(page);
  assert.equal(guided, 10); // 画面の種類ごとに1回だけ（家・家を出た・場所・種類・時刻・帰った・見直し・日付・同じ？・ほかの日）
  ok("5 いつもの休日。地震の日はいつもの平日を写して1か所足す。地震の後の日は家に帰らない日（車中泊）");

  // 6. ほかの日 → 送信（送り先の Apps Script は差し替える。1回目は失敗、2回目は成功）
  assert.equal(await title(page), "ほかの日も書きますか？");
  await press(page, "#more-no");
  assert.equal(await title(page), "送信する");
  const posted = [];
  let accept = false;
  await page.route("https://script.google.com/**", (r) => {
    posted.push(r.request().postData());
    r.fulfill({ json: accept ? { ok: true } : { ok: false, error: "bad_shape" } });
  });
  await page.locator("#submit").click();
  await page.locator("#sent-err", { hasText: "送信できませんでした" }).waitFor();
  accept = true;
  await page.locator("#submit").click();
  await page.locator("#sent-msg", { hasText: "送信しました" }).waitFor();
  assert.equal(await page.locator("#sent-err").textContent(), "");
  assert.equal(posted.length, 2);
  const json = posted[1];
  const a = JSON.parse(json);
  assert.equal(a.v, 1);
  assert.match(a.cid, /^[0-9a-f-]{36}$/); // 送り直しで上書きするための端末の番号
  assert.equal(a.name, "テスト");
  assert.deepEqual(a.home, [32.506, 130.586]); // 検索で動かした真ん中のピンの位置（約100mに丸め）
  assert.deepEqual(a.days.map((d) => d.label), ["いつもの平日", "いつもの休日", "地震の日", "地震の後の日"]);
  assert.deepEqual(a.days.map((d) => d.date), [null, null, "2026-07-27", "2026-07-29"]);
  assert.equal(a.days[2].copied_from, "いつもの平日");
  assert.deepEqual(a.days[2].stays.map((s) => s.type), ["home", "food", "shop", "water", "home"]);
  assert.equal(a.days[3].copied_from, undefined);
  assert.deepEqual(a.days[1].stays.map((s) => [s.type, s.from, s.to]), [["home", "00:00", "09:00"], ["home", "11:00", "00:00"]]);
  assert.deepEqual([a.days[1].stays[1].lat, a.days[1].stays[1].lon], a.home); // 自宅を選ぶと家の位置になる
  const [h, shop, water, back] = a.days[0].stays;
  assert.deepEqual([h.type, shop.type, water.type, back.type], ["home", "shop", "water", "home"]);
  assert.deepEqual([h.lat, h.lon], a.home);
  assert.deepEqual([back.lat, back.lon], a.home);
  assert.deepEqual([h.to, shop.from, shop.to, water.from, water.to, back.from, back.to],
    ["07:45", "08:00", "08:45", "09:30", "10:00", "10:15", "00:00"]);
  assert.equal(shop.detail, "○○スーパー");
  assert.equal(shop.mode, "car");
  assert.equal(water.usual, false);
  assert.equal(water.heard, "neighbor");
  assert.equal(water.purpose, "水をもらいに");
  assert.equal(h.detail, undefined);
  const d1 = a.days[3];
  assert.equal(d1.date, "2026-07-29");
  assert.deepEqual(d1.stays.map((s) => s.type), ["home", "water", "car_sleeping"]);
  assert.deepEqual([d1.stays[2].from, d1.stays[2].to], ["20:00", "06:00"]);
  await page.screenshot({ path: `${SHOTS}/06-send.png` });
  await page.reload(); // 開き直しても、送信済みと出る
  await page.locator("#sent-msg", { hasText: "送信済み" }).waitFor();
  ok(`6 送信できる。失敗したら知らせ、もう一度押せる。送る内容が設計の形で、家が小数3桁（${json.length}文字）`);

  // 7. 日を足す・開き直すと続きから・消去
  await press(page, "#back");
  await press(page, "#more-yes");
  assert.equal(await title(page), "追加の日は、何月何日ですか？");
  assert.equal(await page.locator("#del-day").isVisible(), true);
  await page.reload();
  await page.locator("#map canvas").waitFor({ state: "attached" });
  assert.equal(await title(page), "追加の日は、何月何日ですか？");
  await press(page, "#del-day");
  assert.equal(await title(page), "この日の動きを確かめてください");
  await next(page);
  await press(page, "#more-no");
  await page.locator("#clear").click();
  await page.waitForLoadState("load");
  await page.locator("#name").waitFor();
  assert.equal(await page.locator("#name").inputValue(), "");
  ok("7 日を足せて消せる。開き直すと続きから。この端末から消す で最初に戻る");
  await ctx.close();

  // 360px 幅・ダークモードの見た目（目で確かめる用）
  const dark = await open(browser, { colorScheme: "dark", viewport: { width: 360, height: 740 } });
  await dismiss(dark.page);
  await dark.page.locator("#name").fill("夜");
  await next(dark.page);
  await dark.page.waitForTimeout(1500);
  await dark.page.screenshot({ path: `${SHOTS}/08-dark.png` });
  assert.equal(await dark.page.evaluate(() => document.documentElement.scrollWidth), 360);

  ok("8 360px で横にはみ出さない");

  // 9. 回答を見るページ: 合言葉で全員分を読み、人と日を選ぶと地図・帯・表に出る
  const view = await open(browser, { viewport: { width: 1280, height: 800 } }, URL + "view.html");
  await view.page.route("https://script.google.com/**", (r) => {
    const body = JSON.parse(r.request().postData());
    r.fulfill({ json: body.key !== "secret" ? { ok: false, error: "key" } : { ok: true, answers: [
      { received: "2026-11-04T01:00:00.000Z", answer: a },
      { received: "2026-11-05T01:00:00.000Z", answer: { ...a, name: "二人目", days: a.days.slice(0, 1) } },
    ] } });
  });
  await view.page.locator("#key").fill("ちがう");
  await view.page.locator("#login button").click();
  await view.page.locator("#login-err", { hasText: "合言葉が違います" }).waitFor();
  await view.page.locator("#key").fill("secret");
  await view.page.locator("#login button").click();
  await view.page.locator("#app").waitFor();
  assert.equal(await view.page.locator("#count").textContent(), "2人");
  assert.equal(await view.page.locator(".people button").first().locator("b").textContent(), "二人目"); // 新しい順
  await view.page.locator(".people button").nth(1).click();
  assert.equal(await view.page.locator(".tab").count(), 4);
  assert.equal(await view.page.locator("#rows tr").count(), 4);
  assert.equal(await view.page.locator(".pt--new").count(), 1); // 給水（ふだんは行かない）
  await view.page.locator(".tab").nth(3).click();
  assert.equal(await view.page.locator("#rows tr").count(), 3);
  assert.equal(await view.page.locator(".pt").count(), 3);
  await view.page.waitForTimeout(1500);
  await view.page.screenshot({ path: `${SHOTS}/09-view.png` });
  const [download] = await Promise.all([view.page.waitForEvent("download"), view.page.locator("#download").click()]);
  assert.match(download.suggestedFilename(), /^survey-answers-\d{4}-\d{2}-\d{2}\.json$/);
  await view.page.reload(); // 合言葉はタブを閉じるまで覚える
  await view.page.locator("#app").waitFor();
  ok("9 回答を見るページ: 合言葉で読み、人と日ごとに地図・帯・表、まとめて保存");

  await browser.close();
  assert.deepEqual(errors, []);
  ok("10 Console にエラーなし");
})().catch((e) => {
  console.error("FAILED:", e.message);
  console.log("Console のエラー: " + (errors.length ? "\n" + errors.join("\n") : "なし"));
  process.exit(1);
});
