"""非公式の拠点（候補）と公式拠点の読み書き。ルールの数値は app/rules.py。"""

from datetime import datetime
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
    # 同じ候補への回答を1件ずつ処理する（同時に答えると状態の数え間違いが起きるため）
    conn.execute("SELECT 1 FROM candidates WHERE id = %s FOR UPDATE", (candidate_id,))
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
    _refresh_status(conn, candidate_id, touch=kind in rules.KINDS)


def forget_session(conn: psycopg.Connection, sid: UUID) -> None:
    """参加をやめた人の回答を消す。その人の報告だけでできたピンは消し、残りは状態を決め直す。"""
    rows = conn.execute(
        "DELETE FROM answers WHERE session_id = %s RETURNING candidate_id", (sid,)
    ).fetchall()
    cids = [r[0] for r in rows]
    conn.execute(
        """
        DELETE FROM candidates c
        WHERE c.id = ANY(%s) AND c.source = 'report'
          AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.candidate_id = c.id)
        """,
        (cids,),
    )
    for cid in cids:
        _refresh_status(conn, cid, touch=False)  # 消えた候補は 0 行の更新になるだけ


def _refresh_status(conn: psycopg.Connection, candidate_id: int, touch: bool) -> None:
    """回答から候補の状態を決め直す。touch なら最後の確認時刻も今にする。"""
    conn.execute(
        """
        UPDATE candidates c SET
            last_activity_at = CASE WHEN %(touch)s THEN now() ELSE c.last_activity_at END,
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
            "touch": touch,
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


def find_or_create_report(conn: psycopg.Connection, lat: float, lon: float) -> int:
    """MERGE_RADIUS_M 以内に地図に出ている候補があればその ID、なければ報告の候補を作る。"""
    # ponytail: 同じ場所に同時に報告されてピンが2つできないよう、報告を1件ずつ処理する。
    # 全体で1本のロック。報告が多くなったら場所のグリッドごとに分ける
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('reports'))")
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
                   (array_agg(kind ORDER BY n DESC, first_id))[1] AS top_kind
            FROM (SELECT kind, count(*) AS n, min(id) AS first_id
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
