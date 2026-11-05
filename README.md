# あるばい

> 人の動きが、いちばん速い災害情報になる — GPS で、災害時の非公式な支援拠点を見つける

**あるばい**（熊本弁で「あるよ」）は、災害時にアプリ利用者の匿名の GPS から「いつもと違う人の集まり」を見つけ、公式のリストに載らない支援拠点（お店や個人の炊き出し・物資の配布、ボランティアの給水、車中泊の集まり）を、その場にいる人に聞いて確かめ、地図に載せる仕組みです。

- 中身: API（FastAPI）+ DB（PostgreSQL / PostGIS）+ 検知の定期処理 + Web 画面（MapLibre GL）。`docker compose up` だけで動きます
- 企画の芯・検証の数字・出典は [CLAUDE.md](CLAUDE.md)。この README の数字はそこから引いています
- ライセンス: MIT（データは各出典の条件に従います）

> **このブランチは `demo`**: main に、発表・審査・動画用のデモ（合成データの 1 日を画面で早回し再生する機能、動画の素材、撮影スクリプト、紹介動画のシナリオ）を足したものです。デモの動かし方は [8.8](#88-デモこのブランチだけ)。アプリ本体の説明は main と同じです。

## 目次

1. [背景](#1-背景)
2. [課題](#2-課題)
3. [提案](#3-提案)
4. [全体の流れ](#4-全体の流れ)
5. [システム構成](#5-システム構成)
6. [アーキテクチャ](#6-アーキテクチャ)（API・DB・位置データ・検知・候補の状態・セキュリティ）
7. [検知の結果](#7-検知の結果)
8. [動かし方](#8-動かし方)（デモは [8.8](#88-デモこのブランチだけ)）
9. [ディレクトリ構成](#9-ディレクトリ構成)
10. [ブランチ](#10-ブランチ)
11. [限界と今後](#11-限界と今後)
12. [文書](#12-文書)
13. [参考文献](#参考文献)
14. [ライセンス](#ライセンス)

---

## 1. 背景

発案者は熊本県八代市の出身です。2016年の熊本地震と2025年8月の八代市の豪雨で自身が被災し、2026年7月の熊本地震では実家と友人が被災しました。

- 一番欲しかったのは **「今、どこで水と食べ物がもらえるか」**。開いているお店、給水車の場所、炊き出しや配布。どれも時間とともに変わる情報です
- 本当に助かったのは、**お店が店の前でやっていた炊き出しや商品の配布**でした。自主的な活動なので、市役所やテレビなどの**公式からは出てきません**。知る方法は **SNS とお隣さんだけ**でした
- その情報も人づて頼みでした。Instagram のストーリーで「知っていたら教えて」と頼む、炊き出しをする友人が「拡散して」と頼む、隣の家の人が教えてくれる
- 八代市役所も被災して機能を果たせず、避難所も被災して別の場所が開設されました

公開されている記録でも、公式の情報だけでは避難や支援の実態がつかめていません。

| 事実 | 出典 |
|---|---|
| 2016年熊本地震: 避難者の 68.3% が車中泊。指定避難所に最も長くいた人は 16.8% | [熊本県 県民アンケート調査 結果報告書](https://www.pref.kumamoto.jp/uploaded/attachment/50130.pdf) |
| 2026年熊本地震: 避難指示 25.8万人に対し、避難所は 8,698人（7/29）。車中泊などは把握されていない | [熊本県 被害情報 第1報](https://www.pref.kumamoto.jp/uploaded/life/282503_903848_misc.pdf)、時事通信 2026/8/2 |
| 2026年 八代市: 公式の給水拠点は 1日目 4か所 → 49日間増え続けて 44か所。終了日時の記載は 44か所中 4か所 | [名古屋大学 応急給水拠点マップ（防災科研 bosaiXview）](https://www.bosai.go.jp/info/saigai/2026/20260729.html) |
| 2026年 熊本県全体: 公式の給水拠点 79か所の情報は 8つの自治体サイトに分散し、呼び方は 11通り | 同上 |
| 能登半島地震: 約2人に1人が「情報を得られる人と得られない人で差があった」 | [金沢大学 × LINEヤフー](https://www.lycorp.co.jp/ja/news/release/018094/) |

ほかの出典つきの事実は [CLAUDE.md](CLAUDE.md) §5。

## 2. 課題

1. **情報が一元化されていない** — 公式は公式、非公式は SNS とお隣さん
2. **つながりがないと届かない** — 「教えて」「拡散して」頼み

## 3. 提案

```
 ① 人の動き        ② 自動で検知           ③ 現地に聞く                 ④ 地図に表示
 匿名の GPS  ───▶  いつもと違う    ───▶  その場の人に             ───▶  非公式の拠点
                   集まりを見つける       「ここに何がある？」             として共有
```

なぜ人の動きなのか:

- **誰も投稿しなくていい** — 配る人の発信を待たない
- **つながりがなくていい** — 知り合いがいなくても見つかる
- **集まった時点でわかる** — SNS の拡散より速い

誰がうれしいか:

| 立場 | うれしいこと |
|---|---|
| 被災者 | つながりがなくても、近くの給水・炊き出しが見つかる |
| 配る人（店・ボランティア） | 投稿や拡散のお願いをしなくても見つけてもらえる |
| 自治体・支援団体 | 公式外の拠点や車中泊の集まりを把握し、物資と人の配分に使える |

使われ方の例（想定）:

```
 9:00   ボランティアが公園で給水を始める（SNS への投稿なし）
 9:30〜 アプリの利用者が立ち寄る
10:30   自動で検知し、近くの利用者に「ここに何がある？」と聞く
10:35   「給水」と回答があり、地図に載る
```

## 4. 全体の流れ

```
① スマホ（Web 画面）── GPS を 30 秒に 1 点記録し、60 秒ごとにまとめて送る
      │  POST /v1/locations
      ▼
② API（FastAPI）── 対象地域の外の点・時刻のおかしい点を捨てて保存
      │
      ▼
③ DB（PostGIS）── location_points。14 日で消す
      │  5 分ごと（api の中のスレッド）
      ▼
④ 検知（app/detect.py）── その人の「いつもと違う滞在」を 500m × 30 分で数え、ポアソンで判定
      │
      ▼
⑤ 候補「？」── candidates（status = pending）
      │  GET /v1/questions（候補から 500m 以内の人だけ）
      ▼
⑥ 現地の人に質問 ──「ここに何がある？」→ 給水・食べ物・物資・車中泊・避難・その他 / わからない / もう終わっていた
      │  POST /v1/candidates/{id}/answers
      ▼
⑦ 確認済み ── status = confirmed
      │  GET /v1/spots
      ▼
⑧ 地図 ── 非公式の拠点（確認済み・未確認）と公式の避難所・給水所を 1 枚に
```

利用者が自分から「ここで配っている」と知らせることもできます（`POST /v1/reports`）。そのときは ⑤ の候補が報告から生まれ、ほかの人の回答で確認済みになります。

## 5. システム構成

```
 +-------------------------+
 | smartphone / browser    |   Web 画面（web/）: 素の HTML/JS + MapLibre GL 6
 |  web/index.html, app.js |   地図のタイル: OpenFreeMap（CDN）
 +-----------+-------------+
             |  HTTPS（公開するとき。スマホで GPS を取るには HTTPS が必要）
             v
 +-----------+-------------+
 | ngrok                   |   ngrok http 18080（開発は localhost のまま）
 +-----------+-------------+
             |  http://localhost:18080
             v
 +--------------------------------- docker compose ---------------------------------+
 |                                                                                  |
 |  +-----------------------------------+        +------------------------------+   |
 |  | api  (python 3.12 + uv)           |  SQL   | db                           |   |
 |  |  - FastAPI   /v1/*, /docs         | -----> | PostgreSQL 17 + PostGIS 3.5  |   |
 |  |  - static    web/  (same origin)  |        | schema: db/init.sql          |   |
 |  |  - thread    detect_job (300 s)   |        | volume: dbdata               |   |
 |  +-----------------------------------+        +------------------------------+   |
 |                                                                                  |
 +----------------------------------------------------------------------------------+

 GitHub
   - Actions（.github/workflows/ci.yml）: PostGIS を立てて ruff と pytest
   - Pages: survey ブランチの /docs から記入フォームを配信（本体とは別）
```

- 画面は API と同じオリジンから配るので、CORS の設定は要りません。公開は `ngrok http 18080` の 1 本です
- 検知は API のプロセスの中のスレッドで回します。キューやスケジューラーは入れていません（ワーカーは 1 つで動かす前提）
- デモ（`DEMO=1`、このブランチだけ）は `web/demo/` の素材をブラウザの中で再生するだけで、API・DB・検知スレッドは使いません
- 選定の理由（Python + FastAPI、PostGIS、Bearer トークン など）は [docs/architecture.md](docs/architecture.md)

## 6. アーキテクチャ

### 6.1 部品と技術

| 部品 | 技術 | ファイル |
|---|---|---|
| API | Python 3.12 / FastAPI / psycopg 3（生 SQL、ORM なし） | `app/main.py`（ルーティング）、`app/spots.py`（候補・回答・公式拠点の読み書き） |
| 認証 | アカウントなしの匿名セッション。Bearer トークン、DB には SHA-256 だけ | `app/auth.py` |
| ルール | 確定・終了・表示の数値、備考の検査、対象地域 | `app/rules.py` |
| 回数制限 | プロセス内のスライディングウィンドウ | `app/ratelimit.py` |
| 検知 | 純関数（滞在・学習・集計・ポアソン） | `app/detect.py`、`app/mesh.py`（地域メッシュの番号） |
| 定期処理 | DB から読んで検知し `candidates` に書く。古いデータを消す | `app/detect_job.py` |
| DB | PostgreSQL 17 + PostGIS 3.5 | `db/init.sql` |
| 画面 | 素の HTML/CSS/JS（ビルドなし）+ MapLibre GL 6 + OpenFreeMap。PWA | `web/` |
| 道具 | uv、ruff、pytest、Playwright（画面の自動確認） | `pyproject.toml`、`scripts/check/` |

### 6.2 API

`/v1` を付けて版を管理しています（ネイティブアプリは強制更新できないため）。仕様書は起動後に http://localhost:18080/docs （OpenAPI: `/openapi.json`）。

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/v1/sessions` | 同意と今の位置を受けてセッションを発行 → `{token, expires_at}`。対象地域の外なら 403 |
| DELETE | `/v1/sessions/me` | 参加をやめる。位置・回答・通報・自分だけが知らせたピンを消す |
| POST | `/v1/locations` | 位置をまとめて送る（1〜1000 点）→ 202 |
| GET | `/v1/questions?lat=&lon=` | 近くの未確認の候補 =「ここに何がある？」 |
| POST | `/v1/candidates/{id}/answers` | 回答（種類 + 備考 100 文字まで）。今の位置が要り、500m より遠いと 403 |
| POST | `/v1/reports` | 自分の位置で拠点を知らせる。近くに候補があれば合流 |
| POST | `/v1/answers/{id}/flags` | 備考を不適切として通報 |
| GET | `/v1/spots?bbox=&at=` | 非公式のピンと公式の拠点を 1 つの GeoJSON で。`at` でその時点に開いていた公式拠点 |
| GET | `/healthz` | 死活確認 |

`/v1/sessions` と `/healthz` 以外は、閲覧も書き込みもセッションが必要です。

### 6.3 DB の表

```
                    +------------------+
                    | sessions         |   匿名の参加（トークンは SHA-256 だけ）。30 日使わないと消える
                    +--------+---------+
                             |
          +------------------+-------------------+
          | 1:N              | 1:N               | 1:N
 +--------+--------+  +------+-------+   +-------+------+
 | location_points |  | answers      |   | flags        |   flags = 備考の通報（1 人 1 備考 1 回）
 +-----------------+  +--+--------+--+   +-------+------+
   14 日で消す           | N:1    | 1:N          |
                         |        +--------------+
                  +------+-------+
                  | candidates   |   非公式の拠点の候補（detected = 検知 / report = 報告）
                  +--------------+

 +-----------------+
 | official_spots  |   公式の避難所・給水所（取り込み）。ほかの表とは結ばない
 +-----------------+
```

| 表 | 主な列 | 備考 |
|---|---|---|
| `sessions` | id, token_hash, consented_at, last_seen_at | 名前・電話・端末 ID は持たない |
| `location_points` | session_id, geom（geography Point 4326）, accuracy_m, recorded_at | GiST 索引。14 日で消す |
| `candidates` | source, center, status（pending / confirmed / ended）, last_activity_at, cell_code, window_start, observed, expected, p_value | 検知の列は detected のときだけ |
| `answers` | candidate_id, session_id, kind, note | 1 人 1 候補 1 件（上書き） |
| `flags` | answer_id, session_id | 通報 2 件で備考を出さない |
| `official_spots` | source, source_key, name, kind（shelter / water）, geom, opened_at, closed_at | source ごとに上書き |

削除はすべて `ON DELETE CASCADE` でつながっています。セッションを消すと、その人の位置・回答・通報も消えます。

### 6.4 位置データの形式と流れ

端末がためて、まとめて送ります。電池と電波を節約でき、圏外でも後から送れます。検知は 5 分ごとなので、秒単位の即時性は要りません。

| 項目 | 決めたこと |
|---|---|
| 座標 | WGS84（EPSG:4326）の 10 進の度。`lat`・`lon`、小数 6 桁まで |
| 精度 | `accuracy_m`（端末が返す誤差の半径 m。任意） |
| 時刻 | `recorded_at`。ISO 8601 で**時差つき**（例 `2026-07-29T12:30:00+09:00`）。時差なしは 422。5 分より未来・14 日より古い点は捨てる |
| 間隔（Web 画面） | 30 秒に 1 点を記録し、60 秒ごとにまとめて送る。送れなければ最大 1000 点ためる |
| 保存 | `location_points` に 14 日 |
| 外に出るもの | 候補（マス・時間帯・人数）だけ。個人の軌跡や自宅は出さない |

```json
POST /v1/locations
Authorization: Bearer <token>

{"points": [{"lat": 32.5072, "lon": 130.6016, "accuracy_m": 12, "recorded_at": "2026-07-29T12:30:00+09:00"}]}
```

ネイティブアプリや LINE ミニアプリも同じ `POST /v1/locations` を使う前提です（Web 画面は閉じると位置が取れないため）。詳しくは [docs/architecture.md](docs/architecture.md) §4.1。

### 6.5 検知の手順

**その人にとって「いつもと違う場所」に 10 分以上とどまった人を、同じ場所・同じ時間帯で数えます。**
災害時は「場所ごとに平常時の人数と比べる」方式が崩れるので（街全体が一斉に「いつもより多い」になる）、比べる相手を「場所の平常」から「その人の平常」に変えています。

```
location_points（1 人ずつの点列）
   │ ① 滞在を取り出す: 100m 以内に 10 分以上
   ▼
滞在（場所・開始・終了）
   │ ② その人の「いつも」を学ぶ（直近 7 日。24 時間に 1 回）
   │      自宅         = 3:00 の位置の中央値。周り 200m は数えない
   │      いつもの場所 = 2 日以上行った場所（150m でまとめる）
   │      探索型       = 1 回だけの場所が 6 か所以上 → 重み 0.5
   ▼
いつもと違う滞在（自宅の周りでも、いつもの場所でもない）
   │ ③ 500m メッシュ × 30 分（10 分ずらし）で重みを足す → 人数 k
   ▼
マス × 時間帯の人数 k
   │ ④ 期待値 λ = その時間帯の街全体の「いつもと違う滞在」の総数 × そのマスの平常時の割合（下限 0.3）
   │ ⑤ ポアソンの上側確率 P(X ≥ k | λ) < 1/1000 かつ k ≥ 5 → 候補
   ▼
candidates（150m 以内に生きている候補があれば更新。報告の候補なら 1 人ぶんの裏付けに数える）
```

```
 例: 500m メッシュ × 30 分                ④ の肝: 街全体の量で基準が動く
 +-----+-----+-----+
 |  0  |  1  |  0  |                      平常時の割合 1% のマスに 8 人
 +-----+-----+-----+                        街全体が 60 件   → λ = 0.6 → P ≈ 2.4×10⁻⁷ → 候補
 |  1  |  8  |  0  |  <- k = 8              街全体が 2,000 件 → λ = 20  → P ≈ 0.999   → 候補にしない
 +-----+-----+-----+
 |  0  |  0  |  1  |                      災害日に多くの人が出歩いても、どこでも「多い」とは言わない
 +-----+-----+-----+
```

- 5 人の下限は「少人数の集まりは表示しない」（プライバシー）のため
- 検知は候補を出すところまで。正体は現地の人に聞いて確かめるので、基準は緩めでよい
- AI（LLM・深層学習）は使っていません。ポアソンの式 1 本で説明できます
- 定数の一覧・根拠・採らなかった方式は [docs/algorithm.md](docs/algorithm.md)

### 6.6 候補の状態

```
 [検知] 5 人以上・p < 1/1000 ──┐
                               ├──▶ pending（未確認 ?）── 種類の回答が足りる ──▶ confirmed（確認済み）
 [報告] POST /v1/reports ──────┘          │                                           │
                                          └─── 「もう終わっていた」≧「やっている」 ───┴──▶ ended（終了）
```

| ルール | 値（`app/rules.py`） |
|---|---|
| 確認済みに必要な、種類を答えた人数 | 検知 1 人 / 報告 2 人（報告者を含む）。報告に検知が重なれば 1 人ぶんの裏付け |
| 終了 | 「もう終わっていた」が「やっている」以上 |
| 質問する範囲 | 候補から 500m |
| 報告をまとめる範囲 | 100m |
| 表示 | 最後の確認から 3 時間で薄く、6 時間で地図から消す |

### 6.7 セキュリティとプライバシー

| 守りたいこと | 入れている守り |
|---|---|
| 位置と移動を外に出さない | アカウントなし・トークンはハッシュだけ / 位置は 14 日で消す / 外に出るのはマス × 時間帯 × 人数だけ / 自宅の周り 200m は数えない / 5 人未満は候補にしない / 学習結果はメモリに 24 時間だけ / 参加をやめると自分のデータが消える |
| 嘘の拠点を載せさせない | 候補から 500m 以内の人だけ回答 / 1 人 1 候補 1 票 / 自分の報告は自分で確かめられない / 終了は多数決 / 備考は URL・電話番号不可、通報 2 件で非表示 / 6 時間で消える |
| サーバーを止めさせない | 回数制限（参加 20 回/時/IP、位置 10 回/分、回答・報告・通報 60 回/時）/ 位置は 1 回 1000 点まで / 入力の検査 / SQL はすべてパラメータ |
| 対象地域の外から荒らさせない | `AREA` の中の人だけ参加でき、外の位置・報告は受け付けない |

守れていないこと（GPS の偽装、複数端末での多重参加、回数制限がプロセス内であること など）と運用での注意は [docs/security.md](docs/security.md)。

## 7. 検知の結果

2 種類の数字があります。**混ぜないでください。**

### 7.1 実データでの検証（YJMob100K。CLAUDE.md §4）

実データ: LINEヤフーの YJMob100K（日本のある都市、2.5万人 × 75 日、うち 15 日が緊急期、500m・30 分単位）。

| 検証 | 結果 |
|---|---|
| 既存方式の限界 | 緊急期は街全体が一斉に「いつもより多い」（63 日目 6〜9 時: 多いマス 1847、少ないマス 2）。場所ごとの比較では区別できない |
| 誤報の確認 | 新しい場所への滞在の割合が平日の約 24% から約 44%（約 1.8 倍）に増えても、誤報は増えない |
| 精度の測定 | 実際の 1 日に場所と時刻を決めた集まりを埋め込み、1 日 3 件を人が確認する前提で、**アプリ利用者 10 人の集まりを 97〜98% 検知、検知まで中央値 90 分** |

この検証のコードは、まだこのリポジトリに入っていません（データの入手先は [CLAUDE.md](CLAUDE.md) §7）。

### 7.2 合成データでの答え合わせ（八代市の縮図）

このリポジトリの `app/detect.py` を、八代市の合成データ（1000 人 = 市民の縮図、1 分刻み、平常 14 日 + 地震当日 + 2 日）で答え合わせした結果です。**合成データの行動は仮定が多く、実際の災害時の精度を意味しません。**

| | 平常期で学習 | 当日だけ |
|---|---|---|
| 調整用（seed 1） | 31/31 検知、誤報 1、中央値 30 分 | 31/31 検知、誤報 6 |
| 測定用 3 本の合計（seed 2〜4） | **93/93 検知、誤報 3（1 日 0.3 件）、中央値 30 分** | 93/93 検知、誤報 15（1 日 1.7 件） |

参加率（アプリを入れた人の割合）を変えたとき（seed 1〜4 の 4 本、公式を除く 31 件 × 4）:

| 集まりの人数 | 5% | 10% | 20% | 50% | 100% |
|---|---|---|---|---|---|
| 〜29 人 | 0/80 | 0/80 | 6/80 | 60/80 | 80/80 |
| 30〜59 人 | 0/28 | 1/28 | 9/28 | 27/28 | 28/28 |
| 60 人〜 | 2/16 | 7/16 | 15/16 | 16/16 | 16/16 |

- 目安は **集まりの人数 × 参加率 ≥ 5**。参加率 5% なら 100 人規模、20% なら 25 人規模から見え始める
- 平常期のデータが要るのは誤報を減らすためだけ（当日だけでも集まりは見つかる）
- 詳しくは [docs/algorithm.md](docs/algorithm.md) §4

## 8. 動かし方

### 8.1 起動とテスト

Docker（Compose）だけあれば動きます。

```sh
docker compose up -d --build --wait     # api と db（PostgreSQL + PostGIS）を起動
curl localhost:18080/healthz             # {"ok":true}
docker compose exec api pytest -q       # テスト 61 件（実 DB を使う）
docker compose exec api ruff check .
```

- Web 画面: http://localhost:18080 。スマホ実機で GPS を使うには HTTPS が要るので `ngrok http 18080` で公開します
- 参加は対象地域（既定は八代市役所から 25km）の中からだけです。ほかの場所で試すなら `AREA` を変えるか空にします
- DB を初期化し直す: `docker compose down -v`（取り込んだデータも消えます）
- CI: push と pull request で `.github/workflows/ci.yml` が ruff と pytest を回します
- デモ（このブランチだけ）: `DEMO=1 docker compose up -d` で起動して `http://localhost:18080/?demo=1`（8.8）

### 8.2 環境変数

| 変数 | 既定 | 意味 |
|---|---|---|
| `DATABASE_URL` | `postgresql://app:app@db:5432/app` | DB の接続先。公開運用ではパスワードを差し替える |
| `AREA` | `32.5072,130.6016,25000` | 対象地域 `緯度,経度,半径m`。中にいる人だけが参加でき、外の位置・報告は受け付けない。空にすると制限なし |
| `DETECT_EVERY_S` | `300` | 検知の間隔（秒）。`0` で止める（テスト） |
| `RATE_LIMIT` | （有効） | `off` で書き込みの回数制限を止める（テスト・開発） |
| `DEMO` | （空） | `1` で `web/demo/` を配信し、`/?demo=1` のデモ再生が動く（このブランチだけ） |

例: `AREA= docker compose up -d`（制限なし）、`AREA=32.80,130.71,20000 docker compose up -d`（別の地域）。

### 8.3 公式の拠点を取り込む

```sh
docker compose exec api python scripts/import_bodik.py                       # 八代市の避難所（BODIK 令和8年熊本地震）
docker compose exec api python scripts/import_official.py data/official/<file>.csv --source <名前>   # ほかの地域・給水所（CSV）
```

CSV の列は [data/official/README.md](data/official/README.md)。何度実行しても重複しません（source ごとに上書き）。

### 8.4 合成データで検知を通す（API 経由）

```sh
python scripts/synth/make_demo_data.py --out data/out     # 八代市 1000 人 × 1 分 × 17 日（numpy が要る。約 3 分）
python scripts/synth/simulate.py --people 300 --days 8    # API 経由で投入。「地震の翌日 = 今日」に日付をずらす（約 3 分）
docker compose exec api python -m app.detect_job          # 今の時刻で検知（5 分ごとにも自動で回る）
```

地図に `?` のピンが出て、その近くで画面を開くと「近くで人が集まっています」と聞かれます。答えると確認済みになります。

### 8.5 答え合わせ・参加率・ヒートマップ

```sh
python scripts/synth/detect_demo.py --out data/out        # 検知と答え合わせ → data/out/detect_result.md
python scripts/synth/detect_demo.py --no-baseline         # 平常期を使わない「当日だけ」版
python scripts/synth/detect_demo.py --sample 0.1          # 参加率 10% の見立て
python scripts/synth/participation.py                     # 参加率 × 集まりの人数の表
python scripts/synth/make_heatmap.py && python3 -m http.server 18091 -d data/out   # → http://localhost:18091/heatmap.html
```

合成データの作り方と仮定は [docs/demo-data-design.md](docs/demo-data-design.md)、入力データの出典は [data/README.md](data/README.md)。

### 8.6 画面の自動確認（Playwright）

- `scripts/check/ui-check.js`: 画面の自動確認
- `scripts/check/e2e-check.js`: 検知 → 地図 → 質問 → 回答 → 確認済み を通した確認（`DEMO=1` ならデモ再生の確認）
- `scripts/check/capture-demo.js`: デモを 1 コマずつ撮る（このブランチだけ）

使い方はそれぞれのファイルの冒頭にあります。

### 8.7 地図の背景の施設を作り直す

`python scripts/make_places.py` → `web/places.geojson`（八代市の店・病院・役所・学校・公園など。[docs/web-ui-design.md](docs/web-ui-design.md) §1.7）。

### 8.8 デモ（このブランチだけ）

発表・審査・動画のために、合成データの「地震の翌日」を画面で早回し再生します。**見た目は通常の画面と同じ**で、API・DB は使いません。位置も使わず、**地図の中心が「自分の位置」**です。

```sh
DEMO=1 docker compose up -d --build --wait    # DEMO=1 のときだけ web/demo/ を配信する
# → http://localhost:18080/?demo=1 を開く（公開するなら ngrok http 18080）
```

```
 scripts/synth/demo_scenario_video.json      台本（八代市役所の周りの集まり。家もこの範囲だけに置く）
      │  make_demo_data.py
      ▼
 data/demo/                                  1,000 人 × 1 分の合成データ（コミットしない）
      │  detect_demo.py（app/detect.py で検知と答え合わせ）
      ▼
 検知の候補の時系列
      │  make_demo_assets.py
      ▼
 web/demo/timeline.json, people.bin          候補の時系列 + 1,000 人の位置（5 分おき）
      │  DEMO=1 で配信
      ▼
 /?demo=1                                    ブラウザの中だけで再生（API・DB は使わない）
```

再生中の画面:

- 最初（既定 8:00）は地図に何もありません。再生が進むと、青い点（人）が集まった場所に `?` のピンが立ちます
- 公式の拠点（避難所・公式給水）に当たった候補は、聞かずに確認済み（緑の公式のピン）になります
- `?` のピンから 500m 以内に地図の中心を持っていくと「近くで人が集まっています」と聞かれ、答えると確認済み（紺）になります。回答は手元だけです
- 集まりが終わったピンは薄くなり、2 時間後に消えます

操作:

| キー | 動き |
|---|---|
| スペース | 再生 / 一時停止 |
| ← → | 10 分戻す / 進める |
| [ ] | 遅く / 速く（1 秒 = 1 / 5 / 15 分） |
| c | 再生バー（再生・時計・つまみ・速さ）を出す / 隠す |

URL の指定: `from=HH:MM`（最初の時刻）、`speed=1|5|15`、`paused=1`（止めた状態で始める）、`at=lat,lon`（最初の場所）、`zoom=`、`controls=1`（再生バーを出す）。動画を撮るなら `/?demo=1&from=08:00&speed=5&paused=1` → 同意 → スペース。

デモ動画用のデータ（八代市役所の周り: 北は臨港線・西はゆめタウン・南は植柳・東は国道 3 号の先）を作り直す:

```sh
python scripts/synth/make_demo_data.py --scenario scripts/synth/demo_scenario_video.json --out data/demo
python scripts/synth/make_heatmap.py --out data/demo && python scripts/synth/detect_demo.py --out data/demo
python scripts/synth/make_demo_assets.py --out data/demo --scenario scripts/synth/demo_scenario_video.json   # → web/demo/
```

- 確認: `DEMO=1 node scripts/check/e2e-check.js <lat> <lon> <置き場>`（8:00 にピンがないこと → 再生 → 質問 → 回答）
- 1 コマずつ撮る: `scripts/check/capture-demo.js`（`window.demoSeek(分)` で時刻を指定。使い方はファイル冒頭）
- 紹介動画のシナリオ: [docs/scenario.md](docs/scenario.md)。画面の細かい動き: [docs/web-ui-design.md](docs/web-ui-design.md) §5
- main を直したら、このブランチで `git merge main` で取り込みます

## 9. ディレクトリ構成

```
.
├── app/                     API と検知
│   ├── main.py              ルーティング・画面の配信・検知スレッドの起動
│   ├── auth.py              セッションの発行と検証
│   ├── db.py                DB 接続
│   ├── rules.py             確定・終了・表示の数値、備考の検査、対象地域
│   ├── ratelimit.py         書き込みの回数制限
│   ├── spots.py             候補・回答・公式拠点の読み書き
│   ├── detect.py            検知の純関数（滞在・学習・集計・ポアソン）
│   ├── detect_job.py        定期処理（DB → 検知 → candidates、古いデータの削除）
│   └── mesh.py              地域メッシュの番号
├── web/                     Web 画面（index.html, app.js, style.css, places.geojson, manifest, アイコン）
│   └── demo/                デモ再生の素材（timeline.json, people.bin。DEMO=1 のときだけ配信）
├── db/init.sql              DB のスキーマ
├── scripts/
│   ├── import_bodik.py      公式の避難所の取り込み（BODIK の GeoJSON）
│   ├── import_official.py   公式の拠点の取り込み（CSV、どの地域でも）
│   ├── make_places.py       地図の背景の施設
│   ├── synth/               合成データ・API への投入・答え合わせ・参加率・ヒートマップ・デモの素材（make_demo_assets.py, demo_scenario_video.json）
│   └── check/               Playwright の自動確認・デモの撮影（capture-demo.js）
├── tests/                   pytest（API・ルール・取り込み・検知・定期処理・合成データ）
├── data/                    入力データ（BODIK・OSM・小字・公式拠点の CSV のひな型）。out/ demo/ estat/ はコミットしない
├── docs/                    設計の文書（下の「文書」）
├── .github/workflows/ci.yml CI
├── compose.yaml, Dockerfile, pyproject.toml, uv.lock
├── CLAUDE.md                企画の芯・検証の数字・出典
└── LICENSE                  MIT
```

## 10. ブランチ

```
main   ──●────●────●────●──────────▶  OSS の本体（アプリ・検知・文書）
                         ╲  main を直したら demo で git merge main
demo                      ●────●───▶  main + デモ（発表用の早回し再生・紹介動画の素材・撮影スクリプト・シナリオ）。このブランチ

survey ──●────●────────────────────▶  友人の行動の記入フォーム（GitHub Pages はこのブランチの /docs から配信）
```

- 発表・審査・動画用のデモは、OSS として使う人には要らないので main に入れず、このブランチに置いています（8.8）
- 記入フォームは、検知を実際の行動で確かめるためのデータ集めです。`survey` ブランチの README を見てください

## 11. 限界と今後

限界（正直に）:

- 実データ（YJMob100K）は 30 分・500m 単位。15〜45 分の短い給水は未検証
- 八代市の結果は合成データで、仮定に左右される
- 災害時の実データでの精度は未測定。災害時の GPS と拠点の正解がそろったデータがない
- 参加率 5〜10% では、小さな炊き出し（数十人）は見えない（§7.2）
- 今は「いつもの場所」を判定するため、位置の点をセッション単位でサーバーに置いている（仮名化）。「端末で判定し、集計だけ送る」形はまだ
- Web 画面は閉じると位置が取れない（特に iPhone）
- 集まりが 2 つのマスにまたがると、半分ずつに割れて見逃すことがある

今後:

- 友人の記入フォーム（`survey` ブランチ）の回答で、実際の行動に近いデータを作り直して確かめる
- 場所チャンネル（「ここに何がある？」）の試作と、友人との実地実験
- LINE ミニアプリ・ネイティブアプリ化（画面を閉じても位置が取れるように）
- 端末内で判定し、集計だけ送る形
- 詳しい課題は [docs/algorithm.md](docs/algorithm.md) §8

## 12. 文書

| 文書 | 内容 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | 企画の芯・検証の数字・出典・発表の構成（数字はここのとおりに使う） |
| [docs/architecture.md](docs/architecture.md) | システム構成と選定理由（言語・実行基盤・DB・API・位置データ・セッション・定期処理） |
| [docs/algorithm.md](docs/algorithm.md) | 検知アルゴリズム: 考え方・手順・定数・合成データでの結果・参加率・採らなかった方式 |
| [docs/security.md](docs/security.md) | セキュリティと個人情報: 入れている守り・守れていないこと・運用・確かめ方 |
| [docs/web-ui-design.md](docs/web-ui-design.md) | 画面の設計（地図・質問・報告・公式拠点）と API・DB のルール、デモモード（§5） |
| [docs/web-ui-implementation.md](docs/web-ui-implementation.md) | 画面を最初に作ったときの実装手順書（履歴。今の仕様は web-ui-design.md） |
| [docs/demo-data-design.md](docs/demo-data-design.md) | 合成の人流データ: 作り方・仮定値・2026年熊本地震の事実・答え合わせ |
| [docs/scenario.md](docs/scenario.md) | 3本に分けた紹介動画のシナリオ（テーマ紹介・デモ・精度）: シーンごとの画面・動き・テロップ・出典、デモの撮影リスト |
| [data/README.md](data/README.md) | 入力データの出典とライセンス |
| [data/official/README.md](data/official/README.md) | 公式の拠点の CSV の形式 |

## 参考文献

※「本文確認済み」と書いていない文献は、要旨のみ確認。

### 場所ごとの平常比較は災害時に崩れる

- **Jiang, R., Song, X., Huang, D., Song, X., Xia, T., Cai, Z., Wang, Z., Kim, K.-S., Shibasaki, R. (2019). DeepUrbanEvent: A System for Predicting Citywide Crowd Dynamics at Big Events.** Proceedings of the 25th ACM SIGKDD International Conference on Knowledge Discovery & Data Mining, 2114–2122. https://doi.org/10.1145/3292500.3330654
  - 地震・台風・大きな祭りなどの「大きな出来事」のときは、人の動きが日常と大きく変わるため、街全体の人の動きの予測が難しいと指摘
  - 直近1時間の街全体の人の動きを入力にして、次の1時間を予測するシステムを提案。動画の予測と同じ考え方で、深層学習（再帰型ニューラルネットワーク）を使う
  - 企画との関係: 「災害時は平常時の比較が通用しない」ことを支持

- **Fan, Z., Song, X., Shibasaki, R., Adachi, R. (2015). CityMomentum: An Online Approach for Crowd Behavior Prediction at a Citywide Level.** Proceedings of the 2015 ACM International Joint Conference on Pervasive and Ubiquitous Computing (UbiComp 2015), 559–569. https://doi.org/10.1145/2750858.2804277
  - 過去のデータを使わず、直近の人の動きだけで街全体の短期の人の動きを予測する方法（似た動きの人をまとめて予測する）
  - 大規模災害への対応を目的とした研究の一部
  - 企画との関係: 「災害時は過去の平常データに頼れない」ことを支持

### 個人の移動は規則的で、逸脱が信号になる

- **Song, C., Qu, Z., Blumm, N., Barabási, A.-L. (2010). Limits of Predictability in Human Mobility.** Science, 327, 1018–1021. https://doi.org/10.1126/science.1177170
  - 匿名の携帯電話利用者5万人（1,000万人から無作為に抽出）の3か月分の移動を分析
  - 移動の予測可能性は利用者全体で93%。移動距離が人によって大きく違っても、予測可能性はあまり変わらない
  - 企画との関係: 人の動きは規則的なので、「いつもと違う場所に行った」こと自体が信号になる

- **Song, C., Koren, T., Wang, P., Barabási, A.-L. (2010). Modelling the scaling properties of human mobility.** Nature Physics, 6, 818–823. https://doi.org/10.1038/nphys1760
  - 匿名の携帯電話利用者300万人の移動を分析
  - 人の移動を「新しい場所の探索」と「よく行く場所に戻る」の2つの要素で表すモデルを提案。単純なランダムな動きとはどれだけ違うかを示した
  - 企画との関係: 同上（個人ごとの「いつもの場所」を基準にする考え方の裏付け）

### 探索型の人がいる → 人ごとの補正が必要

- **Pappalardo, L., Simini, F., Rinzivillo, S., Pedreschi, D., Giannotti, F., Barabási, A.-L. (2015). Returners and explorers dichotomy in human mobility.** Nature Communications, 6, 8166. https://doi.org/10.1038/ncomms9166
  - いつもの場所を行き来する「リターナー（戻る型）」と、新しい場所へ広く動く「エクスプローラー（探索型）」の2種類の人がいることを発見
  - 既存のモデルではこの2種類を説明できないため、新しいモデルを作った
  - 企画との関係: 探索型の人を軽く数える補正の根拠

### 個人単位でイベント参加を判定

- **Traag, V. A., Browet, A., Calabrese, F., Morlot, F. (2011). Social Event Detection in Massive Mobile Phone Data Using Probabilistic Location Inference.** 2011 IEEE Third International Conference on Privacy, Security, Risk and Trust and 2011 IEEE Third International Conference on Social Computing (PASSAT/SocialCom), 625–628. https://doi.org/10.1109/PASSAT/SocialCom.2011.133
  - 大量の携帯電話データから、普段と違う大きな人の集まり（イベント）を見つける方法。位置を確率的に推定する（ベイズ推定）
  - 「誰がそのイベントに参加しているか」を判定する枠組みも作った。いくつかの例で方法を示した
  - 企画との関係: 個人単位で集まりへの参加を判定する、最も近い先例

### 災害時の人流で避難・支援の実態を把握

- **Yabe, T., Tsubouchi, K., Sudo, A., Sekimoto, Y. (2016). A framework for evacuation hotspot detection after large scale disasters using location data from smartphones: case study of Kumamoto earthquake.** Proceedings of the 24th ACM SIGSPATIAL International Conference on Advances in Geographic Information Systems. https://doi.org/10.1145/2996913.2997014
  - 2016年4月16日の熊本地震（M7.3）では1万棟以上が大きく壊れ、10万人以上が避難した。指定避難所以外に避難した人もいて、行政が避難先を把握するまで数日かかり、物資や救助が遅れた
  - 従来の把握方法は徒歩の現地調査で、時間がかかり、発災直後は実施が難しい
  - Yahoo Japan が集めたスマホの GPS から、避難が集中した場所（避難ホットスポット）を推定する枠組みを提案。施設の位置や新聞報道と照らし合わせて検証し、場所ごとの人数の推移と、拠点として機能した期間を示した
  - 企画との関係: 熊本での最も近い先例。対象は避難先

- **瀬戸寿一・樫山武浩・関本義秀 (2016). 平成28年4月熊本地震における携帯電話位置情報を用いた避難所の混雑度推計.** 地理情報システム学会 講演論文集. https://www.gisa-japan.org/content/files/conferences/2016/papers/C32.pdf （本文確認済み）
  - データ: ゼンリンデータコム「混雑統計®」（ドコモ地図ナビのオート GPS 利用者の位置情報を統計加工したもの、250m メッシュ）。熊本県内、2016年4月7日〜17日。4月7日〜13日を平時とした
  - 避難所データ: 学生 IT ボランティア「Youth Action for Kumamoto」の避難所データベース（903件）。このうち259件（約28.7%）は国土数値情報に載っていない自主避難所など
  - 結果: 平時より混雑したメッシュは15日10、16日38、17日22。グランメッセ熊本（当初は指定避難所でない）周辺では16日2〜4時に約3,200人以上。江原中学校周辺はピーク（17日17時）で約2,700人以上
  - 限界: 著者自身が「この手法だけでは実際の避難状況の把握や対策には不完全」と述べている
  - 企画との関係: 場所ごとの平常比較の国内先例

- **菊池健太・円山琢也 (2023). 複数の位置情報データを利用した令和2年7月豪雨熊本県人吉市の避難と支援の実態把握.** 土木学会論文集, 79(1). https://doi.org/10.2208/jscejj.22-00243
  - データ: 基地局ベースの「モバイル空間統計」と、GPS ベースの点群型の人流データ
  - 浸水した地区では、夜間の人口が平常時より大きく減った
  - GPS データからボランティアの活動の様子を捉え、ボランティアセンターでの滞在時間を算出した
  - 企画との関係: 災害時の人流で支援の実態を把握した、国内で最も近い研究

### 現地の人に聞いて確かめる

- **Liu, Y., Alexandrova, T., Nakajima, T. (2013). Using Stranger as Sensors: Temporal and Geo-sensitive Question Answering via Social Media.** Proceedings of the 22nd International Conference on World Wide Web (WWW 2013), 803–814. https://doi.org/10.1145/2488388.2488458 （本文確認済み）
  - MoboQ: 「今、あの店の行列はどれくらい？」のような、場所と時間に関する質問に、その場にいそうな人が答えるサービス。中国で実際に運用
  - 中国の SNS「新浪微博（Sina Weibo）」の投稿から、その場所に今いそうな人を見つけ、その人に質問を送る（答える人は MoboQ の利用者でなくてよい）
  - 2012年初めから10月までに、登録者35,214人が15,224件の質問をし、29,491件の回答が集まった。74.6%の質問に1件以上の回答があり、28%は10分以内、51%は20分以内に最初の回答が届いた
  - 企画との関係: 「ここに何がある？」を現地の人に聞いて確かめる部分の先例

## ライセンス

[MIT](LICENSE)。公式の避難所データ（BODIK）、地図（OpenStreetMap・OpenFreeMap など）、入力データ（[data/README.md](data/README.md)）は、それぞれの提供元の利用条件に従います。
