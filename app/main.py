from datetime import UTC, datetime
from typing import Annotated, Literal

import psycopg
from fastapi import FastAPI, HTTPException, Query, status
from pydantic import AwareDatetime, BaseModel, Field

from app.auth import SESSION_TTL, Session, create_session
from app.db import Conn

app = FastAPI(title="Aid Spot Finder API", version="0.1.0")

# 「ここに何がある？」を聞く範囲
QUESTION_RADIUS_M = 500

Kind = Literal["water", "food", "goods", "car_sleeping", "shelter", "none", "other"]
Lat = Annotated[float, Field(ge=-90, le=90)]
Lon = Annotated[float, Field(ge=-180, le=180)]


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
    window_start: datetime


class Answer(BaseModel):
    kind: Kind


@app.get("/healthz")
def healthz(conn: Conn) -> dict:
    conn.execute("SELECT 1")
    return {"ok": True}


@app.post("/v1/sessions", status_code=status.HTTP_201_CREATED)
def post_session(body: SessionCreate, conn: Conn) -> SessionOut:
    return SessionOut(token=create_session(conn), expires_at=datetime.now(UTC) + SESSION_TTL)


@app.delete("/v1/sessions/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(conn: Conn, sid: Session) -> None:
    # 位置・回答も ON DELETE CASCADE で消える
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
    rows = conn.execute(
        """
        SELECT c.id, ST_Y(c.center::geometry), ST_X(c.center::geometry), c.window_start
        FROM candidates c
        WHERE c.status = 'pending'
          AND ST_DWithin(c.center, ST_MakePoint(%(lon)s, %(lat)s)::geography, %(r)s)
          AND NOT EXISTS (
              SELECT 1 FROM answers a WHERE a.candidate_id = c.id AND a.session_id = %(sid)s)
        ORDER BY c.window_start DESC
        """,
        {"lat": lat, "lon": lon, "r": QUESTION_RADIUS_M, "sid": sid},
    ).fetchall()
    return [Question(candidate_id=r[0], lat=r[1], lon=r[2], window_start=r[3]) for r in rows]


@app.post("/v1/candidates/{candidate_id}/answers", status_code=status.HTTP_201_CREATED)
def post_answer(candidate_id: int, body: Answer, conn: Conn, sid: Session) -> Answer:
    try:
        conn.execute(
            "INSERT INTO answers (candidate_id, session_id, kind) VALUES (%s, %s, %s)",
            (candidate_id, sid, body.kind),
        )
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate not found")
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, "already answered")
    if body.kind != "none":
        # ponytail: 回答1件で地図に載せる。回答数による信頼度は構想段階（CLAUDE.md §2）
        conn.execute("UPDATE candidates SET status = 'confirmed' WHERE id = %s", (candidate_id,))
    return body


@app.get("/v1/spots")
def get_spots(
    conn: Conn,
    sid: Session,
    bbox: Annotated[str | None, Query(description="minLon,minLat,maxLon,maxLat")] = None,
) -> dict:
    """確認済みの非公式拠点を GeoJSON の FeatureCollection で返す。"""
    where, params = "", {}
    if bbox is not None:
        try:
            min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
        except ValueError:
            raise HTTPException(422, "bbox must be minLon,minLat,maxLon,maxLat")
        where = "AND c.center::geometry && ST_MakeEnvelope(%s, %s, %s, %s, 4326)"
        params = (min_lon, min_lat, max_lon, max_lat)
    rows = conn.execute(
        f"""
        SELECT c.id, ST_X(c.center::geometry), ST_Y(c.center::geometry), c.window_start,
               jsonb_object_agg(k.kind, k.n)
        FROM candidates c
        JOIN (SELECT candidate_id, kind, count(*) AS n FROM answers
              WHERE kind <> 'none' GROUP BY candidate_id, kind) k ON k.candidate_id = c.id
        WHERE c.status = 'confirmed' {where}
        GROUP BY c.id
        """,
        params,
    ).fetchall()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "candidate_id": cid,
                    "window_start": window_start.isoformat(),
                    "kinds": kinds,  # 例 {"water": 3, "food": 1}
                },
            }
            for cid, lon, lat, window_start, kinds in rows
        ],
    }
