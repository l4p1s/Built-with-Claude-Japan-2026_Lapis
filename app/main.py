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
    return {"type": "FeatureCollection", "features": spots.unofficial_features(conn, parse_bbox(bbox))}
