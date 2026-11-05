"""DB の位置（location_points）から集まりを検知して candidates に書く定期処理。古い位置と失効したセッションも消す。

    python -m app.detect_job                      # 今の時刻で 1 回
    python -m app.detect_job --at 2026-07-29T12:30+09:00

API の起動時にも DETECT_EVERY_S 秒ごと（既定 300、0 で止める）に回る（app/main.py）。
検知の中身は app/detect.py（docs/algorithm.md）。ここは DB との出し入れだけ。
"""

import argparse
import os
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone

import psycopg

from app import rules
from app.auth import SESSION_TTL
from app.db import DATABASE_URL
from app.detect import baseline_share, detect, learn_person, stays_from_track
from app.mesh import mesh_code

LOOKBACK = timedelta(hours=3)  # この幅の時間帯を調べる（続いている集まりは候補を更新する）
WARMUP = timedelta(hours=2)  # 調べ始めより前から続く滞在を拾うため、点はこれだけ前から読む
LEARN_DAYS = 7  # 平常の学習に使う日数（docs/algorithm.md §4.1: 平日 2 日を含む 5 日で十分）
LEARN_EVERY = timedelta(
    hours=24
)  # 学習（自宅・いつもの場所・マスの割合）を回し直す間隔。それまでは覚えたものを使う
RETENTION = timedelta(days=14)  # 位置を残す期間（学習に使う分より長く）
MERGE_M = 150  # この距離にある候補（検知・報告）にまとめる
JST = timezone(timedelta(hours=9))


def _tracks(conn, since, until):
    """区間の点を人ごとに (時刻の一覧, lat, lon) にする。時刻は datetime"""
    rows = conn.execute(
        """
        SELECT session_id, recorded_at, ST_Y(geom::geometry), ST_X(geom::geometry)
        FROM location_points
        WHERE recorded_at >= %s AND recorded_at < %s
        ORDER BY session_id, recorded_at
        """,
        (since, until),
    ).fetchall()
    out = defaultdict(lambda: ([], [], []))
    for sid, t, lat, lon in rows:
        ts, la, lo = out[sid]
        ts.append(t)
        la.append(lat)
        lo.append(lon)
    return out


def _stays(ts, la, lo, base):
    minutes = [(t - base).total_seconds() / 60 for t in ts]
    return stays_from_track(la, lo, minutes)


def learn(conn, now):
    """直近 LEARN_DAYS 日（調べる時間帯より前）から、人ごとの自宅・いつもの場所・探索型と、マス別の滞在の割合"""
    since, until = now - timedelta(days=LEARN_DAYS), now - LOOKBACK - WARMUP
    by_day = defaultdict(lambda: defaultdict(list))  # sid → 日 → stays
    nights = defaultdict(list)
    cells = defaultdict(int)
    for sid, (ts, la, lo) in _tracks(conn, since, until).items():
        day_idx = defaultdict(list)
        for i, t in enumerate(ts):
            local = t.astimezone(JST)
            day_idx[local.date()].append(i)
            if 1 <= local.hour < 4:
                nights[sid].append((la[i], lo[i]))
        for d, idx in day_idx.items():
            st = _stays([ts[i] for i in idx], [la[i] for i in idx], [lo[i] for i in idx], since)
            by_day[sid][d] = st
            for s in st:
                cells[mesh_code(s.lat, s.lon)] += 1
    people = {sid: learn_person(list(by_day[sid].values()), nights.get(sid, [])) for sid in by_day}
    return people, baseline_share(cells), nights


_learned = {"at": None, "value": None}  # プロセス内に覚えた学習結果（定期処理が使う）


def learn_cached(conn, now):
    """LEARN_EVERY に 1 回だけ learn() を回し、あとは覚えたものを返す。新しい人は次の学習まで「当日だけ」版で扱う"""
    if _learned["at"] is None or now - _learned["at"] >= LEARN_EVERY:
        _learned.update(at=now, value=learn(conn, now))
    return _learned["value"]


def run(conn, now=None, use_cache=False):
    """1 回ぶんの検知。見つけた候補を candidates に書き、[(候補 id, 新規か)] を返す"""
    now = now or datetime.now(UTC)
    people, share, nights = learn_cached(conn, now) if use_cache else learn(conn, now)
    base = now - LOOKBACK - WARMUP
    unusual = []
    for sid, (ts, la, lo) in _tracks(conn, base, now).items():
        p = people.get(sid) or learn_person([], nights.get(sid, []))
        for s in _stays(ts, la, lo, base):
            if not p.is_usual(s.lat, s.lon):
                unusual.append((sid, s, p.weight))
    t_start = int(WARMUP.total_seconds() // 60)
    t_end = int((now - base).total_seconds() // 60)
    cands = detect(unusual, share, t_start=t_start, t_end=t_end)
    written = [_write(conn, c, base) for c in cands]
    _cleanup(conn, now)
    return written


def _write(conn, c, base):
    """候補を DB に書く。近くに生きている候補があればそれを更新し、なければ作る"""
    ws = base + timedelta(minutes=c.window_start)
    we = base + timedelta(minutes=c.window_end)
    params = {
        "lat": c.lat,
        "lon": c.lon,
        "cell": c.cell,
        "ws": ws,
        "we": we,
        "obs": c.observed,
        "exp": c.expected,
        "p": c.p_value,
        "r": MERGE_M,
        "hide": rules.HIDE_AFTER,
    }
    row = conn.execute(
        """
        SELECT id, source FROM candidates
        WHERE status <> 'ended' AND last_activity_at > %(ws)s - %(hide)s
          AND ST_DWithin(center, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(r)s)
        ORDER BY (source = 'detected') DESC,
                 ST_Distance(center, ST_MakePoint(%(lon)s, %(lat)s)::geography)
        LIMIT 1
        """,
        params,
    ).fetchone()
    if row and row[1] == "detected":
        conn.execute(
            """
            UPDATE candidates SET observed = %(obs)s, expected = %(exp)s, p_value = %(p)s,
                   last_activity_at = greatest(last_activity_at, %(we)s)
            WHERE id = %(id)s
            """,
            {**params, "id": row[0]},
        )
        return row[0], False
    if row:
        # 報告の候補に検知が重なった: 検知を 1 人ぶんの裏付けとして数え、足りれば確認済みにする
        conn.execute(
            """
            UPDATE candidates c SET
                last_activity_at = greatest(c.last_activity_at, %(we)s),
                status = CASE WHEN c.status = 'pending' AND (
                    SELECT count(*) FROM answers a
                    WHERE a.candidate_id = c.id AND a.kind = ANY(%(kinds)s)
                ) + 1 >= %(need)s THEN 'confirmed' ELSE c.status END
            WHERE c.id = %(id)s
            """,
            {
                **params,
                "id": row[0],
                "kinds": list(rules.KINDS),
                "need": rules.CONFIRM_ANSWERS["report"],
            },
        )
        return row[0], False
    cid = conn.execute(
        """
        INSERT INTO candidates (source, center, last_activity_at, created_at,
                                cell_code, window_start, observed, expected, p_value)
        VALUES ('detected', ST_MakePoint(%(lon)s, %(lat)s)::geography, %(we)s, %(ws)s,
                %(cell)s, %(ws)s, %(obs)s, %(exp)s, %(p)s)
        ON CONFLICT (cell_code, window_start) DO UPDATE SET
            observed = EXCLUDED.observed, expected = EXCLUDED.expected,
            p_value = EXCLUDED.p_value,
            last_activity_at = greatest(candidates.last_activity_at, EXCLUDED.last_activity_at)
        RETURNING id
        """,
        params,
    ).fetchone()[0]
    return cid, True


def _cleanup(conn, now):
    """古い位置と、失効したセッション（位置・回答・通報は ON DELETE CASCADE）を消す"""
    # ponytail: 失効した人の回答が消えても候補の状態は決め直さない。次の回答か検知で決まり直る
    conn.execute("DELETE FROM location_points WHERE recorded_at < %s", (now - RETENTION,))
    conn.execute("DELETE FROM sessions WHERE last_seen_at < %s", (now - SESSION_TTL,))


def run_once(at=None, use_cache=False):
    with psycopg.connect(DATABASE_URL) as conn:
        return run(conn, at, use_cache)


def loop(every_s, stop):
    """API の中で every_s 秒ごとに回す（app/main.py から別スレッドで）"""
    while not stop.wait(every_s):
        try:
            run_once(use_cache=True)
        except Exception as e:  # 1 回の失敗で止めない
            print("detect_job:", repr(e))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", help="この時刻として検知する（既定: 今）。例 2026-07-29T12:30+09:00")
    a = ap.parse_args()
    at = datetime.fromisoformat(a.at) if a.at else None
    t0 = time.time()
    written = run_once(at)
    new = sum(1 for _, is_new in written)
    print(f"候補 {len(written)} 件（新規 {new}）{time.time() - t0:.1f} 秒")
    os._exit(0)
