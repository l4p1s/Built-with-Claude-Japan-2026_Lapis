// 検知 → 地図 → 質問 → 回答 → 確認済み を通して確かめる（画面の確認は scripts/check/ui-check.js と同じ動かし方）。
// 先に scripts/synth/simulate.py で投入し、docker compose exec api python -m app.detect_job で候補を作っておく。
//   NODE_PATH=<playwright-core の場所> node scripts/check/e2e-check.js <候補の lat> <候補の lon> <スクリーンショットの置き場>
//   URL=http://localhost:18082/ のように、確かめるサーバーを変えられる
//   DEMO=1 を付けると、位置を使わないデモ（/?demo=1&at=lat,lon）で同じことを確かめる（サーバーも DEMO=1 で起動しておく）
// 候補の場所: docker compose exec db psql -U app -c "select ST_Y(center::geometry), ST_X(center::geometry) from candidates where source='detected' and status='pending'"
const { chromium } = require("playwright-core");
const [lat, lon, shots] = [Number(process.argv[2]), Number(process.argv[3]), process.argv[4]];
const URL = process.env.URL ?? "http://localhost:18080/";
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.HOME + "/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome" });
  const demo = Boolean(process.env.DEMO);
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, locale: "ja-JP",
    ...(demo ? {} : { geolocation: { latitude: lat, longitude: lon }, permissions: ["geolocation"] }),
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(e.message));
  if (demo) {
    // 最初（8:00）は何も出ていない
    await page.goto(`${URL}?demo=1&at=${lat},${lon}&from=08:00&controls=1`);
    await page.locator("#consent-ok").click();
    await page.locator(".me").waitFor();
    await page.locator("#replay-play").click(); // 止める
    await page.waitForTimeout(1500);
    console.log("pins at 08:00:", await page.locator(".pin").count());
    await page.screenshot({ path: `${shots}/e2e-demo-start.png` });
    await page.locator("#replay-play").click(); // 再生（1 秒 = 5 分）
  } else {
    await page.goto(URL);
    await page.locator("#consent-ok").click();
    await page.locator(".me").waitFor();
  }
  await page.waitForFunction(() => !document.querySelector("#question").hidden, null, { timeout: 90000 });
  await page.waitForTimeout(2500);
  console.log("question:", await page.textContent("#question-title"));
  await page.screenshot({ path: `${shots}/e2e-question${demo ? "-demo" : ""}.png` });
  await page.locator("#question-kinds button").first().click();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${shots}/e2e-answered${demo ? "-demo" : ""}.png` });
  console.log("errors:", errors);
  await browser.close();
})();
