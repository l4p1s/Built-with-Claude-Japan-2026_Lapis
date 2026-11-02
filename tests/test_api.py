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
    sql("DELETE FROM official_spots WHERE source = 'test'")


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
    return client.post(f"/v1/candidates/{cid}/answers", json={"kind": kind, "note": note}, headers=h)


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
    answer(h1, cid, "food")  # 「まだやっている」で上書きしても、先に付いた順は変わらない
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


def test_none_answer_keeps_activity_and_stops_asking():
    h = new_session()
    cid = add_candidate()
    sql("UPDATE candidates SET last_activity_at = now() - interval '4 hours' WHERE id = %s", (cid,))
    assert answer(h, cid, "none").status_code == 201
    p = spot(h, cid)
    assert (p["status"], p["faded"]) == ("pending", True)  # 「わからない」では時刻を更新しない
    assert cid not in question_ids(h)  # 答えた人には聞かない


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


def test_report_does_not_merge_into_hidden_candidate():
    h = new_session()
    old = add_candidate()
    sql("UPDATE candidates SET last_activity_at = now() - interval '7 hours' WHERE id = %s", (old,))
    assert report(h, "water").json()["candidate_id"] != old


def test_same_person_cannot_confirm_own_report():
    h = new_session()
    cid = report(h, "water").json()["candidate_id"]
    assert report(h, "water", lat=LAT + 0.0005).json()["candidate_id"] == cid
    assert spot(h, cid)["status"] == "pending"

def test_report_rejects_bad_input():
    h = new_session()
    assert report(h, "none").status_code == 422  # 報告に「わからない」は使えない
    assert report(h, "water", lat=200).status_code == 422
    assert report(h, "water", note="www.example.com").status_code == 422


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


def test_web_is_served():
    r = client.get("/")
    assert r.status_code == 200 and 'id="map"' in r.text
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
