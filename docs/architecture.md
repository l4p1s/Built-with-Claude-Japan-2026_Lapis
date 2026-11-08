# システム構成（実行基盤・言語・DB・API・セッション）

> 画面と API・DB の詳細は [web-ui-design.md](web-ui-design.md)、検知の中身は [algorithm.md](algorithm.md)、合成データは [demo-data-design.md](demo-data-design.md) が最新。この文書は全体の構成と選定理由をまとめる。
> ここに書いた構成は実装済み（API、画面、検知の定期処理、CI）。発表用のデモ再生は `demo` ブランチに分けている。

## 前提
ハッカソンへの提出と OSS としての公開を前提に、「GPS → いつもと違う滞在を数える → ポアソンで候補検知 → 近くの人に質問 → 地図に掲載」を動かす土台を決める。
今は Web アプリで、将来はネイティブアプリや LINE ミニアプリへ移るため、**REST API を唯一の契約**にし、Web はその 1 クライアントにする。
アカウントは作らず、匿名のセッション ID で読み書きを管理する。

決めたこと: DB は PostgreSQL + PostGIS。まず手元で動かし、公開時の HTTPS の前段は使う人が用意する。言語は OSS として長く使う前提で選ぶ。

---

## 1. 言語の選定

評価軸（OSS として長く使われる前提）:
1. 検知ロジック（滞在判定・ポアソン検定・メッシュ集計）を書きやすく、**検証（YJMob100K・八代合成データ）を同じ言語で再現できる**か
2. 地理空間・統計のライブラリの厚さ
3. 貢献者の母数（防災・GIS・シビックテック・研究者）
4. REST の仕様書（OpenAPI）を自動生成でき、ネイティブ用クライアントを生成できるか
5. 型安全・保守性・配布の手軽さ

| 候補 | 1 検証の再現 | 2 地理・統計 | 3 貢献者 | 4 OpenAPI | 5 保守・配布 |
|---|---|---|---|---|---|
| **Python + FastAPI** | ◎ 研究コードと同じ言語 | ◎ numpy / scipy / shapely / geopandas | ◎ GIS・研究・Code for Japan 系に多い | ◎ 型定義から自動生成 | ○ 型ヒント＋静的検査で補う。速度は本用途で十分 |
| TypeScript (Node) | △ 統計・地理が薄い | △ turf.js 程度 | ◎ Web 開発者に多い | ○ 追加ライブラリで可 | ○ フロントと同言語 |
| Go | △ | △ | ○ | ○ | ◎ 単一バイナリ |
| Rust / Kotlin | △ | △ | △ | ○ | ○ 学習コスト高 |

**結論: Python 3.12+ / FastAPI。**
決め手は「検証と本番の検知コードを同じ言語・同じ関数で共有できる」こと。検知精度（97〜98% など）の数字を OSS の利用者が自分で再現できる。
負荷は「数分おきのバッチ検知＋位置の受信」で、Python の速度が問題になる規模ではない。

周辺ツール: パッケージ管理 **uv**（pyproject.toml + uv.lock）、整形・静的検査 **ruff**、テスト **pytest**。
DB 接続は **psycopg 3 + 生 SQL**（PostGIS の関数は SQL で書く方が素直。ORM は入れない）。

## 2. 実行基盤

- **Docker Compose** で `db`（公式 `postgis/postgis` イメージ）と `api`（python-slim + uv）の2コンテナ。`docker compose up` 1 発で起動し、利用者も同じ手順で再現できる
- 手元ではポート 18080 で動く（コンテナ内は 8000）。`api` はソースをマウントして自動リロードする
- Web 画面は API と同じオリジンから配信 → CORS 設定不要、TLS の前段 1 つで公開できる
- 注意（先に伝える制約）: スマホで GPS を取るには HTTPS が必須。HTTP のままでは PC のブラウザでしか位置が取れないので、スマホ実機は HTTPS で公開したときに確認する。発表のデモは `demo` ブランチの再生機能で見せる
- 環境変数（compose.yaml）: `DATABASE_URL`、`AREA`（対象地域 `lat,lon,半径m`。既定は八代市役所から 25km、空で制限なし）、`DETECT_EVERY_S`（検知の間隔。既定 300 秒、0 で止める。テストでは 0）、`RATE_LIMIT=off`（回数制限を止める。テスト用）
- Web アプリは画面を閉じると位置が取れない（CLAUDE.md §3 のとおり）。これはネイティブ化で解決する前提

## 3. DB（PostgreSQL + PostGIS）

`db/init.sql` を postgis コンテナの初期化スクリプトとして読み込む（移行ツールは公開後にスキーマ変更が出たら Alembic 等を導入）。

| テーブル | 主な列 | 備考 |
|---|---|---|
| sessions | id uuid, token_hash bytea unique, consented_at, last_seen_at | トークン本体は保存せずハッシュのみ |
| location_points | session_id → sessions(on delete cascade), geom geography(Point,4326), accuracy_m, recorded_at | GiST 索引。保存期間を決めて古い点は削除 |
| candidates | id, source(detected/report), center geography, status(pending/confirmed/ended), last_activity_at, created_at | 非公式拠点の候補。検知の列（cell_code, window_start, observed, expected, p_value）は detected のときだけ入る |
| answers | id, candidate_id, session_id, kind(water/food/goods/car_sleeping/shelter/other/none/ended), note(100文字まで), answered_at, unique(candidate_id, session_id) | 1人1候補につき最新の意見1件（上書き） |
| flags | answer_id, session_id, created_at | 備考の「不適切」通報。1人1備考1回 |
| official_spots | id, source, source_key, name, address, kind(shelter/water), geom, opened_at, closed_at | 公式の拠点。八代市の避難所は `scripts/import_bodik.py`（BODIK の GeoJSON）、他の地域や給水所は `scripts/import_official.py`（CSV、data/official/README.md）で取り込む。source ごとに上書き |

表の関係:

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
 | official_spots  |   公式の避難所と給水所（取り込み）。ほかの表とは結ばない
 +-----------------+
```

削除はすべて `ON DELETE CASCADE` でつながっている。セッションを消すと、その人の位置、回答、通報も消える。

マス目: **1/2 地域メッシュ（500m）の番号**（`app/mesh.py`）で集計。検証（YJMob100K の 500m）と国勢調査・経済センサスのメッシュと揃う。メッシュ番号の計算は純関数でテストを付ける。

## 4. REST API（`/v1` 付きで版管理。ネイティブは強制更新できないため）

| メソッド | パス | 用途 |
|---|---|---|
| POST | `/v1/sessions` | 同意と今の位置を受けてセッションを発行 → `{token, expires_at}`。対象地域の外なら 403 |
| DELETE | `/v1/sessions/me` | 参加をやめる。位置、回答、通報、自分だけが知らせたピンを消す |
| POST | `/v1/locations` | 位置をまとめて送る（1〜1000 点）→ 202。ネイティブのバックグラウンド送信にもそのまま使える |
| POST | `/v1/questions` `{lat, lon}` | 近く（ST_DWithin）の未確認の候補 =「ここに何がある？」。位置は URL に入れず本文で送る |
| POST | `/v1/candidates/{id}/answers` | 回答（種類 + 備考 100 文字まで）。今の位置が要り、500m より遠いと 403 |
| POST | `/v1/reports` | 自分の位置で拠点を知らせる。近くに表示中の候補があれば合流 |
| POST | `/v1/answers/{id}/flags` | 備考を不適切として通報 |
| GET | `/v1/spots?bbox=&at=` | 非公式のピンと公式の拠点を 1 つの GeoJSON で返す。`at` でその時点に開いていた公式拠点 |
| GET | `/healthz` | 死活確認 |

仕様書は FastAPI が `/openapi.json` と `/docs` を自動生成。将来のネイティブ用クライアントはここから生成する。

### 4.1 位置データの形式と流れ（端末 → 集計）

位置は **端末がためて、まとめて送る**（ストリーミングで 1 点ずつ送らない）。電池と電波を節約でき、圏外でも後から送れる。検知は 5 分ごとのバッチなので、秒単位の即時性は要らない。

| 項目 | 決めたこと |
|---|---|
| 座標 | WGS84（GPS そのまま、EPSG:4326）の 10 進の度。`lat`（緯度、-90〜90）、`lon`（経度、-180〜180）。小数 6 桁まで（約 0.1m）。度分秒や日本測地系は使わない |
| 精度 | `accuracy_m`: 端末が返す誤差の半径（m）。任意、0 以上 |
| 時刻 | `recorded_at`: 端末で取った時刻。ISO 8601 で**時差つき**（例 `2026-07-29T12:30:00+09:00` か `...03:30:00Z`）。時差なしは 422 |
| 1 回の送信 | `POST /v1/locations` に `{"points": [{lat, lon, accuracy_m, recorded_at}, ...]}`、1〜1000 点。`Authorization: Bearer <token>` |
| Web 画面の間隔 | 位置は `watchPosition` の更新のうち **30 秒に 1 点**を記録し、**60 秒ごと**にまとめて送る（最初の 1 点はすぐ送る）。送れなければ端末に最大 1000 点ためて次回に送る |
| 保存 | `location_points(session_id, geom, accuracy_m, recorded_at)`。参加ごとの番号（セッション）に結びつき、名前や端末 ID は持たない。14 日で消す |
| 集計 | `app/detect_job.py` が 5 分ごとに直近 3 時間を読み、500m メッシュ × 30 分で「いつもと違う滞在」を数える（docs/algorithm.md）。個人の軌跡は外に出さず、候補（マス・時間帯・人数）だけが `candidates` に残る |

ネイティブアプリや LINE ミニアプリも同じ `POST /v1/locations` を使う（画面を閉じても位置が取れるのがネイティブ化の目的）。間隔は端末側で変えてよい（例: 動いていないときは送らない）。
合成データの CSV（`scripts/synth/`）は実験用で、本番の経路はこの API だけ。発表用のデモ（`demo` ブランチ）は位置を取らず送らない。

### 4.2 候補の状態

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

## 5. セッション ID の扱い（アカウントなし）

- 発行: サーバーが `secrets.token_urlsafe(32)` で生成し、**DB には SHA-256 ハッシュだけ**保存（DB が漏れてもなりすませない）
- 送り方: `Authorization: Bearer <token>` ヘッダー。Cookie にしない理由 = ネイティブでもそのまま使え、CSRF 対策が不要
- 保存先: Web は localStorage、ネイティブは Keychain / Keystore
- 有効期限: 最終利用から30日でセッションが使えなくなる。失効したセッションと 14 日より古い位置は検知の定期処理（`app/detect_job.py`）が消す
- 参加をやめると、位置・回答・通報に加え、自分の報告だけでできたピンも消える（他の人の回答があるピンは残り、状態を決め直す）
- `/v1/sessions` と `/healthz` 以外はすべてセッション必須（閲覧も書き込みも）

### 5.1 書き込みの守り（悪用対策）

対象地域の中の人だけ参加（`AREA`）、候補から 500m 以内の人だけ回答、回数制限（`app/ratelimit.py`）、位置の時刻の検査、入力の検査。一覧と守れていないことは [security.md](security.md)。

- 正直に書く点: 今の構成では「いつもの場所」の判定のため、位置の点はセッション単位でサーバーに保存する（仮名化）。「端末で判定し、集計だけ送る」形は今後の課題

## 6. 検知処理と画面

| 部品 | 技術 | ファイル |
|---|---|---|
| API | Python 3.12 / FastAPI / psycopg 3（生 SQL、ORM なし） | `app/main.py`（ルーティング）、`app/spots.py`（候補、回答、公式拠点の読み書き） |
| 認証 | アカウントなしの匿名セッション。Bearer トークン、DB には SHA-256 だけ | `app/auth.py` |
| ルール | 確定、終了、表示の数値、備考の検査、対象地域 | `app/rules.py` |
| 回数制限 | プロセス内のスライディングウィンドウ | `app/ratelimit.py` |
| 検知 | 純関数（滞在、学習、集計、ポアソン） | `app/detect.py`、`app/mesh.py`（地域メッシュの番号） |
| 定期処理 | DB から読んで検知し `candidates` に書く。古いデータを消す | `app/detect_job.py` |
| DB | PostgreSQL 17 + PostGIS 3.5 | `db/init.sql` |
| 画面 | 素の HTML/CSS/JS（ビルドなし）+ MapLibre GL 6 + OpenFreeMap。PWA | `web/` |
| 道具 | uv、ruff、pytest、Playwright（画面の自動確認） | `pyproject.toml`、`scripts/check/` |


- 検知: 純関数は `app/detect.py`（滞在 → いつもの場所 → 500m メッシュ × 30 分 → ポアソン。説明は docs/algorithm.md）。DB との出し入れは `app/detect_job.py`
  - `api` の起動時に別スレッドで `DETECT_EVERY_S` 秒ごと（既定 300、0 で止める）に回る。手動は `docker compose exec api python -m app.detect_job [--at 時刻]`
  - 直近 7 日で人ごとの自宅・いつもの場所・マス別の滞在の割合を学習し（24 時間に 1 回。あいだは覚えたものを使う）、直近 3 時間を調べる。開設中の公式の拠点（`official_spots`）から 150m 以内の集まりは候補にしない（公式のピンがもう出ている）。近く（150m）に生きている候補があれば更新、報告の候補に重なれば 1 人ぶんの裏付けとして確認済みにする
  - 同じ処理で、14 日より古い位置と 30 日使われていないセッションを消す
  - 合成データでの答え合わせ（CSV）は `scripts/synth/detect_demo.py`（docs/demo-data-design.md §7）
  - 点 → 10分以上の滞在 → その人のいつもの場所（自宅周り200mは対象外）か判定 → マス・時間帯ごとに件数 → その日の街全体の量から期待値 → ポアソンで大きく超えたら候補
  - ポアソンの上側確率は標準ライブラリ `math` で計算（scipy は検証の再現で必要になったら追加）
- Web 画面: ビルド不要の素の HTML/JS + MapLibre GL 6（CDN）+ OpenFreeMap。API から配信
- デモ用 `scripts/synth/simulate.py`: 合成データ（`scripts/synth/make_demo_data.py`、docs/demo-data-design.md）を **REST API 経由で**投入する。日付は「地震の翌日 = 今日」にずらすので、時間が進むと検知が「今」の集まりを見つける（再生）

## 7. ファイル構成

```
.
├── app/                     API と検知
│   ├── main.py              ルーティング、画面の配信、検知スレッドの起動
│   ├── auth.py              セッションの発行と検証
│   ├── db.py                DB 接続
│   ├── rules.py             確定、終了、表示の数値、備考の検査、対象地域
│   ├── ratelimit.py         書き込みの回数制限
│   ├── spots.py             候補、回答、公式拠点の読み書き
│   ├── detect.py            検知の純関数（滞在、学習、集計、ポアソン）
│   ├── detect_job.py        定期処理（DB → 検知 → candidates、古いデータの削除）
│   └── mesh.py              地域メッシュの番号
├── web/                     Web 画面（index.html, app.js, style.css, places.geojson, manifest, アイコン）
├── db/init.sql              DB のスキーマ
├── scripts/
│   ├── import_bodik.py      公式の避難所の取り込み（BODIK の GeoJSON）
│   ├── import_official.py   公式の拠点の取り込み（CSV、どの地域でも）
│   ├── make_places.py       地図の背景の施設（→ web/places.geojson）
│   ├── synth/               合成データ、API への投入、答え合わせ、参加率、ヒートマップ
│   └── check/               Playwright の自動確認（ui-check.js、e2e-check.js。使い方はファイルの冒頭）
├── tests/                   pytest（API、ルール、取り込み、検知、定期処理、合成データ）
├── data/                    入力データ（BODIK、OSM、小字、公式拠点の CSV のひな型）。out/ demo/ estat/ はコミットしない
├── docs/                    設計の文書、紹介動画 promo.mp4 と README 冒頭の GIF
├── .github/workflows/ci.yml CI（PostGIS を立てて ruff と pytest）
├── compose.yaml, Dockerfile, pyproject.toml, uv.lock
├── CLAUDE.md                企画の芯、検証の数字、出典
└── LICENSE                  MIT
```

ライセンス: MIT。YJMob100K は CC BY 4.0 なのでデータ自体はリポジトリに入れず、取得手順だけ書く。

## 8. 入れないもの（必要になったら追加）
ORM・移行ツール・キュー/スケジューラー（検知は api の中のスレッド。プロセスを複数にしたら外に出す）・プッシュ通知・管理画面・フロントのビルド環境。回数制限はプロセス内（`app/ratelimit.py`）で入れた。ワーカーを増やすなら Redis 等に移す。

## 検証（構築後の確認手順）
以下の `$BASE` は起動したサーバーの URL。

1. `docker compose up` → `curl "$BASE/healthz"`
2. `curl -X POST "$BASE/v1/sessions" -H 'Content-Type: application/json' -d '{"consent":true,"lat":32.5072,"lon":130.6016}'` でトークン取得、トークンなしで `/v1/spots` が 401 になること
3. 報告 `POST /v1/reports` → 別セッションで回答 → `/v1/spots` の GeoJSON で `confirmed` になる
3'. `python scripts/synth/simulate.py` → `docker compose exec api python -m app.detect_job` → `/v1/spots` に `source: detected` の候補が出る → 近くで `/v1/questions` に出て、回答すると `confirmed`
4. ブラウザで画面を開き、地図にピンが出ること
5. `DELETE /v1/sessions/me` で location_points・回答・自分だけの報告のピンが消えること
6. `docker compose run --rm api pytest -q`（API・ルール・取り込み・検知・定期処理・合成データ）と GitHub Actions（`.github/workflows/ci.yml`）が通ること
