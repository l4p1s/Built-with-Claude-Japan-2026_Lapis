// デモの画面を1コマずつ撮って動画にする（docs/scenario.md §6 の撮影 D1〜D3、横長の静止画 M1・M2）。
// 画面録画だと GPU のない機械では点の動きがカクつくので、時刻を window.demoSeek で直接指定して1コマずつ撮る。
//   DEMO=1 docker compose up -d のうえで
//   NODE_PATH=<playwright-core の場所> node scripts/check/capture-demo.js <D1|D2|D3|M1|M2> <出力の mp4>
//   URL= で撮る画面のサーバー（既定は http://localhost:18080/）、CHROME= でブラウザの実行ファイルを指定できる
// 時刻は keys（[動画の秒, デモの分] の折れ線）で決める。CSS のアニメーション（ピンが弾む、シートが上がる）も1コマ分ずつ進める。
// 画面はスマホの幅 390×844 を 3 倍の密度（1170×2532）で撮る。view があるショットは横長で撮り、地図の上のボタンや帯を消す。ffmpeg が要る
// 構図を確かめるだけなら STILLS=5,6.5 のように秒を渡すと、その秒の静止画（<出力>-<秒>.png）だけを撮る
const { chromium } = require("playwright-core");
const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const FPS = 30;
const SHOTS = {
  // A07: 10:16 から毘舎丸団地の前に人が集まり、10:40（6 秒）に「？」が立つ。団地は施設の印が出ないので、集まる点が隠れない
  // 横長 640×360 を 3 倍の密度（1920×1080）で撮る。人の点が動画で大きく見えるように
  D1: { query: "from=10:10&at=32.51019,130.61329&zoom=16.3", keys: [[0, 616], [6, 640], [12, 652]], hideQuestion: true, view: [640, 360, 3] },
  // 2本目 B01〜B04: 8:00 は何もない → 16.5 秒（8:50）に古城町団地の「？」と質問のカード → 27 秒に「給水」と答える → 35.4 秒（9:10）に公式のピン（代陽コミュニティセンター）→ 38 秒にその詳細
  // 見る人が追えるように、「？」が立った後（16.5–20 秒）と質問のカード（20–27 秒）では時刻をほぼ止める
  D2: {
    query: "from=08:00&at=32.5021,130.59743&zoom=14.2",
    keys: [[0, 480], [6, 480], [13.5, 525], [16.5, 530], [20, 530], [27, 530.5], [32, 533], [35.4, 550], [36, 551], [48, 553]],
    actions: [
      [27, (page) => page.locator("#question-kinds button", { hasText: "給水" }).click({ force: true })],
      [38, (page) => page.locator('.pin[data-key="代陽コミュニティセンター"]').click({ force: true })],
    ],
  },
  // S18: 台本の範囲の全体で 11:00 → 21:00 を早送り。中心の近くの集まりの質問のカードは出さない
  D3: { query: "from=08:00", keys: [[0, 660], [10, 1260]], hideQuestion: true },
  // A02・A10 の背景（横長の静止画。STILLS=0 で撮る）: 8:00 の市役所の周り / 21:00 の台本の範囲の全体
  M1: { query: "from=08:00&at=32.5072,130.6016&zoom=14.6", keys: [[0, 480], [1, 480]], hideQuestion: true, view: [1920, 1080, 1] },
  M2: { query: "from=08:00&zoom=13.4", keys: [[0, 1260], [1, 1260]], hideQuestion: true, view: [1920, 1080, 1] },
};

// 動画の秒 s のときのデモの時刻（分）
function minuteAt(keys, s) {
  for (let i = 1; i < keys.length; i++) {
    const [s0, t0] = keys[i - 1];
    const [s1, t1] = keys[i];
    if (s <= s1) return t0 + ((t1 - t0) * (s - s0)) / (s1 - s0);
  }
  return keys.at(-1)[1];
}

(async () => {
  const [name, out] = process.argv.slice(2);
  const shot = SHOTS[name];
  if (!shot || !out) throw new Error("使い方: node scripts/check/capture-demo.js <D1|D2|D3|M1|M2> <出力の mp4>");
  const browser = await chromium.launch({ executablePath: process.env.CHROME || undefined });
  const [width, height, dpr] = shot.view ?? [390, 844, 3];
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: dpr, locale: "ja-JP" });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${process.env.URL ?? "http://localhost:18080/"}?demo=1&paused=1&${shot.query}`);
  await page.locator("#consent-ok").click();
  await page.locator(".me").waitFor();
  if (shot.hideQuestion) await page.addStyleTag({ content: "#question { display: none !important; }" });
  // 横長は動画の地図の絵にするので、地図の上のボタンや帯を消す（地図の出典は動画の側で出す）
  if (shot.view) await page.addStyleTag({ content: ".top, .chips, .dock, .credits, .replay, .me, #menu-btn, #locate, .maplibregl-ctrl-bottom-left, .maplibregl-ctrl-bottom-right { display: none !important; }" });
  await page.waitForFunction(() => typeof window.demoSeek === "function");
  await page.evaluate(() => new Promise((r) => setTimeout(r, 2000))); // 背景の地図の読み込みを待つ

  const frames = Math.round(shot.keys.at(-1)[0] * FPS);
  const actions = [...(shot.actions ?? [])];
  const stills = process.env.STILLS?.split(",").map((s) => Math.round(Number(s) * FPS));
  const dir = stills ? null : fs.mkdtempSync(path.join(os.tmpdir(), `capture-${name}-`));
  const started = Date.now();
  for (let f = 0; f < frames; f++) {
    while (actions.length && actions[0][0] * FPS <= f) await actions.shift()[1](page);
    if (stills && !stills.includes(f)) continue;
    await page.evaluate(
      async ([t, fps, still]) => {
        await Promise.race([window.demoSeek(t), new Promise((r) => setTimeout(r, 3000))]);
        // CSS のアニメーションを実時間ではなく、撮ったコマ数で進める（静止画だけのときは終わった状態にする）
        for (const a of document.getAnimations()) {
          if (still) {
            a.finish();
            continue;
          }
          a.pause();
          a.__frame = (a.__frame ?? -1) + 1;
          a.currentTime = (a.__frame * 1000) / fps;
        }
      },
      [minuteAt(shot.keys, f / FPS), FPS, Boolean(stills)],
    );
    await page.screenshot({ path: stills ? `${out}-${f / FPS}.png` : path.join(dir, `${String(f).padStart(5, "0")}.png`) });
    if (f % 30 === 0) console.log(`${name}: ${f}/${frames}（${((Date.now() - started) / 1000).toFixed(0)} 秒）`);
  }
  await browser.close();
  if (stills) return console.log(`静止画 ${stills.length} 枚`, errors.length ? `エラー: ${errors}` : "");
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-framerate", String(FPS), "-i", path.join(dir, "%05d.png"),
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-g", String(FPS), out]);
  fs.rmSync(dir, { recursive: true });
  console.log(`${out}: ${frames} コマ`, errors.length ? `エラー: ${errors}` : "");
})();
