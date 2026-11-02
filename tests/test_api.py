"""実 DB（compose の db）に対して API を通しで確認する。

docker compose run --rm api pytest
"""

from datetime import UTC, datetime

import psycopg
from fastapi.testclient import TestClient

from app.db import DATABASE_URL
from app.main import app

client = TestClient(app)

# 八代市役所付近
LAT, LON = 32.5072, 130.6016


def new_session() -> dict:
    r = client.post("/v1/sessions", json={"consent": True})
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['token']}"}


def count_points() -> int:
    with psycopg.connect(DATABASE_URL) as conn:
        return conn.execute("SELECT count(*) FROM location_points").fetchone()[0]


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
    assert client.post("/v1/locations", json={"points": [point] * 3}, headers=h).json() == {
        "accepted": 3
    }
    assert count_points() == before + 3

    bad = {**point, "lat": 200}
    assert client.post("/v1/locations", json={"points": [bad]}, headers=h).status_code == 422
    naive = {**point, "recorded_at": "2026-07-29T09:00:00"}
    assert client.post("/v1/locations", json={"points": [naive]}, headers=h).status_code == 422

    assert client.delete("/v1/sessions/me", headers=h).status_code == 204
    assert count_points() == before
    assert client.get("/v1/spots", headers=h).status_code == 401


def test_question_answer_spot():
    h = new_session()
    with psycopg.connect(DATABASE_URL) as conn:
        cid = conn.execute(
            """
            INSERT INTO candidates (cell_code, center, window_start, observed, expected, p_value)
            VALUES ('test', ST_MakePoint(%s, %s)::geography, now(), 10, 1.0, 0.001)
            RETURNING id
            """,
            (LON, LAT),
        ).fetchone()[0]
    try:
        ids = lambda: [q["candidate_id"] for q in client.get(
            "/v1/questions", params={"lat": LAT, "lon": LON}, headers=h).json()]  # fmt: skip
        assert cid in ids()
        far = client.get("/v1/questions", params={"lat": LAT + 0.1, "lon": LON}, headers=h)
        assert cid not in [q["candidate_id"] for q in far.json()]

        url = f"/v1/candidates/{cid}/answers"
        assert client.post(url, json={"kind": "water"}, headers=h).status_code == 201
        assert client.post(url, json={"kind": "food"}, headers=h).status_code == 409
        assert client.post(url, json={"kind": "beer"}, headers=h).status_code == 422
        assert client.post("/v1/candidates/0/answers", json={"kind": "water"},
                           headers=h).status_code == 404  # fmt: skip
        assert cid not in ids()

        bbox = f"{LON - 0.01},{LAT - 0.01},{LON + 0.01},{LAT + 0.01}"
        spots = client.get("/v1/spots", params={"bbox": bbox}, headers=h).json()["features"]
        spot = next(f for f in spots if f["properties"]["candidate_id"] == cid)
        assert spot["properties"]["kinds"] == {"water": 1}
        assert spot["geometry"]["coordinates"] == [LON, LAT]

        elsewhere = client.get("/v1/spots", params={"bbox": "0,0,1,1"}, headers=h).json()
        assert cid not in [f["properties"]["candidate_id"] for f in elsewhere["features"]]
        assert client.get("/v1/spots", params={"bbox": "x"}, headers=h).status_code == 422
    finally:
        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("DELETE FROM candidates WHERE id = %s", (cid,))
