# Web 画面 実装手順書

> **これは画面を最初に作ったとき（2026-11-02〜03）の手順書で、履歴として残している。** 現在の仕様は [web-ui-design.md](web-ui-design.md)。その後の変更（検知の組み込み `app/detect_job.py`、デモ再生 `/?demo=1`、地図を Google / Apple マップ風にした改善、画面の自動確認 `scripts/ui-check.js`）はここには反映していない。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/web-ui-design.md` の Web 画面（地図・質問・報告・公式の避難所）と、それに必要な API・DB の変更を作る。

**Architecture:** API（FastAPI + PostgreSQL/PostGIS）に「報告」「回答の上書き」「通報」「公式拠点」を足し、確定・終了・表示のルールは `app/rules.py` の定数にまとめる。DB の読み書きは `app/spots.py`、ルーティングは `app/main.py`。画面はビルドなしの HTML/CSS/JS（`web/`）を FastAPI が `/` で配信し、地図は MapLibre GL 6（CDN の ES モジュール）＋ OpenFreeMap。

**Tech Stack:** Python 3.12 / FastAPI / psycopg 3 / PostgreSQL 17 + PostGIS 3.5 / pytest / MapLibre GL JS 6.16.0 / OpenFreeMap（positron・dark）

---

## 前提

- すべてのコマンドはリポジトリのルート（`/home/lapis/ClaudeCodeHackason2026`）で実行する
- `docker compose up -d --build --wait` で API（ホスト側 `127.0.0.1:18080`）と DB が起動していること。`api` コンテナはソースをマウントしているので、Python と `web/` の変更はビルドし直さなくても反映される
- テストは `docker compose run --rm api pytest ...` で実行する（実 DB を使う）
- DB の表を変えたら `docker compose down -v && docker compose up -d --wait` で作り直す。開発中の DB に入っているのはテストのデータだけなので消えてよい
- コミットはタスクごとに行う。push はしない（ユーザーの指示があったときだけ）
- コミットメッセージの末尾には次の行を付ける

```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

## ファイル構成

| ファイル | 役割 | 状態 |
|---|---|---|
| `app/rules.py` | 確定・終了・表示のルールの定数と、備考の検査（DB を使わない純関数） | 新規 |
| `app/spots.py` | 候補・回答・公式拠点の SQL | 新規 |
| `app/main.py` | ルーティングと入力の型。`web/` の配信 | 変更 |
| `db/init.sql` | 表の定義 | 変更 |
| `scripts/import_bodik.py` | BODIK の避難所データの取り込み | 新規 |
| `web/index.html` / `web/style.css` / `web/app.js` | 画面 | 新規 |
| `web/manifest.webmanifest` / `web/icon.svg` / `web/icon-{180,192,512}.png` | PWA | 新規 |
| `tests/test_rules.py` | `app/rules.py` のテスト | 新規 |
| `tests/test_api.py` | API のテスト（実 DB） | 変更 |
| `tests/test_import_bodik.py` | 取り込みの行の作り方のテスト | 新規 |
| `README.md` | 起動方法に画面と取り込みを追記 | 変更 |

`app/auth.py` と `app/db.py` は変えない。

---

### Task 1: ルールと備考の検査（`app/rules.py`）

**Files:**
- Create: `app/rules.py`
- Test: `tests/test_rules.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_rules.py`:

```python
import pytest

from app.rules import clean_note


def test_clean_note_accepts():
    assert clean_note(None) is None
    assert clean_note("   ") is None
    assert clean_note(" 12時まで。容器を持参 ") == "12時まで。容器を持参"
    assert clean_note("2026/7/29 12:00〜15:00") == "2026/7/29 12:00〜15:00"
    assert clean_note("あ" * 100) == "あ" * 100


@pytest.mark.parametrize(
    "note",
    [
        "あ" * 101,
        "詳細は https://example.com",
        "www.example.com",
        "0965-33-4111 まで",
        "０９０１２３４５６７８",
        "090 1234 5678",
    ],
)
def test_clean_note_rejects(note):
    with pytest.raises(ValueError):
        clean_note(note)
```

- [ ] **Step 2: 失敗することを確認する**

Run: `docker compose run --rm api pytest tests/test_rules.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.rules'`）

- [ ] **Step 3: 実装する**

`app/rules.py`:

```python
"""確定・終了・表示のルールと、備考の検査。数値はここだけで変える（docs/web-ui-design.md 2.3）。"""

import re
import unicodedata
from datetime import timedelta
from typing import Literal, get_args

# 「何があるか」の種類。none（わからない）と ended（もう終わっていた）は含まない
Kind = Literal["water", "food", "goods", "car_sleeping", "shelter", "other"]
KINDS = get_args(Kind)

QUESTION_RADIUS_M = 500  # 「ここに何がある？」を聞く範囲
MERGE_RADIUS_M = 100  # 報告を既存の候補にまとめる範囲
FADE_AFTER = timedelta(hours=3)  # 最後の確認からこの時間で薄く表示する
HIDE_AFTER = timedelta(hours=6)  # 最後の確認からこの時間で地図から消す
CONFIRM_ANSWERS = {"detected": 1, "report": 2}  # 確認済みになるのに必要な種類の回答の人数
NOTE_MAX_LEN = 100
NOTE_HIDE_FLAGS = 2  # この件数の通報で備考を出さなくする

_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_PHONE = re.compile(r"\d(?:[\s\-‐−ー()]*\d){9,}")  # 区切りを挟んで10桁以上の数字


def clean_note(note: str | None) -> str | None:
    """備考を検査して前後の空白を取る。空なら None。書けない内容なら ValueError。"""
    if note is None or not note.strip():
        return None
    note = note.strip()
    if len(note) > NOTE_MAX_LEN:
        raise ValueError(f"備考は{NOTE_MAX_LEN}文字までです")
    plain = unicodedata.normalize("NFKC", note)  # 全角の数字・記号も半角にして調べる
    if _URL.search(plain):
        raise ValueError("備考に URL は書けません")
    if _PHONE.search(plain):
        raise ValueError("備考に電話番号は書けません")
    return note
```

- [ ] **Step 4: 通ることを確認する**

Run: `docker compose run --rm api pytest tests/test_rules.py -q`
Expected: `7 passed`

- [ ] **Step 5: コミットする**

```bash
git add app/rules.py tests/test_rules.py
git commit -m "確定・表示のルールと備考の検査を追加（app/rules.py）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 表の変更と、回答・質問・地図の API

候補に `source`・`last_activity_at` を足し、回答を「1人1件の最新の意見（上書き）」にして、確定・終了・薄く表示・消すのルールを入れる。

**Files:**
- Modify: `db/init.sql`（全体を置き換え）
- Create: `app/spots.py`
- Modify: `app/main.py`（全体を置き換え）
- Test: `tests/test_api.py`（全体を置き換え）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_api.py` を次の内容で置き換える:

```python
"""実 DB（compose の db）に対して API を通しで確認する。

docker compose run --rm api pytest
"""

from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.db import DATABASE_URL
from app.main import app

client = TestClient(app)

# 海の上（紀伊半島の南）。開発用のデータ（八代市）と重ならない場所でテストする
LAT, LON = 30.0, 135.0
BBOX = f"{LON - 0.05},{LAT - 0.05},{LON + 0.05},{LAT + 0.05}"


def sql(query, params=()):
    with psycopg.connect(DATABASE_URL) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture(autouse=True)
def cleanup():
    yield
    sql(
        "DELETE FROM candidates WHERE ST_DWithin(center, ST_MakePoint(%s, %s)::geography, 20000)",
        (LON, LAT),
    )


def new_session() -> dict:
    r = client.post("/v1/sessions", json={"consent": True})
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['token']}"}


def add_candidate(source="detected") -> int:
    return sql(
        "INSERT INTO candidates (source, center) VALUES (%s, ST_MakePoint(%s, %s)::geography)"
        " RETURNING id",
        (source, LON, LAT),
    )[0][0]


def answer(h, cid, kind, note=None):
    return client.post(
        f"/v1/candidates/{cid}/answers", json={"kind": kind, "note": note}, headers=h
    )


def features(h, bbox=BBOX):
    return client.get("/v1/spots", params={"bbox": bbox}, headers=h).json()["features"]


def spot(h, cid):
    """地図に出ている候補の properties。出ていなければ None。"""
    return next(
        (f["properties"] for f in features(h) if f["properties"].get("candidate_id") == cid), None
    )


def question_ids(h, lat=LAT, lon=LON):
    r = client.get("/v1/questions", params={"lat": lat, "lon": lon}, headers=h)
    return [q["candidate_id"] for q in r.json()]


def count_points() -> int:
    return sql("SELECT count(*) FROM location_points")[0][0]


def test_session_requires_consent():
    assert client.post("/v1/sessions", json={"consent": False}).status_code == 422
    assert client.post("/v1/sessions", json={}).status_code == 422


def test_session_required():
    assert client.get("/v1/spots").status_code == 401
    assert client.get("/v1/spots", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_locations_and_delete_session():
    h = new_session()
    before = count_points()
    point = {"lat": LAT, "lon": LON, "accuracy_m": 10, "recorded_at": datetime.now(UTC).isoformat()}
    r = client.post("/v1/locations", json={"points": [point] * 3}, headers=h)
    assert r.json() == {"accepted": 3}
    assert count_points() == before + 3

    bad = {**point, "lat": 200}
    assert client.post("/v1/locations", json={"points": [bad]}, headers=h).status_code == 422
    naive = {**point, "recorded_at": "2026-07-29T09:00:00"}
    assert client.post("/v1/locations", json={"points": [naive]}, headers=h).status_code == 422

    assert client.delete("/v1/sessions/me", headers=h).status_code == 204
    assert count_points() == before
    assert client.get("/v1/spots", headers=h).status_code == 401


def test_detected_candidate_confirmed_by_one_answer():
    h = new_session()
    cid = add_candidate()
    assert cid in question_ids(h)
    assert cid not in question_ids(h, lat=LAT + 0.1)  # 約11km 先
    p = spot(h, cid)
    assert (p["source"], p["status"], p["top_kind"]) == ("detected", "pending", None)

    assert answer(h, cid, "water").status_code == 201
    p = spot(h, cid)
    assert (p["status"], p["kinds"], p["top_kind"]) == ("confirmed", {"water": 1}, "water")
    assert cid not in question_ids(h)

    assert answer(h, cid, "beer").status_code == 422
    assert answer(h, 0, "water").status_code == 404


def test_answer_is_overwritten():
    h = new_session()
    cid = add_candidate()
    assert answer(h, cid, "water", "12時まで").status_code == 201
    assert answer(h, cid, "food").status_code == 201
    p = spot(h, cid)
    assert (p["kinds"], p["notes"]) == ({"food": 1}, [])  # 種類を変えたら備考も入れ替わる

    answer(h, cid, "food", "容器を持参")
    answer(h, cid, "food")  # 「まだやっている」: 備考を書かなければ前の備考を残す
    assert [n["note"] for n in spot(h, cid)["notes"]] == ["容器を持参"]


def test_top_kind_tie_goes_to_first_answer():
    h1, h2 = new_session(), new_session()
    cid = add_candidate()
    answer(h1, cid, "food")
    answer(h2, cid, "water")
    assert spot(h1, cid)["top_kind"] == "food"


def test_ended_by_majority():
    h1, h2, h3 = new_session(), new_session(), new_session()
    cid = add_candidate()
    answer(h1, cid, "water")
    answer(h2, cid, "ended")  # 1対1の同数 → 終了
    assert spot(h1, cid) is None
    answer(h3, cid, "water")  # 2対1 → また地図に出る
    assert spot(h1, cid)["status"] == "confirmed"


def test_fade_and_hide():
    h = new_session()
    cid = add_candidate()
    answer(h, cid, "water")
    assert spot(h, cid)["faded"] is False
    sql("UPDATE candidates SET last_activity_at = now() - interval '4 hours' WHERE id = %s", (cid,))
    assert spot(h, cid)["faded"] is True
    sql("UPDATE candidates SET last_activity_at = now() - interval '7 hours' WHERE id = %s", (cid,))
    assert spot(h, cid) is None

    old = add_candidate()
    sql("UPDATE candidates SET last_activity_at = now() - interval '7 hours' WHERE id = %s", (old,))
    assert old not in question_ids(h)


def test_spots_bbox_and_geometry():
    h = new_session()
    cid = add_candidate()
    f = next(f for f in features(h) if f["properties"].get("candidate_id") == cid)
    assert f["geometry"]["coordinates"] == [LON, LAT]
    assert cid not in [f["properties"].get("candidate_id") for f in features(h, "0,0,1,1")]
    assert client.get("/v1/spots", params={"bbox": "x"}, headers=h).status_code == 422


def test_note_validation():
    h = new_session()
    cid = add_candidate()
    r = answer(h, cid, "water", "詳細は https://example.com")
    assert r.status_code == 422 and "URL" in r.text
    assert answer(h, cid, "water", "あ" * 101).status_code == 422
    assert answer(h, cid, "water", "0965-33-4111 まで").status_code == 422
```

- [ ] **Step 2: 失敗することを確認する**

Run: `docker compose run --rm api pytest tests/test_api.py -q`
Expected: FAIL（`add_candidate` が `column "source" of relation "candidates" does not exist` で落ちるなど）。セッション・位置の3つのテストは通る

- [ ] **Step 3: 表の定義を置き換える**

`db/init.sql` を次の内容で置き換える（`sessions`・`location_points` は今と同じ）:

```sql
-- ponytail: 初回起動時だけ実行される。スキーマ変更が出たら移行ツール（Alembic 等）を入れる
CREATE EXTENSION IF NOT EXISTS postgis;

-- アカウントの代わりの匿名セッション。トークン本体は持たず SHA-256 だけ保存する
CREATE TABLE sessions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash    bytea NOT NULL UNIQUE,
    consented_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE location_points (
    id           bigserial PRIMARY KEY,
    session_id   uuid NOT NULL REFERENCES sessions ON DELETE CASCADE,
    geom         geography(Point, 4326) NOT NULL,
    accuracy_m   real CHECK (accuracy_m >= 0),
    recorded_at  timestamptz NOT NULL
);
CREATE INDEX ON location_points (session_id, recorded_at);
CREATE INDEX ON location_points USING gist (geom);

-- 非公式の拠点の候補。人の集まりの検知（detected）か、利用者の報告（report）から生まれる
CREATE TABLE candidates (
    id                bigserial PRIMARY KEY,
    source            text NOT NULL CHECK (source IN ('detected', 'report')),
    center            geography(Point, 4326) NOT NULL,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'confirmed', 'ended')),
    last_activity_at  timestamptz NOT NULL DEFAULT now(),  -- 最後に確認された時刻
    created_at        timestamptz NOT NULL DEFAULT now(),
    -- ここから下は検知（detected）のときだけ入る
    cell_code         text,  -- 1/2 地域メッシュ（500m）の番号
    window_start      timestamptz,
    observed          integer,
    expected          double precision,
    p_value           double precision,
    UNIQUE (cell_code, window_start)
);
CREATE INDEX ON candidates USING gist (center);

-- 「ここに何がある？」への回答と、利用者の報告。1人1候補1件の最新の意見
CREATE TABLE answers (
    id            bigserial PRIMARY KEY,
    candidate_id  bigint NOT NULL REFERENCES candidates ON DELETE CASCADE,
    session_id    uuid NOT NULL REFERENCES sessions ON DELETE CASCADE,
    kind          text NOT NULL CHECK (kind IN (
                      'water', 'food', 'goods', 'car_sleeping', 'shelter', 'other',
                      'none', 'ended')),
    note          text CHECK (char_length(note) <= 100),
    answered_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, session_id)
);

-- 備考の「不適切」通報。1人1備考1回
CREATE TABLE flags (
    answer_id   bigint NOT NULL REFERENCES answers ON DELETE CASCADE,
    session_id  uuid NOT NULL REFERENCES sessions ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (answer_id, session_id)
);

-- 公式の拠点。取り込みは scripts/import_bodik.py
CREATE TABLE official_spots (
    id          bigserial PRIMARY KEY,
    source      text NOT NULL,  -- 取り込み元。例: bodik-kumamoto-r8
    source_key  text NOT NULL,  -- 取り込み元の中で一意なキー。BODIK は「市町村コード:名前」
    name        text NOT NULL,
    address     text,
    kind        text NOT NULL CHECK (kind IN ('shelter', 'water')),
    geom        geography(Point, 4326) NOT NULL,
    opened_at   timestamptz,  -- 開設されなかった拠点は NULL
    closed_at   timestamptz,  -- 開設中は NULL
    UNIQUE (source, source_key)
);
CREATE INDEX ON official_spots USING gist (geom);
```

- [ ] **Step 4: DB の読み書きを書く**

`app/spots.py`:

```python
"""非公式の拠点（候補）と公式拠点の読み書き。ルールの数値は app/rules.py。"""

from uuid import UUID

import psycopg

from app import rules

Bbox = tuple[float, float, float, float]  # 西, 南, 東, 北

# 地図に出す候補: 終了しておらず、最後の確認から HIDE_AFTER 以内
ALIVE = "c.status <> 'ended' AND c.last_activity_at > now() - %(hide)s"


def record_answer(
    conn: psycopg.Connection, candidate_id: int, sid: UUID, kind: str, note: str | None
) -> None:
    """回答を記録し（1人1件、上書き）、候補の状態を決め直す。

    候補がなければ psycopg.errors.ForeignKeyViolation。
    """
    conn.execute(
        """
        INSERT INTO answers (candidate_id, session_id, kind, note)
        VALUES (%(cid)s, %(sid)s, %(kind)s, %(note)s)
        ON CONFLICT (candidate_id, session_id) DO UPDATE SET
            -- 同じ種類での再回答（「まだやっている」）で備考がなければ、前の備考を残す
            note = CASE WHEN answers.kind = EXCLUDED.kind
                        THEN coalesce(EXCLUDED.note, answers.note) ELSE EXCLUDED.note END,
            kind = EXCLUDED.kind,
            answered_at = now()
        """,
        {"cid": candidate_id, "sid": sid, "kind": kind, "note": note},
    )
    conn.execute(
        """
        UPDATE candidates c SET
            last_activity_at = CASE WHEN %(active)s THEN now() ELSE c.last_activity_at END,
            status = CASE
                WHEN s.ended > 0 AND s.ended >= s.active THEN 'ended'
                WHEN s.active >= CASE c.source WHEN 'detected' THEN %(need_detected)s
                                               ELSE %(need_report)s END THEN 'confirmed'
                ELSE 'pending'
            END
        FROM (SELECT count(*) FILTER (WHERE kind = ANY(%(kinds)s)) AS active,
                     count(*) FILTER (WHERE kind = 'ended') AS ended
              FROM answers WHERE candidate_id = %(cid)s) s
        WHERE c.id = %(cid)s
        """,
        {
            "cid": candidate_id,
            "active": kind in rules.KINDS,
            "kinds": list(rules.KINDS),
            "need_detected": rules.CONFIRM_ANSWERS["detected"],
            "need_report": rules.CONFIRM_ANSWERS["report"],
        },
    )


def questions(
    conn: psycopg.Connection, sid: UUID, lat: float, lon: float
) -> list[tuple[int, float, float]]:
    """近くの未確認の候補のうち、この人がまだ答えていないもの。(id, lat, lon) を近い順に。"""
    return conn.execute(
        f"""
        SELECT c.id, ST_Y(c.center::geometry), ST_X(c.center::geometry)
        FROM candidates c
        WHERE {ALIVE} AND c.status = 'pending'
          AND ST_DWithin(c.center, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(r)s)
          AND NOT EXISTS (
              SELECT 1 FROM answers a WHERE a.candidate_id = c.id AND a.session_id = %(sid)s)
        ORDER BY ST_Distance(c.center, ST_MakePoint(%(lon)s, %(lat)s)::geography)
        """,
        {
            "lat": lat,
            "lon": lon,
            "r": rules.QUESTION_RADIUS_M,
            "sid": sid,
            "hide": rules.HIDE_AFTER,
        },
    ).fetchall()


def unofficial_features(conn: psycopg.Connection, bbox: Bbox | None) -> list[dict]:
    """地図に出す候補を GeoJSON の Feature にする。"""
    where, params = _bbox_filter("c.center", bbox)
    rows = conn.execute(
        f"""
        SELECT c.id, c.source, c.status, ST_X(c.center::geometry), ST_Y(c.center::geometry),
               c.last_activity_at, c.last_activity_at < now() - %(fade)s,
               coalesce(k.kinds, jsonb_build_object()), k.top_kind,
               coalesce(n.notes, jsonb_build_array())
        FROM candidates c
        LEFT JOIN LATERAL (
            -- 種類ごとの人数と、一番多い種類（同数なら先に回答が付いた種類）
            SELECT jsonb_object_agg(kind, n) AS kinds,
                   (array_agg(kind ORDER BY n DESC, first_at))[1] AS top_kind
            FROM (SELECT kind, count(*) AS n, min(answered_at) AS first_at
                  FROM answers WHERE candidate_id = c.id AND kind = ANY(%(kinds)s)
                  GROUP BY kind) t
        ) k ON true
        LEFT JOIN LATERAL (
            -- 通報が NOTE_HIDE_FLAGS 件に達した備考は出さない
            SELECT jsonb_agg(jsonb_build_object('answer_id', a.id, 'kind', a.kind,
                                                'note', a.note, 'answered_at', a.answered_at)
                             ORDER BY a.answered_at DESC) AS notes
            FROM answers a
            WHERE a.candidate_id = c.id AND a.note IS NOT NULL
              AND (SELECT count(*) FROM flags f WHERE f.answer_id = a.id) < %(hide_flags)s
        ) n ON true
        WHERE {ALIVE} {where}
        """,
        {
            "fade": rules.FADE_AFTER,
            "hide": rules.HIDE_AFTER,
            "kinds": list(rules.KINDS),
            "hide_flags": rules.NOTE_HIDE_FLAGS,
            **params,
        },
    ).fetchall()
    return [
        _feature(
            lon,
            lat,
            {
                "source": source,
                "status": status,
                "candidate_id": cid,
                "kinds": kinds,  # 例 {"water": 3, "food": 1}
                "top_kind": top_kind,  # 回答がなければ None
                "last_activity_at": last_activity_at.isoformat(),
                "faded": faded,
                "notes": notes,  # [{answer_id, kind, note, answered_at}]、新しい順
            },
        )
        for cid, source, status, lon, lat, last_activity_at, faded, kinds, top_kind, notes in rows
    ]


def _bbox_filter(column: str, bbox: Bbox | None) -> tuple[str, dict]:
    if bbox is None:
        return "", {}
    w, s, e, n = bbox
    where = f"AND {column}::geometry && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)"
    return where, {"w": w, "s": s, "e": e, "n": n}


def _feature(lon: float, lat: float, properties: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": properties,
    }
```

- [ ] **Step 5: ルーティングを置き換える**

`app/main.py` を次の内容で置き換える（`/v1/questions` の応答から `window_start` がなくなる。報告の候補には検知の時刻がないため）:

```python
from datetime import UTC, datetime
from typing import Annotated, Literal

import psycopg
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field

from app import rules, spots
from app.auth import SESSION_TTL, Session, create_session
from app.db import Conn

app = FastAPI(title="Aid Spot Finder API", version="0.1.0")

Lat = Annotated[float, Field(ge=-90, le=90)]
Lon = Annotated[float, Field(ge=-180, le=180)]
Note = Annotated[str | None, AfterValidator(rules.clean_note)]
AnswerKind = rules.Kind | Literal["none", "ended"]


class SessionCreate(BaseModel):
    consent: Literal[True]  # 参加は同意制


class SessionOut(BaseModel):
    token: str
    expires_at: datetime  # 使うたびに延びる


class LocationPoint(BaseModel):
    lat: Lat
    lon: Lon
    accuracy_m: float | None = Field(None, ge=0)
    recorded_at: AwareDatetime


class LocationBatch(BaseModel):
    points: list[LocationPoint] = Field(min_length=1, max_length=1000)


class Accepted(BaseModel):
    accepted: int


class Question(BaseModel):
    candidate_id: int
    lat: float
    lon: float


class AnswerIn(BaseModel):
    kind: AnswerKind  # none = わからない、ended = もう終わっていた
    note: Note = None


def parse_bbox(bbox: str | None) -> spots.Bbox | None:
    if bbox is None:
        return None
    try:
        w, s, e, n = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(422, "bbox must be minLon,minLat,maxLon,maxLat")
    return w, s, e, n


@app.get("/healthz")
def healthz(conn: Conn) -> dict:
    conn.execute("SELECT 1")
    return {"ok": True}


@app.post("/v1/sessions", status_code=status.HTTP_201_CREATED)
def post_session(body: SessionCreate, conn: Conn) -> SessionOut:
    return SessionOut(token=create_session(conn), expires_at=datetime.now(UTC) + SESSION_TTL)


@app.delete("/v1/sessions/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(conn: Conn, sid: Session) -> None:
    # 位置・回答・通報も ON DELETE CASCADE で消える
    conn.execute("DELETE FROM sessions WHERE id = %s", (sid,))


@app.post("/v1/locations", status_code=status.HTTP_202_ACCEPTED)
def post_locations(body: LocationBatch, conn: Conn, sid: Session) -> Accepted:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO location_points (session_id, geom, accuracy_m, recorded_at)
            VALUES (%s, ST_MakePoint(%s, %s)::geography, %s, %s)
            """,
            [(sid, p.lon, p.lat, p.accuracy_m, p.recorded_at) for p in body.points],
        )
    return Accepted(accepted=len(body.points))


@app.get("/v1/questions")
def get_questions(
    lat: Annotated[float, Query(ge=-90, le=90)],
    lon: Annotated[float, Query(ge=-180, le=180)],
    conn: Conn,
    sid: Session,
) -> list[Question]:
    rows = spots.questions(conn, sid, lat, lon)
    return [Question(candidate_id=cid, lat=la, lon=lo) for cid, la, lo in rows]


@app.post("/v1/candidates/{candidate_id}/answers", status_code=status.HTTP_201_CREATED)
def post_answer(candidate_id: int, body: AnswerIn, conn: Conn, sid: Session) -> AnswerIn:
    try:
        spots.record_answer(conn, candidate_id, sid, body.kind, body.note)
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate not found")
    return body


@app.get("/v1/spots")
def get_spots(
    conn: Conn,
    sid: Session,
    bbox: Annotated[str | None, Query(description="minLon,minLat,maxLon,maxLat")] = None,
) -> dict:
    """非公式の拠点（確認済み・未確認）を GeoJSON の FeatureCollection で返す。"""
    return {
        "type": "FeatureCollection",
        "features": spots.unofficial_features(conn, parse_bbox(bbox)),
    }
```

- [ ] **Step 6: DB を作り直す**

Run: `docker compose down -v && docker compose up -d --wait`
Expected: 最後に `Container claudecodehackason2026-api-1 Healthy`（または `Started`）

- [ ] **Step 7: 通ることを確認する**

Run: `docker compose run --rm api pytest -q`
Expected: `17 passed`（test_rules 7 ＋ test_api 10）

- [ ] **Step 8: コミットする**

```bash
git add db/init.sql app/spots.py app/main.py tests/test_api.py
git commit -m "回答の上書き・確定と終了のルール・薄く表示/消す、を API に追加

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 自分から知らせる（`POST /v1/reports`）

**Files:**
- Modify: `app/spots.py`（関数を追加）
- Modify: `app/main.py`（型とルートを追加）
- Test: `tests/test_api.py`（テストを追加）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_api.py` の末尾に追加する:

```python
def report(h, kind, lat=LAT, lon=LON, note=None):
    body = {"lat": lat, "lon": lon, "kind": kind, "note": note}
    return client.post("/v1/reports", json=body, headers=h)


def test_report_needs_second_person():
    h1, h2 = new_session(), new_session()
    r = report(h1, "water", note="12時まで")
    assert r.status_code == 201
    cid = r.json()["candidate_id"]
    p = spot(h1, cid)
    assert (p["source"], p["status"], p["top_kind"]) == ("report", "pending", "water")
    assert [n["note"] for n in p["notes"]] == ["12時まで"]
    assert cid not in question_ids(h1)  # 報告した本人には聞かない
    assert cid in question_ids(h2)

    # 約50m 先からの報告は、同じ候補にまとまる
    assert report(h2, "water", lat=LAT + 0.0005).json()["candidate_id"] == cid
    p = spot(h1, cid)
    assert (p["status"], p["kinds"]) == ("confirmed", {"water": 2})

    # 約1km 先なら別の候補
    assert report(h1, "food", lat=LAT + 0.01).json()["candidate_id"] != cid


def test_report_merges_into_detected_candidate():
    h = new_session()
    cid = add_candidate()
    assert report(h, "water").json()["candidate_id"] == cid
    assert spot(h, cid)["status"] == "confirmed"  # 検知の候補は1人で確認済み


def test_report_rejects_bad_input():
    h = new_session()
    assert report(h, "none").status_code == 422  # 報告に「わからない」は使えない
    assert report(h, "water", lat=200).status_code == 422
    assert report(h, "water", note="www.example.com").status_code == 422
```

- [ ] **Step 2: 失敗することを確認する**

Run: `docker compose run --rm api pytest tests/test_api.py -q -k report`
Expected: FAIL（`/v1/reports` が 404 なので `KeyError: 'candidate_id'` や `assert 404 == 201`）

- [ ] **Step 3: 実装する**

`app/spots.py` の `questions` の後に追加する:

```python
def find_or_create_report(conn: psycopg.Connection, lat: float, lon: float) -> int:
    """MERGE_RADIUS_M 以内に地図に出ている候補があればその ID、なければ報告の候補を作る。"""
    params = {"lat": lat, "lon": lon, "r": rules.MERGE_RADIUS_M, "hide": rules.HIDE_AFTER}
    row = conn.execute(
        f"""
        SELECT c.id FROM candidates c
        WHERE {ALIVE}
          AND ST_DWithin(c.center, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(r)s)
        ORDER BY ST_Distance(c.center, ST_MakePoint(%(lon)s, %(lat)s)::geography)
        LIMIT 1
        """,
        params,
    ).fetchone()
    if row:
        return row[0]
    return conn.execute(
        "INSERT INTO candidates (source, center)"
        " VALUES ('report', ST_MakePoint(%(lon)s, %(lat)s)::geography) RETURNING id",
        params,
    ).fetchone()[0]
```

`app/main.py` の `class AnswerIn` の後に追加する:

```python
class ReportIn(BaseModel):
    lat: Lat
    lon: Lon
    kind: rules.Kind
    note: Note = None


class ReportOut(BaseModel):
    candidate_id: int
```

`app/main.py` の `post_answer` の後に追加する:

```python
@app.post("/v1/reports", status_code=status.HTTP_201_CREATED)
def post_report(body: ReportIn, conn: Conn, sid: Session) -> ReportOut:
    """今いる場所にあるものを知らせる。近くの候補にまとめるか、新しい候補を作る。"""
    cid = spots.find_or_create_report(conn, body.lat, body.lon)
    spots.record_answer(conn, cid, sid, body.kind, body.note)
    return ReportOut(candidate_id=cid)
```

- [ ] **Step 4: 通ることを確認する**

Run: `docker compose run --rm api pytest -q`
Expected: `20 passed`

- [ ] **Step 5: コミットする**

```bash
git add app/spots.py app/main.py tests/test_api.py
git commit -m "自分から知らせる API（POST /v1/reports）を追加

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 備考の通報（`POST /v1/answers/{id}/flags`）

**Files:**
- Modify: `app/main.py`（ルートを追加）
- Test: `tests/test_api.py`（テストを追加）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_api.py` の末尾に追加する:

```python
def flag(h, answer_id) -> int:
    return client.post(f"/v1/answers/{answer_id}/flags", headers=h).status_code


def test_flags_hide_note():
    h1, h2, h3 = new_session(), new_session(), new_session()
    cid = report(h1, "water", note="12時まで").json()["candidate_id"]
    aid = spot(h1, cid)["notes"][0]["answer_id"]

    assert flag(h2, aid) == 204
    assert flag(h2, aid) == 204  # 同じ人の2回目は数えない
    assert len(spot(h1, cid)["notes"]) == 1
    assert flag(h3, aid) == 204  # 2件目で非表示
    assert spot(h1, cid)["notes"] == []
    assert flag(h3, 0) == 404
```

- [ ] **Step 2: 失敗することを確認する**

Run: `docker compose run --rm api pytest tests/test_api.py -q -k flags`
Expected: FAIL（`assert 404 == 204`）

- [ ] **Step 3: 実装する**

`app/main.py` の `post_report` の後に追加する:

```python
@app.post("/v1/answers/{answer_id}/flags", status_code=status.HTTP_204_NO_CONTENT)
def post_flag(answer_id: int, conn: Conn, sid: Session) -> None:
    """備考を「不適切」と通報する。同じ人の2回目は何もしない。"""
    try:
        conn.execute(
            "INSERT INTO flags (answer_id, session_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (answer_id, sid),
        )
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "answer not found")
```

- [ ] **Step 4: 通ることを確認する**

Run: `docker compose run --rm api pytest -q`
Expected: `21 passed`

- [ ] **Step 5: コミットする**

```bash
git add app/main.py tests/test_api.py
git commit -m "備考の通報 API（POST /v1/answers/{id}/flags）を追加

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 公式の避難所（取り込みと `GET /v1/spots?at=`）

**Files:**
- Modify: `app/spots.py`（関数を追加）
- Modify: `app/main.py`（`get_spots` を置き換え）
- Create: `scripts/import_bodik.py`
- Test: `tests/test_api.py`（後片付けとテストを追加）
- Test: `tests/test_import_bodik.py`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_api.py` の `cleanup` を次の内容に置き換える（テスト用の公式拠点も消す）:

```python
@pytest.fixture(autouse=True)
def cleanup():
    yield
    sql(
        "DELETE FROM candidates WHERE ST_DWithin(center, ST_MakePoint(%s, %s)::geography, 20000)",
        (LON, LAT),
    )
    sql("DELETE FROM official_spots WHERE source = 'test'")
```

`tests/test_api.py` の末尾に追加する:

```python
def add_official(name, opened_at, closed_at):
    sql(
        "INSERT INTO official_spots (source, source_key, name, kind, geom, opened_at, closed_at)"
        " VALUES ('test', %s, %s, 'shelter', ST_MakePoint(%s, %s)::geography, %s, %s)",
        (name, name, LON, LAT, opened_at, closed_at),
    )


def official_names(h, at=None):
    params = {"bbox": BBOX} | ({"at": at} if at else {})
    feats = client.get("/v1/spots", params=params, headers=h).json()["features"]
    return sorted(f["properties"]["name"] for f in feats if f["properties"]["source"] == "official")


def test_official_spots_open_at():
    h = new_session()
    add_official("test-A", "2026-07-28T16:00:00+09:00", "2026-08-05T18:00:00+09:00")
    add_official("test-B", "2026-07-30T09:00:00+09:00", None)
    add_official("test-C", None, None)  # 開設されなかった

    assert official_names(h, "2026-07-29T12:00:00+09:00") == ["test-A"]
    assert official_names(h, "2026-08-10T12:00:00+09:00") == ["test-B"]
    assert official_names(h) == ["test-B"]  # 省略時は現在
    r = client.get("/v1/spots", params={"at": "2026-07-29T12:00:00"}, headers=h)
    assert r.status_code == 422  # タイムゾーンなしは受け付けない
```

`tests/test_import_bodik.py`:

```python
from scripts.import_bodik import rows


def feature(name, opened_at, lon=130.6):
    return {
        "geometry": {"coordinates": [lon, 32.5]},
        "properties": {"municipality_code": "432024", "name": name, "address": "八代市",
                       "opened_at": opened_at, "closed_at": None},
    }  # fmt: skip


def test_opened_row_comes_last_for_same_key():
    data = {
        "features": [
            feature("代陽", "2026-07-28T16:27:00+09:00", lon=130.59),
            feature("代陽", None),
        ]
    }
    out = rows(data)
    assert [r[1] for r in out] == ["432024:代陽", "432024:代陽"]
    assert out[-1][6] == "2026-07-28T16:27:00+09:00"  # 上書きで残るのは開設の行
    assert out[-1][4] == 130.59
```

- [ ] **Step 2: 失敗することを確認する**

Run: `docker compose run --rm api pytest tests/test_api.py tests/test_import_bodik.py -q -k "official or opened"`
Expected: FAIL（`assert [] == ['test-A']` と `ModuleNotFoundError: No module named 'scripts.import_bodik'`）

- [ ] **Step 3: 実装する**

`app/spots.py` の `unofficial_features` の後に追加する（ファイル先頭の import に `from datetime import datetime` を足す）:

```python
def official_features(
    conn: psycopg.Connection, bbox: Bbox | None, at: datetime | None
) -> list[dict]:
    """at の時点で開いていた公式拠点を GeoJSON の Feature にする。at が None なら現在。"""
    where, params = _bbox_filter("o.geom", bbox)
    rows = conn.execute(
        f"""
        SELECT o.id, o.kind, o.name, o.address, ST_X(o.geom::geometry), ST_Y(o.geom::geometry),
               o.opened_at, o.closed_at
        FROM official_spots o
        CROSS JOIN (SELECT coalesce(%(at)s::timestamptz, now()) AS at) t
        WHERE o.opened_at <= t.at AND (o.closed_at IS NULL OR o.closed_at > t.at) {where}
        """,
        {"at": at, **params},
    ).fetchall()
    return [
        _feature(
            lon,
            lat,
            {
                "source": "official",
                "status": "open",
                "official_id": oid,
                "kind": kind,
                "name": name,
                "address": address,
                "opened_at": opened_at.isoformat(),
                "closed_at": closed_at and closed_at.isoformat(),
            },
        )
        for oid, kind, name, address, lon, lat, opened_at, closed_at in rows
    ]
```

`app/main.py` の `get_spots` を次の内容で置き換える:

```python
@app.get("/v1/spots")
def get_spots(
    conn: Conn,
    sid: Session,
    bbox: Annotated[str | None, Query(description="minLon,minLat,maxLon,maxLat")] = None,
    at: Annotated[AwareDatetime | None, Query(description="公式拠点をこの時点で絞る")] = None,
) -> dict:
    """非公式の拠点（確認済み・未確認）と公式の拠点を、1つの GeoJSON で返す。"""
    box = parse_bbox(bbox)
    return {
        "type": "FeatureCollection",
        "features": spots.unofficial_features(conn, box) + spots.official_features(conn, box, at),
    }
```

`scripts/import_bodik.py`:

```python
"""BODIK 令和8年熊本地震の避難所データを official_spots に取り込む。何度実行しても重複しない。

    docker compose run --rm api python scripts/import_bodik.py

データの利用条件を確認できるまで、データ自体はリポジトリに入れない。
"""

import json
import os
import urllib.request

import psycopg

URL = "https://odcs.bodik.jp/kumamoto-r8/data/portal/shelters.geojson"
SOURCE = "bodik-kumamoto-r8"


def rows(geojson: dict) -> list[tuple]:
    """取り込む行。キーは「市町村コード:名前」（BODIK のデータには ID がない）。

    同じ施設が「台帳の行」と「開設の行」の2行で入っていることがあるので、開設日時のある行を
    後ろに並べる。取り込みは後の行で上書きするので、開設の記録が残る。
    """
    features = sorted(
        geojson["features"], key=lambda f: f["properties"].get("opened_at") is not None
    )
    out = []
    for f in features:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        key = f"{p['municipality_code']}:{p['name']}"
        out.append(
            (
                SOURCE,
                key,
                p["name"],
                p.get("address"),
                lon,
                lat,
                p.get("opened_at"),
                p.get("closed_at"),
            )
        )
    return out


def main() -> None:
    with urllib.request.urlopen(URL, timeout=60) as res:
        data = rows(json.load(res))
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO official_spots
                (source, source_key, name, address, kind, geom, opened_at, closed_at)
            VALUES (%s, %s, %s, %s, 'shelter', ST_MakePoint(%s, %s)::geography, %s, %s)
            ON CONFLICT (source, source_key) DO UPDATE SET
                name = EXCLUDED.name, address = EXCLUDED.address, geom = EXCLUDED.geom,
                opened_at = EXCLUDED.opened_at, closed_at = EXCLUDED.closed_at
            """,
            data,
        )
    print(f"{len(data)} 件を取り込みました（{URL}）")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 通ることを確認する**

Run: `docker compose run --rm api pytest -q`
Expected: `23 passed`

- [ ] **Step 5: 取り込みを実行して確かめる**

Run: `docker compose run --rm api python scripts/import_bodik.py`
Expected: `2127 件を取り込みました（https://odcs.bodik.jp/...）`（件数は BODIK 側の更新で変わりうる）

Run: `docker compose exec db psql -U app -c "SELECT count(*) FROM official_spots WHERE source_key LIKE '432024:%' AND opened_at IS NOT NULL"`
Expected: 開設された八代市（市町村コード 432024）の避難所の数。2026-11-02 の確認時点で 65（CLAUDE.md の「最終的に65か所」と一致）

- [ ] **Step 6: コミットする**

```bash
git add app/spots.py app/main.py scripts/import_bodik.py tests/test_api.py tests/test_import_bodik.py
git commit -m "公式の避難所（BODIK）の取り込みと GET /v1/spots?at= を追加

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 画面の骨組み（HTML・CSS・PWA・配信）

**Files:**
- Create: `web/index.html` / `web/style.css` / `web/manifest.webmanifest` / `web/icon.svg` / `web/icon-180.png` / `web/icon-192.png` / `web/icon-512.png`
- Create: `web/app.js`（このタスクでは空のファイル。中身は Task 7）
- Modify: `app/main.py`（`web/` の配信を末尾に追加）
- Test: `tests/test_api.py`（テストを追加）

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_api.py` の末尾に追加する:

```python
def test_web_is_served():
    r = client.get("/")
    assert r.status_code == 200 and 'id="map"' in r.text
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
```

- [ ] **Step 2: 失敗することを確認する**

Run: `docker compose run --rm api pytest tests/test_api.py -q -k web`
Expected: FAIL（`assert 404 == 200`）

- [ ] **Step 3: HTML を書く**

`web/index.html`:

```html
<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>支援拠点マップ</title>
  <meta name="description" content="災害時、人の集まりから公式に載らない給水・炊き出しの場所を見つけて地図に載せます">
  <meta name="theme-color" content="#1e3a5f">
  <link rel="manifest" href="manifest.webmanifest">
  <link rel="icon" href="icon.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="icon-180.png">
  <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.css">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <div id="map" aria-label="地図"></div>

  <header class="top">
    <div class="chips" role="group" aria-label="絞り込み">
      <button type="button" class="chip" data-filter="all" aria-pressed="true">すべて</button>
      <button type="button" class="chip" data-filter="water" aria-pressed="false">水</button>
      <button type="button" class="chip" data-filter="food" aria-pressed="false">食</button>
      <button type="button" class="chip" data-filter="shelter" aria-pressed="false">避難</button>
    </div>
    <button type="button" id="menu-btn" class="round" aria-label="メニュー">≡</button>
  </header>

  <p id="banner" class="banner" role="status" hidden>接続できません。位置は端末にためて、つながったら送ります。</p>

  <section id="question" class="card" aria-live="polite" hidden>
    <button type="button" id="question-close" class="close" aria-label="閉じる">×</button>
    <h2>近くで人が集まっています</h2>
    <p>ここに何がありますか？</p>
    <div id="question-kinds"></div>
    <p id="question-error" class="error" role="alert"></p>
  </section>

  <nav class="dock" aria-label="知らせる">
    <h2>ここにあるものを知らせる</h2>
    <div id="dock-kinds"></div>
    <p id="location-hint" class="hint" hidden></p>
  </nav>

  <dialog id="consent" class="center">
    <div class="sheet-body">
      <h1>支援拠点マップ</h1>
      <p>災害のとき、人の集まりから、公式に載らない給水や炊き出しの場所を見つけて地図に載せます。</p>
      <ul>
        <li>送るのは位置だけです。名前や連絡先は送りません（匿名）。</li>
        <li>少人数の集まりは地図に表示しません。</li>
        <li>メニューからいつでも参加をやめ、送ったデータを消せます。</li>
      </ul>
      <button type="button" id="consent-ok" class="btn btn--primary">同意して始める</button>
      <p id="consent-error" class="error" role="alert"></p>
    </div>
  </dialog>

  <dialog id="detail" class="sheet">
    <div class="sheet-body">
      <h2 id="detail-title"></h2>
      <div id="detail-body"></div>
      <p id="detail-error" class="error" role="alert"></p>
      <div class="actions"><button type="button" class="btn" data-close>閉じる</button></div>
    </div>
  </dialog>

  <dialog id="report" class="sheet">
    <form id="report-form" class="sheet-body">
      <h2>今いる場所にあるものは？</h2>
      <div id="report-kinds"></div>
      <label for="report-note">備考（任意・100文字まで）</label>
      <textarea id="report-note" maxlength="100" rows="2" placeholder="例: 12時まで。容器を持参"></textarea>
      <p id="report-error" class="error" role="alert"></p>
      <div class="actions">
        <button type="button" class="btn" data-close>やめる</button>
        <button type="submit" class="btn btn--primary">送信する</button>
      </div>
    </form>
  </dialog>

  <dialog id="menu" class="sheet">
    <div class="sheet-body">
      <h2>メニュー</h2>
      <details>
        <summary>このアプリについて</summary>
        <p>アプリ利用者の匿名の位置から「いつもと違う人の集まり」を見つけ、近くの人に「ここに何がある？」と聞いて、公式に載らない支援拠点を地図に載せます。自分から知らせることもできます。</p>
      </details>
      <details>
        <summary>データの扱い</summary>
        <ul>
          <li>画面を開いている間、約1分ごとに位置を送ります。</li>
          <li>位置は、人の集まりを見つけるためだけに使います。</li>
          <li>最後に使ってから30日で参加は切れます。</li>
          <li>ピンと備考は、最後の確認から6時間で地図から消えます。</li>
        </ul>
      </details>
      <button type="button" id="leave" class="btn btn--danger">参加をやめてデータを消す</button>
      <p id="menu-error" class="error" role="alert"></p>
      <p class="credits">
        地図: © OpenFreeMap © OpenMapTiles © OpenStreetMap contributors<br>
        公式の避難所: BODIK 令和8年熊本地震オープンデータポータル<br>
        <a href="https://github.com/l4p1s/Built-with-Claude-Japan-2026_Lapis" target="_blank" rel="noopener">GitHub</a>
      </p>
      <div class="actions"><button type="button" class="btn" data-close>閉じる</button></div>
    </div>
  </dialog>

  <script type="module" src="app.js"></script>
</body>
</html>
```

- [ ] **Step 4: CSS を書く**

`web/style.css`:

```css
/* 色はすべてここで決める。ダークモードは端末の設定に合わせて自動で切り替える */
:root {
  color-scheme: light dark;
  --accent: #1e3a5f;
  --on-accent: #fff;
  --pending: #ea8a00;
  --on-pending: #fff;
  --surface: #fff;
  --surface-2: #f1f5f9;
  --fg: #0f172a;
  --muted: #64748b;
  --border: #cbd5e1;
  --danger: #b91c1c;
  --shadow: 0 2px 10px rgb(0 0 0 / 0.15);
  font-family: system-ui, -apple-system, "Hiragino Sans", "Noto Sans JP", sans-serif;
}

@media (prefers-color-scheme: dark) {
  :root {
    --accent: #60a5fa;
    --on-accent: #0b1220;
    --pending: #f59e0b;
    --on-pending: #0b1220;
    --surface: #111827;
    --surface-2: #1f2937;
    --fg: #e5e7eb;
    --muted: #94a3b8;
    --border: #374151;
    --danger: #f87171;
    --shadow: 0 2px 10px rgb(0 0 0 / 0.5);
  }
}

* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; background: var(--surface); color: var(--fg); }
#map { position: fixed; inset: 0; }
button { font: inherit; color: inherit; cursor: pointer; }
button:disabled { opacity: 0.45; cursor: not-allowed; }
:focus-visible { outline: 3px solid var(--pending); outline-offset: 2px; }

/* 地図の出典表示を下のドックに隠さない（--dock-h は app.js が入れる） */
.maplibregl-ctrl-bottom-left, .maplibregl-ctrl-bottom-right { bottom: var(--dock-h, 0px); }

/* 上部: 絞り込みとメニュー */
.top {
  position: fixed; z-index: 2; top: calc(10px + env(safe-area-inset-top)); left: 10px; right: 10px;
  display: flex; gap: 6px; align-items: center;
}
.chips { display: flex; gap: 6px; overflow-x: auto; padding: 2px; }
.chip {
  min-height: 44px; padding: 0 14px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--surface); box-shadow: var(--shadow); font-weight: 600; white-space: nowrap;
}
.chip[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
.round {
  flex: none; margin-left: auto; width: 44px; height: 44px; border-radius: 50%; border: 0;
  background: var(--surface); box-shadow: var(--shadow); font-size: 20px;
}

/* 通信できないときの帯と、「ここに何がある？」のカード */
.banner, .card {
  position: fixed; z-index: 3; top: calc(64px + env(safe-area-inset-top)); left: 10px; right: 10px;
  max-width: 520px; margin: 0 auto; border-radius: 14px; box-shadow: var(--shadow);
}
.banner { padding: 8px 12px; background: var(--danger); color: #fff; font-size: 14px; }
.card { padding: 12px 14px; background: var(--surface); border-left: 5px solid var(--pending); }
.card h2 { margin: 0 0 2px; font-size: 16px; }
.card p { margin: 0 0 8px; }
.close {
  position: absolute; top: 2px; right: 2px; width: 44px; height: 44px;
  border: 0; background: none; font-size: 20px; color: var(--muted);
}

/* 下部: 知らせるボタン */
.dock {
  position: fixed; z-index: 2; left: 0; right: 0; bottom: 0; max-width: 640px; margin: 0 auto;
  padding: 12px 12px calc(14px + env(safe-area-inset-bottom));
  border-radius: 18px 18px 0 0; background: var(--surface); box-shadow: 0 -2px 12px rgb(0 0 0 / 0.15);
}
.dock h2 { margin: 0 0 8px; font-size: 14px; }
.hint { margin: 8px 0 0; font-size: 13px; color: var(--muted); }

.kinds { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.kind {
  min-height: 56px; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 2px; border: 1px solid transparent; border-radius: 12px; background: var(--surface-2);
  font-size: 13px; font-weight: 600;
}
.kind b { font-size: 18px; font-weight: normal; }
.kind[aria-pressed="true"] { background: var(--accent); color: var(--on-accent); }

/* ピン。MapLibre が外側の要素の transform を使うので、回転は内側でかける */
.pin { width: 32px; height: 40px; padding: 0; border: 0; background: none; }
.pin__shape {
  display: flex; align-items: center; justify-content: center; width: 30px; height: 30px; margin: 0 auto;
  border-radius: 50% 50% 50% 0; transform: rotate(-45deg); box-shadow: 0 2px 4px rgb(0 0 0 / 0.3);
}
.pin__text { transform: rotate(45deg); font-size: 13px; font-weight: 700; }
.pin--confirmed .pin__shape { background: var(--accent); color: var(--on-accent); }
.pin--pending .pin__shape { background: var(--pending); color: var(--on-pending); }
.pin--official .pin__shape { background: var(--surface); color: var(--accent); border: 2px solid var(--accent); }
.pin--faded { opacity: 0.45; }
.me {
  width: 16px; height: 16px; border-radius: 50%; background: #2563eb; border: 3px solid #fff;
  box-shadow: 0 0 0 8px rgb(37 99 235 / 0.22);
}

/* シート（下から出る）と中央のダイアログ */
dialog { border: 0; padding: 0; color: var(--fg); background: var(--surface); }
dialog::backdrop { background: rgb(0 0 0 / 0.35); }
dialog.sheet { width: 100%; max-width: 640px; max-height: 85vh; margin: auto auto 0; border-radius: 18px 18px 0 0; }
dialog.center { width: calc(100% - 32px); max-width: 480px; border-radius: 18px; }
.sheet-body { padding: 18px 16px calc(18px + env(safe-area-inset-bottom)); }
.sheet-body h1 { margin: 0 0 8px; font-size: 22px; }
.sheet-body h2 { margin: 0 0 10px; font-size: 17px; }
.sheet-body p { margin: 0 0 8px; }

.btn {
  min-height: 44px; padding: 0 18px; border-radius: 12px; border: 1px solid var(--border);
  background: var(--surface-2); font-weight: 600;
}
.btn--primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
.btn--danger { width: 100%; margin: 12px 0 4px; background: none; color: var(--danger); border-color: var(--danger); }
#consent-ok { width: 100%; margin-top: 8px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; margin: 12px 0 0; }
label { display: block; margin: 12px 0 4px; font-size: 13px; color: var(--muted); }
textarea {
  width: 100%; padding: 8px; font: inherit; border-radius: 10px; border: 1px solid var(--border);
  background: var(--surface); color: var(--fg);
}
.error { min-height: 1em; margin: 6px 0; color: var(--danger); font-size: 13px; }
.note { display: flex; gap: 8px; align-items: start; justify-content: space-between; padding: 8px 0; border-top: 1px solid var(--border); }
.note p { margin: 0; }
.link { flex: none; min-height: 44px; border: 0; background: none; color: var(--muted); text-decoration: underline; font-size: 12px; }
details { padding: 10px 0; border-bottom: 1px solid var(--border); }
summary { display: flex; align-items: center; min-height: 44px; font-weight: 600; cursor: pointer; }
.credits { margin-top: 12px; font-size: 12px; color: var(--muted); }
.credits a { color: inherit; }
```

- [ ] **Step 5: PWA のファイルを書く**

`web/manifest.webmanifest`:

```json
{
  "name": "支援拠点マップ",
  "short_name": "支援マップ",
  "start_url": "/",
  "display": "standalone",
  "lang": "ja",
  "background_color": "#ffffff",
  "theme_color": "#1e3a5f",
  "icons": [
    { "src": "icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "icon-512.png", "sizes": "512x512", "type": "image/png" },
    { "src": "icon.svg", "sizes": "any", "type": "image/svg+xml" }
  ]
}
```

`web/icon.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="96" fill="#1e3a5f"/>
  <path d="M256 96c-71 0-128 57-128 128 0 92 128 192 128 192s128-100 128-192c0-71-57-128-128-128z" fill="#fff"/>
  <circle cx="256" cy="224" r="48" fill="#1e3a5f"/>
</svg>
```

PNG のアイコンを同じ形で作る（ホストの Pillow を使う。リポジトリには PNG だけを入れる）:

Run:

```bash
python3 - <<'EOF'
from PIL import Image, ImageDraw

for size in (180, 192, 512):
    s = size / 512
    img = Image.new("RGB", (size, size), "#1e3a5f")  # iPhone は角を自動で丸めるので四角のまま
    d = ImageDraw.Draw(img)
    d.ellipse([128 * s, 96 * s, 384 * s, 352 * s], fill="white")
    d.polygon([(146 * s, 290 * s), (366 * s, 290 * s), (256 * s, 416 * s)], fill="white")
    d.ellipse([208 * s, 176 * s, 304 * s, 272 * s], fill="#1e3a5f")
    img.save(f"web/icon-{size}.png")
EOF
ls -l web/icon-*.png
```

Expected: `icon-180.png`・`icon-192.png`・`icon-512.png` の3つ

空の `web/app.js` を作る:

Run: `: > web/app.js`

- [ ] **Step 6: `web/` を配信する**

`app/main.py` の先頭の import に次を足す:

```python
import mimetypes
from pathlib import Path

from fastapi.staticfiles import StaticFiles
```

`app/main.py` の末尾に追加する（API のルートより後に登録する。先に登録すると `/v1` も静的ファイル扱いになる）:

```python
# 画面（web/）を / で配信する。API と同じオリジンなので CORS の設定は要らない
mimetypes.add_type("application/manifest+json", ".webmanifest")
app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "web", html=True), name="web")
```

- [ ] **Step 7: 通ることを確認する**

Run: `docker compose run --rm api pytest -q`
Expected: `24 passed`

- [ ] **Step 8: コミットする**

```bash
git add web app/main.py tests/test_api.py
git commit -m "Web 画面の骨組み（HTML・CSS・PWA）と配信を追加

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 画面の動き（`web/app.js`）

画面には自動テストを入れない（設計書 3.4）。ブラウザで動かして確かめる。

**Files:**
- Modify: `web/app.js`（全体を書く）

- [ ] **Step 1: `web/app.js` を書く**

```js
// 支援拠点マップ（Web 版）。REST API（/v1）のクライアント。設計: docs/web-ui-design.md
import * as maplibregl from "https://unpkg.com/maplibre-gl@6.16.0/dist/maplibre-gl.mjs";

const KIND_LABEL = {
  water: "給水",
  food: "食べ物",
  goods: "物資",
  car_sleeping: "宿泊・車中泊",
  shelter: "避難",
  other: "その他",
  none: "わからない",
  ended: "終わっていた",
};
const KIND_CHAR = { water: "水", food: "食", goods: "物", car_sleeping: "宿", shelter: "避", other: "他" };
const KIND_ICON = { water: "💧", food: "🍙", goods: "📦", car_sleeping: "🚗", shelter: "🏫", other: "…", none: "？" };
const REPORT_KINDS = ["water", "food", "goods", "car_sleeping", "shelter", "other"];
const QUESTION_KINDS = ["water", "food", "goods", "car_sleeping", "shelter", "none"];

const START = { center: [130.6016, 32.5072], zoom: 13 }; // 八代市役所
const MAP_STYLE = {
  light: "https://tiles.openfreemap.org/styles/positron",
  dark: "https://tiles.openfreemap.org/styles/dark",
};
const SEND_EVERY_MS = 60_000; // 位置を送り、質問と地図を更新する間隔
const RECORD_EVERY_MS = 30_000; // 位置を記録する間隔
const MAX_BUFFER = 1000; // 送れないときに端末にためる位置の上限（API の1回の上限と同じ）
const TOKEN_KEY = "aid-spot-token";

const $ = (selector) => document.querySelector(selector);

let token = loadToken();
let here = null; // 最新の現在地 { lat, lon }
let buffer = []; // まだ送っていない位置
let lastRecorded = 0;
let meMarker = null;
let tracking = false;
let spots = []; // 最後に受け取った GeoJSON の features
let markers = [];
let filter = "all";
let reportKind = null;
const dismissed = new Set(); // 閉じた質問の候補 ID。開き直すまで聞かない

// ---- セッションと API ----

function loadToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // 保存できない環境（プライベートモードなど）では、開いている間だけ使う
  }
}

function saveToken(value) {
  token = value;
  try {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // 同上
  }
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status; // 0 は通信できなかったとき
  }
}

async function api(path, { method = "GET", body } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(`/v1${path}`, { method, headers, body: body && JSON.stringify(body) });
  } catch {
    $("#banner").hidden = false;
    throw new ApiError(0, "接続できません");
  }
  $("#banner").hidden = true;
  if (res.status === 401) {
    saveToken(null);
    showConsent();
    throw new ApiError(401, "参加が切れました。もう一度同意してください");
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return res.status === 204 ? null : res.json();
}

async function errorMessage(res) {
  const data = await res.json().catch(() => ({}));
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail.map((d) => d.msg.replace(/^Value error, /, "")).join(" / ");
  }
  return `エラーが起きました（${res.status}）`;
}

// fn が失敗したら errorSelector の要素に理由を出す。成功したら true
async function run(fn, errorSelector) {
  const el = $(errorSelector);
  el.textContent = "";
  try {
    await fn();
    return true;
  } catch (e) {
    if (e.status !== 401) el.textContent = e.message;
    return false;
  }
}

// ---- 同意 ----

function showConsent() {
  if (!$("#consent").open) $("#consent").showModal();
}

$("#consent").addEventListener("cancel", (e) => e.preventDefault()); // Esc で閉じさせない

$("#consent-ok").addEventListener("click", async () => {
  const ok = await run(async () => {
    const session = await api("/sessions", { method: "POST", body: { consent: true } });
    saveToken(session.token);
  }, "#consent-error");
  if (!ok) return;
  $("#consent").close();
  start();
});

function start() {
  if (!tracking) {
    tracking = true;
    startTracking();
  }
  refreshSpots();
}

// ---- 地図とピン ----

const darkMode = matchMedia("(prefers-color-scheme: dark)");
const mapStyle = () => MAP_STYLE[darkMode.matches ? "dark" : "light"];

const map = new maplibregl.Map({ container: "map", style: mapStyle(), ...START });
darkMode.addEventListener("change", () => map.setStyle(mapStyle()));
map.on("style.load", useJapaneseLabels);
map.on("moveend", refreshSpots);

// OpenFreeMap の地名は「ローマ字＋日本語」の2段なので、日本語だけにする
function useJapaneseLabels() {
  for (const layer of map.getStyle().layers) {
    const field = layer.layout?.["text-field"];
    if (field && JSON.stringify(field).includes('"name')) {
      map.setLayoutProperty(layer.id, "text-field", ["coalesce", ["get", "name:ja"], ["get", "name"]]);
    }
  }
}

async function refreshSpots() {
  if (!token) return;
  const b = map.getBounds();
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(",");
  try {
    spots = (await api(`/spots?bbox=${bbox}`)).features;
  } catch {
    return; // 通信できないときは、前に受け取ったピンをそのまま出しておく
  }
  drawSpots();
}

function drawSpots() {
  for (const m of markers) m.remove();
  markers = spots
    .filter(matchesFilter)
    .map((f) =>
      new maplibregl.Marker({ element: pinElement(f), anchor: "bottom" })
        .setLngLat(f.geometry.coordinates)
        .addTo(map),
    );
}

function matchesFilter({ properties: p }) {
  if (filter === "all") return true;
  if (filter === "shelter") {
    return p.source === "official" || Boolean(p.kinds?.shelter || p.kinds?.car_sleeping);
  }
  return Boolean(p.kinds?.[filter]);
}

function pinLabel(p) {
  if (p.source === "official") return "避";
  const char = KIND_CHAR[p.top_kind] ?? "";
  return p.status === "confirmed" ? char : `${char}?`;
}

function pinTitle(p) {
  if (p.source === "official") return `${p.name}（公式の避難所）`;
  const kind = KIND_LABEL[p.top_kind] ?? "人の集まり";
  return `${kind}（${p.status === "confirmed" ? "確認済み" : "未確認"}）`;
}

function pinElement(feature) {
  const p = feature.properties;
  const kind = p.source === "official" ? "official" : p.status;
  const el = button("", () => openDetail(feature), `pin pin--${kind}`);
  if (p.faded) el.classList.add("pin--faded");
  el.setAttribute("aria-label", pinTitle(p));
  const shape = document.createElement("span");
  shape.className = "pin__shape";
  const text = document.createElement("span");
  text.className = "pin__text";
  text.textContent = pinLabel(p);
  shape.append(text);
  el.append(shape);
  return el;
}

for (const chip of document.querySelectorAll(".chip")) {
  chip.addEventListener("click", () => {
    filter = chip.dataset.filter;
    for (const c of document.querySelectorAll(".chip")) c.setAttribute("aria-pressed", String(c === chip));
    drawSpots();
  });
}

// ---- ピンの詳細 ----

function openDetail(feature) {
  const p = feature.properties;
  $("#detail-title").textContent = pinTitle(p);
  $("#detail-error").textContent = "";
  const body = $("#detail-body");
  body.replaceChildren();
  if (p.source === "official") {
    body.append(
      para(p.address ?? ""),
      para(`開設: ${formatTime(p.opened_at)}`),
      para("出典: BODIK 令和8年熊本地震オープンデータポータル"),
    );
  } else {
    body.append(para(`最後の確認: ${ago(p.last_activity_at)}`));
    const counts = Object.entries(p.kinds).map(([k, n]) => `${KIND_LABEL[k]} ${n}人`);
    if (counts.length) body.append(para(`回答: ${counts.join("、")}`));
    if (p.top_kind) {
      body.append(
        actions([
          ["まだやっている", () => answer(p.candidate_id, p.top_kind, "#detail-error")],
          ["もう終わっていた", () => answer(p.candidate_id, "ended", "#detail-error")],
        ]),
      );
    } else {
      body.append(para("近くで人が集まっています。ここに何がありますか？"));
      body.append(kindButtons(QUESTION_KINDS, (kind) => answer(p.candidate_id, kind, "#detail-error")));
    }
    for (const n of p.notes) body.append(noteRow(n));
  }
  $("#detail").showModal();
}

function noteRow(n) {
  const row = document.createElement("div");
  row.className = "note";
  row.append(para(`${KIND_LABEL[n.kind]}・${ago(n.answered_at)}: ${n.note}`));
  const flag = button(
    "不適切",
    async () => {
      if (await run(() => api(`/answers/${n.answer_id}/flags`, { method: "POST" }), "#detail-error")) {
        flag.disabled = true;
        flag.textContent = "通報しました";
      }
    },
    "link",
  );
  row.append(flag);
  return row;
}

async function answer(candidateId, kind, errorSelector) {
  const ok = await run(
    () => api(`/candidates/${candidateId}/answers`, { method: "POST", body: { kind } }),
    errorSelector,
  );
  if (!ok) return;
  if ($("#detail").open) $("#detail").close();
  hideQuestion();
  refreshSpots();
}

// ---- 位置の送信と「ここに何がある？」 ----

function startTracking() {
  if (!("geolocation" in navigator)) return setLocationState("denied");
  navigator.geolocation.watchPosition(
    onPosition,
    (err) => {
      if (err.code === err.PERMISSION_DENIED) setLocationState("denied");
    },
    { enableHighAccuracy: true, maximumAge: 15_000 },
  );
  setInterval(flush, SEND_EVERY_MS);
}

function onPosition(pos) {
  const { latitude: lat, longitude: lon, accuracy } = pos.coords;
  const first = here === null;
  here = { lat, lon };
  setLocationState("on");
  if (meMarker) {
    meMarker.setLngLat([lon, lat]);
  } else {
    const dot = document.createElement("div");
    dot.className = "me";
    dot.setAttribute("aria-label", "現在地");
    meMarker = new maplibregl.Marker({ element: dot }).setLngLat([lon, lat]).addTo(map);
  }
  if (pos.timestamp - lastRecorded >= RECORD_EVERY_MS) {
    lastRecorded = pos.timestamp;
    buffer.push({ lat, lon, accuracy_m: accuracy, recorded_at: new Date(pos.timestamp).toISOString() });
    buffer = buffer.slice(-MAX_BUFFER);
  }
  if (first) {
    map.jumpTo({ center: [lon, lat] });
    flush();
  }
}

async function flush() {
  if (!token) return;
  if (buffer.length) {
    const sending = buffer;
    buffer = [];
    try {
      await api("/locations", { method: "POST", body: { points: sending } });
    } catch (e) {
      // 通信できないときだけ戻して次に送る。不正な位置（422）などは捨てる
      if (e.status === 0) buffer = [...sending, ...buffer].slice(-MAX_BUFFER);
    }
  }
  await checkQuestions();
  refreshSpots();
}

async function checkQuestions() {
  if (!here) return;
  let list;
  try {
    list = await api(`/questions?lat=${here.lat}&lon=${here.lon}`);
  } catch {
    return;
  }
  const q = list.find((item) => !dismissed.has(item.candidate_id));
  if (!q) return hideQuestion();
  const card = $("#question");
  if (!card.hidden && Number(card.dataset.candidateId) === q.candidate_id) return; // 押している途中で作り直さない
  card.dataset.candidateId = q.candidate_id;
  $("#question-error").textContent = "";
  $("#question-kinds").replaceChildren(
    kindButtons(QUESTION_KINDS, (kind) => answer(q.candidate_id, kind, "#question-error")),
  );
  card.hidden = false;
}

function hideQuestion() {
  $("#question").hidden = true;
}

$("#question-close").addEventListener("click", () => {
  dismissed.add(Number($("#question").dataset.candidateId));
  hideQuestion();
});

function setLocationState(state) {
  const hint = {
    waiting: "現在地を確認しています…",
    denied: "知らせるには、今いる場所が必要です（位置の利用を許可してください）",
    on: "",
  }[state];
  $("#location-hint").textContent = hint;
  $("#location-hint").hidden = !hint;
  for (const b of $("#dock-kinds").querySelectorAll("button")) b.disabled = state !== "on";
  updateDockHeight();
}

// ---- 自分から知らせる ----

function openReport(kind) {
  if (!here) return;
  $("#report-kinds").replaceChildren(kindButtons(REPORT_KINDS, selectReportKind));
  selectReportKind(kind);
  $("#report-note").value = "";
  $("#report-error").textContent = "";
  $("#report").showModal();
}

function selectReportKind(kind) {
  reportKind = kind;
  for (const b of $("#report-kinds").querySelectorAll(".kind")) {
    b.setAttribute("aria-pressed", String(b.dataset.kind === kind));
  }
}

$("#report-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const note = $("#report-note").value.trim() || null;
  const body = { lat: here.lat, lon: here.lon, kind: reportKind, note };
  if (await run(() => api("/reports", { method: "POST", body }), "#report-error")) {
    $("#report").close();
    refreshSpots();
  }
});

// ---- メニュー ----

$("#menu-btn").addEventListener("click", () => $("#menu").showModal());

$("#leave").addEventListener("click", async () => {
  if (!confirm("参加をやめて、送ったデータをすべて消します。よろしいですか？")) return;
  if (await run(() => api("/sessions/me", { method: "DELETE" }), "#menu-error")) {
    saveToken(null);
    location.reload();
  }
});

// ---- 共通の部品 ----

function para(text) {
  const p = document.createElement("p");
  p.textContent = text;
  return p;
}

function button(text, onClick, className = "btn") {
  const b = document.createElement("button");
  b.type = "button";
  b.className = className;
  b.textContent = text;
  b.addEventListener("click", onClick);
  return b;
}

function actions(list) {
  const div = document.createElement("div");
  div.className = "actions";
  div.append(...list.map(([text, onClick]) => button(text, onClick)));
  return div;
}

function kindButtons(kinds, onPick) {
  const grid = document.createElement("div");
  grid.className = "kinds";
  for (const kind of kinds) {
    const b = button("", () => onPick(kind), "kind");
    b.dataset.kind = kind;
    const icon = document.createElement("b");
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = KIND_ICON[kind];
    b.append(icon, KIND_LABEL[kind]);
    grid.append(b);
  }
  return grid;
}

function ago(iso) {
  const min = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000));
  if (min < 60) return `${min}分前`;
  return `${Math.floor(min / 60)}時間${min % 60 ? `${min % 60}分` : ""}前`;
}

function formatTime(iso) {
  return new Date(iso).toLocaleString("ja-JP", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function updateDockHeight() {
  document.documentElement.style.setProperty("--dock-h", `${$(".dock").offsetHeight}px`);
}

// シートは外側（暗い部分）を押すと閉じる。「閉じる」「やめる」ボタンも同じ
for (const d of document.querySelectorAll("dialog.sheet")) {
  d.addEventListener("click", (e) => {
    if (e.target === d) d.close();
  });
}
for (const b of document.querySelectorAll("[data-close]")) {
  b.addEventListener("click", () => b.closest("dialog").close());
}

// ---- 起動 ----

$("#dock-kinds").replaceChildren(kindButtons(REPORT_KINDS, openReport));
setLocationState("waiting");
addEventListener("resize", updateDockHeight);
if (token) start();
else showConsent();
```

- [ ] **Step 2: ブラウザで動かして確かめる**

Chrome で `http://localhost:18080` を開き、DevTools のデバイスモード（スマホの幅、例: iPhone 14）と、Sensors の Location を `32.5072, 130.6016`（八代市役所）にする。

確認すること（すべて満たすまで直す）:
1. 同意の画面が出る。Esc で閉じない。「同意して始める」で閉じ、位置の許可を求められる
2. 位置を許可すると、八代市の地図に青い点が出る。地名は日本語だけで出る
3. 「給水」を押す → シートが開き「給水」が選ばれている → 備考「12時まで」→ 送信 → オレンジの「水?」のピンが立つ
4. ピンを押す → 「給水（未確認）」「回答: 給水 1人」「12時まで」が出る
5. 備考に `https://example.com` を書いて送信すると、入力欄の下に「備考に URL は書けません」が出る
6. シークレットウィンドウで同じ URL を開き、同じ位置で同意する → 1分以内に「近くで人が集まっています」のカードが出る → 「給水」を押す → 両方のウィンドウで紺の「水」のピンになる（地図は1分ごとに更新される。すぐ見たいときは地図を少し動かす）
7. ピンの詳細で「もう終わっていた」を押す → 1対1の同数になり、ピンが消える
8. 絞り込みの「食」を押すと「水」のピンが消え、「すべて」で戻る
9. DevTools の Rendering で `prefers-color-scheme: dark` にすると、配色と地図が暗くなる
10. サイトの設定で位置を「ブロック」にして再読み込みすると、地図は見られるが、知らせるボタンは押せず「今いる場所が必要です」と出る
11. メニュー → 「参加をやめてデータを消す」→ 確認 → 同意の画面に戻る
12. DevTools の Network を Offline にして1分待つと、上部に「接続できません」の帯が出る。Online に戻すと消える
13. Console にエラーが出ていない

公式の避難所の確認（Task 5 で取り込み済みの場合）:
14. 今（2026年11月）開いている八代市の避難所に、白枠の「避」のピンが出る。押すと名前・住所・開設日時・出典が出る

- [ ] **Step 3: コミットする**

```bash
git add web/app.js
git commit -m "Web 画面の動き（地図・質問・報告・詳細・メニュー）を追加

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 仕上げ（README・全体の確認）

**Files:**
- Modify: `README.md`（「起動方法」に追記）

- [ ] **Step 1: README に追記する**

`README.md` の「## 起動方法」のコードブロックの下にある箇条書きを、次の内容で置き換える:

```markdown
- Web 画面: http://localhost:18080 （スマホ実機は HTTPS が必要なので `ngrok http 18080` で公開する）
- API 仕様書: http://localhost:18080/docs （OpenAPI: `/openapi.json`）
- 公式の避難所データ（BODIK 令和8年熊本地震）の取り込み: `docker compose run --rm api python scripts/import_bodik.py`
- 構成と設計の理由: [docs/architecture.md](architecture.md) / 画面: [docs/web-ui-design.md](web-ui-design.md)
- DB を初期化し直す: `docker compose down -v`（取り込んだデータも消えるので、取り込みをやり直す）
```

- [ ] **Step 2: 全体を確認する**

Run: `docker compose down -v && docker compose up -d --build --wait && docker compose run --rm api python scripts/import_bodik.py && docker compose run --rm api sh -c "pytest -q && ruff check . && ruff format --check ."`
Expected: 取り込みの件数、`24 passed`、`All checks passed!`、`files already formatted`

`ruff format --check` が失敗したら `docker compose run --rm api ruff format .` で整え、もう一度確認する。

- [ ] **Step 3: root 所有のファイルができていないことを確認する**

Run: `find . -path ./.git -prune -o -user root -print`
Expected: 何も出ない

- [ ] **Step 4: コミットする**

```bash
git add README.md
git commit -m "README に Web 画面と避難所データの取り込みを追記

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
