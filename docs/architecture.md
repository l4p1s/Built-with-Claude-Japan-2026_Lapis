# システム構成（実行基盤・言語・DB・API・セッション）

> 画面と API・DB の詳細は [web-ui-design.md](web-ui-design.md) が最新。この文書は全体の構成と選定理由をまとめる。

## Context
リポジトリにはまだ CLAUDE.md と README.md しかない。ハッカソン提出（GitHub 必須）かつ OSS として公開する前提で、
「GPS → いつもと違う滞在を数える → ポアソンで候補検知 → 近くの人に質問 → 地図に掲載」を動かす土台を決める。
今は Web アプリ、将来ネイティブ / LINE ミニアプリへ移るため、**REST API を唯一の契約**にし、Web はその1クライアントに過ぎない形にする。
アカウントは作らず、匿名のセッション ID で読み書きを管理する。

確定済み（ユーザー回答）: DB = PostgreSQL + PostGIS / 実行はまず localhost、公開時は ngrok をユーザーが用意 / 言語は OSS 前提で厳密に選ぶ。

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

- **Docker Compose** で `db`（公式 `postgis/postgis` イメージ）と `api`（python-slim + uv）の2コンテナ。`docker compose up` 1発で起動し、審査員・OSS 利用者も同じ手順で再現できる
- 開発は localhost:18080。`api` はソースをマウントして自動リロード（ホスト側は 18080。8000 は同じマシンの別サーバーが使っているため。コンテナ内は 8000）
- Web 画面は API と同じオリジンから配信 → CORS 設定不要、`ngrok http 18080` 1本で公開できる
- 注意（先に伝える制約）: スマホで GPS を取るには HTTPS が必須。localhost では PC ブラウザでしか位置が取れないので、スマホ実機は ngrok 公開時に確認する。デモ本体はシミュレーター（後述）で動かす
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
| official_spots | id, source, source_key, name, address, kind(shelter/water), geom, opened_at, closed_at | 公式の拠点。`scripts/import_bodik.py` で取り込む |

マス目: **1/2 地域メッシュ（500m）の番号**（`app/mesh.py`）で集計。検証（YJMob100K の 500m）と国勢調査・経済センサスのメッシュと揃う。メッシュ番号の計算は純関数でテストを付ける。

## 4. REST API（`/v1` 付きで版管理。ネイティブは強制更新できないため）

| メソッド | パス | 用途 |
|---|---|---|
| POST | /v1/sessions | 同意 `{"consent": true}` を受けてセッション発行 → `{token, expires_at}` |
| DELETE | /v1/sessions/me | 自分のデータを全削除（参加の取り消し）。自分の回答と、自分の報告だけでできたピンも消える |
| POST | /v1/locations | 位置の一括送信 `{points:[{lat,lon,accuracy_m,recorded_at}]}` → 202。ネイティブのバックグラウンド送信にもそのまま使える |
| GET | /v1/questions?lat=&lon= | 近く（ST_DWithin）の未確認候補 =「ここに何がある？」 |
| POST | /v1/candidates/{id}/answers | `{kind, note}` で回答（note は任意、100文字まで） |
| POST | /v1/reports | 自分の位置で拠点を報告。近くに表示中の候補があればそこへ合流 |
| POST | /v1/answers/{id}/flags | 備考を不適切として通報 |
| GET | /v1/spots?bbox=&at= | 確認済み・未確認の非公式ピンと公式の避難所を1つの GeoJSON で返す。`at` を付けるとその時点で開いていた公式拠点（MapLibre もネイティブ地図もそのまま読める） |
| GET | /healthz | 死活確認 |

仕様書は FastAPI が `/openapi.json` と `/docs` を自動生成。将来のネイティブ用クライアントはここから生成する。

## 5. セッション ID の扱い（アカウントなし）

- 発行: サーバーが `secrets.token_urlsafe(32)` で生成し、**DB には SHA-256 ハッシュだけ**保存（DB が漏れてもなりすませない）
- 送り方: `Authorization: Bearer <token>` ヘッダー。Cookie にしない理由 = ネイティブでもそのまま使え、CSRF 対策が不要
- 保存先: Web は localStorage、ネイティブは Keychain / Keystore
- 有効期限: 最終利用から30日でセッションが使えなくなる。失効したセッションと 14 日より古い位置は検知の定期処理（`app/detect_job.py`）が消す
- 参加をやめると、位置・回答・通報に加え、自分の報告だけでできたピンも消える（他の人の回答があるピンは残り、状態を決め直す）
- `/v1/sessions` と `/healthz` 以外はすべてセッション必須（閲覧も書き込みも）
- 正直に書く点: 今の構成では「いつもの場所」の判定のため、位置の点はセッション単位でサーバーに保存する（仮名化）。スライド8の「端末で判定し集計だけ送る」は今後（スライド15）の扱いと揃える

## 6. 検知処理と画面

- 検知: 純関数は `app/detect.py`（滞在 → いつもの場所 → 500m メッシュ × 30 分 → ポアソン。説明は docs/algorithm.md）。DB との出し入れは `app/detect_job.py`
  - `api` の起動時に別スレッドで `DETECT_EVERY_S` 秒ごと（既定 300、0 で止める）に回る。手動は `docker compose exec api python -m app.detect_job [--at 時刻]`
  - 直近 7 日で人ごとの自宅・いつもの場所・マス別の滞在の割合を学習し、直近 3 時間を調べる。近く（150m）に生きている候補があれば更新、報告の候補に重なれば 1 人ぶんの裏付けとして確認済みにする
  - 同じ処理で、14 日より古い位置と 30 日使われていないセッションを消す
  - 合成データでの答え合わせ（CSV）は `scripts/detect_demo.py`（docs/demo-data-design.md §7）
  - 点 → 10分以上の滞在 → その人のいつもの場所（自宅周り200mは対象外）か判定 → マス・時間帯ごとに件数 → その日の街全体の量から期待値 → ポアソンで大きく超えたら候補
  - ポアソンの上側確率は標準ライブラリ `math` で計算（scipy は検証の再現で必要になったら追加）
- Web 画面: ビルド不要の素の HTML/JS + MapLibre GL 6（CDN）+ OpenFreeMap。API から配信
- デモ用 `scripts/simulate.py`: 合成データ（`scripts/make_demo_data.py`、docs/demo-data-design.md）を **REST API 経由で**投入する。日付は「地震の翌日 = 今日」にずらすので、時間が進むと検知が「今」の集まりを見つける（再生）

## 7. ファイル構成（最小）

```
compose.yaml / Dockerfile / pyproject.toml / uv.lock / README.md
LICENSE                   # MIT
db/init.sql
app/main.py               # ルーティング・Web 画面の配信
app/auth.py               # セッション発行・検証
app/db.py                 # DB 接続
app/rules.py              # 確定・終了などの数値ルール
app/spots.py              # 候補・回答・公式拠点の読み書き
app/detect.py             # 滞在判定・ポアソン検知（純関数）
app/detect_job.py         # DB から検知して candidates に書く定期処理・古いデータの削除
app/mesh.py               # 地域メッシュ番号
web/index.html, app.js, style.css, manifest.webmanifest, icon*.svg/png
scripts/import_bodik.py   # 避難所データの取り込み
scripts/make_demo_data.py, demo_scenario.json, make_heatmap.py, heatmap.html, detect_demo.py  # 合成データ・ヒートマップ・答え合わせ
scripts/simulate.py       # 合成データを API 経由で投入
tests/test_api.py, test_rules.py, test_import_bodik.py, test_detect.py, test_detect_job.py, test_make_demo_data.py
.github/workflows/ci.yml  # PostGIS をサービスに立てて ruff と pytest
```

ライセンス: MIT。YJMob100K は CC BY 4.0 なのでデータ自体はリポジトリに入れず、取得手順だけ書く。

## 8. 入れないもの（必要になったら追加）
ORM・移行ツール・キュー/スケジューラー（検知は api の中のスレッド。プロセスを複数にしたら外に出す）・プッシュ通知・レート制限・管理画面・フロントのビルド環境。

## 検証（構築後の確認手順）
1. `docker compose up` → `curl localhost:18080/healthz`
2. `curl -X POST localhost:18080/v1/sessions -d '{"consent":true}'` でトークン取得、トークンなしで `/v1/spots` が 401 になること
3. 報告 `POST /v1/reports` → 別セッションで回答 → `/v1/spots` の GeoJSON で `confirmed` になる
3'. `python scripts/simulate.py` → `docker compose exec api python -m app.detect_job` → `/v1/spots` に `source: detected` の候補が出る → 近くで `/v1/questions` に出て、回答すると `confirmed`
4. ブラウザで localhost:18080 を開き、地図にピンが出ること
5. `DELETE /v1/sessions/me` で location_points・回答・自分だけの報告のピンが消えること
6. `docker compose run --rm api pytest -q`（API・ルール・取り込み・検知・定期処理・合成データ）と GitHub Actions（`.github/workflows/ci.yml`）が通ること
