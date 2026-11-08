"""検知の定期処理（app/detect_job.py）を実 DB で確かめる。

docker compose run --rm api pytest tests/test_detect_job.py
"""

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from app import detect_job
from app.db import DATABASE_URL

# 海の上（紀伊半島の南の沖）。他のテスト（30.0, 135.0）とも重ねない
LAT, LON = 29.5, 135.5
NOW = datetime(2026, 7, 29, 3, 0, tzinfo=UTC)  # JST 12:00


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
    sql("DELETE FROM sessions WHERE token_hash = ANY(%s)", ([f"t{i}".encode() for i in range(40)],))
    sql("DELETE FROM official_spots WHERE source = 'test-job'")


def add_person(i, track):
    """track: [(時刻, lat, lon)] を 1 分おきに置く"""
    sid = sql("INSERT INTO sessions (token_hash) VALUES (%s) RETURNING id", (f"t{i}".encode(),))[0][
        0
    ]
    with psycopg.connect(DATABASE_URL) as conn:
        conn.cursor().executemany(
            "INSERT INTO location_points (session_id, geom, recorded_at)"
            " VALUES (%s, ST_MakePoint(%s, %s)::geography, %s)",
            [(sid, lon, lat, t) for t, lat, lon in track],
        )
    return sid


def minutes(start, n, lat, lon):
    return [(start + timedelta(minutes=k), lat, lon) for k in range(n)]


def test_gathering_becomes_candidate_and_updates():
    home = lambda i: (LAT + 0.02 * (i % 4), LON + 0.02 * (i // 4))  # noqa: E731
    spot = (LAT + 0.1, LON + 0.1)
    # 平常 3 日: 夜は家、昼は家。地震後の今日: 11:00 から集まりの場所に 40 分
    for i in range(10):
        track = []
        for d in (3, 2, 1):
            day = NOW - timedelta(days=d)
            track += minutes(
                day - timedelta(hours=10), 24 * 60, *home(i)
            )  # 前日 17:00 から丸 1 日、家
        track += minutes(NOW - timedelta(hours=3), 120, *home(i))  # 今日 9:00〜11:00 家
        track += minutes(NOW - timedelta(hours=1), 40, *spot)  # 11:00〜11:40 集まり
        add_person(i, track)
    written = detect_job.run_once(NOW)
    assert len(written) == 1 and written[0][1] is True
    cid = written[0][0]
    row = sql(
        "SELECT source, status, observed, expected, p_value, cell_code,"
        " ST_Distance(center, ST_MakePoint(%s, %s)::geography) FROM candidates WHERE id = %s",
        (spot[1], spot[0], cid),
    )[0]
    assert row[0] == "detected" and row[1] == "pending"
    assert row[2] == 10 and row[4] < 1e-3 and row[5] and row[6] < 100
    # 10 分後にもう一度回しても、同じ候補が更新されるだけ（新しい候補は作らない）
    written = detect_job.run_once(NOW + timedelta(minutes=10))
    assert written == [(cid, False)]


def test_detection_backs_up_report():
    """報告の候補（1 人の回答）に検知が重なると、確認済みになる"""
    spot = (LAT + 0.1, LON + 0.1)
    sid0 = add_person(20, minutes(NOW - timedelta(hours=1), 40, *spot))
    cid = sql(
        "INSERT INTO candidates (source, center) VALUES ('report', ST_MakePoint(%s, %s)::geography)"
        " RETURNING id",
        (spot[1], spot[0]),
    )[0][0]
    sql(
        "INSERT INTO answers (candidate_id, session_id, kind) VALUES (%s, %s, 'water')", (cid, sid0)
    )
    for i in range(21, 30):
        add_person(i, minutes(NOW - timedelta(hours=1), 40, *spot))
    written = detect_job.run_once(NOW)
    assert written == [(cid, False)]
    assert sql("SELECT status FROM candidates WHERE id = %s", (cid,))[0][0] == "confirmed"


def test_cleanup_removes_old_points_and_sessions():
    sid = add_person(30, minutes(NOW - timedelta(days=20), 5, LAT, LON))
    sql("UPDATE sessions SET last_seen_at = %s WHERE id = %s", (NOW - timedelta(days=31), sid))
    detect_job.run_once(NOW)
    assert sql("SELECT count(*) FROM sessions WHERE id = %s", (sid,))[0][0] == 0


def test_gathering_at_open_official_spot_is_not_a_candidate():
    """開設中の公式の拠点（避難所）の 150m 以内の集まりは候補にしない。閉鎖後なら候補になる"""
    spot = (LAT + 0.1, LON + 0.1)
    for i in range(31, 40):
        add_person(i, minutes(NOW - timedelta(hours=1), 40, *spot))
    sql(
        "INSERT INTO official_spots (source, source_key, name, kind, geom, opened_at, closed_at)"
        " VALUES ('test-job', 'x', '避難所', 'shelter', ST_MakePoint(%s, %s)::geography, %s, NULL)",
        (spot[1] + 0.0005, spot[0], NOW - timedelta(days=1)),  # 約 50m 東
    )
    assert detect_job.run_once(NOW) == []
    sql(
        "UPDATE official_spots SET closed_at = %s WHERE source = 'test-job'",
        (NOW - timedelta(hours=3),),
    )
    written = detect_job.run_once(NOW)
    assert len(written) == 1 and written[0][1] is True


def test_one_session_cannot_hide_a_gathering_with_backdated_stays():
    """1 人が過去の日付で同じマスの滞在を大量に送っても、その人は 1 日 1 マス 1 回しか数えない"""
    home = lambda i: (LAT + 0.02 * (i % 4), LON + 0.02 * (i // 4))  # noqa: E731
    spot = (LAT + 0.1, LON + 0.1)
    elsewhere = (LAT + 0.15, LON + 0.15)
    for i in range(10):  # test_gathering_becomes_candidate_and_updates と同じ集まり
        track = []
        for d in (3, 2, 1):
            track += minutes(NOW - timedelta(days=d) - timedelta(hours=10), 24 * 60, *home(i))
        track += minutes(NOW - timedelta(hours=3), 120, *home(i))
        track += minutes(NOW - timedelta(hours=1), 40, *spot)
        add_person(i, track)
    # 6 日前から、集まりのマスと別のマスに交互に 10 分の滞在（2 点）を 300 回
    t0, track = NOW - timedelta(days=6), []
    for k in range(300):
        place = spot if k % 2 == 0 else elsewhere
        start = t0 + timedelta(minutes=20 * k)
        track += [(start, *place), (start + timedelta(minutes=9), *place)]
    add_person(39, track)
    written = detect_job.run_once(NOW)
    assert len(written) == 1 and written[0][1] is True
