import mimetypes
import os
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import psycopg
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field

from app import detect_job, rules, spots
from app.auth import SESSION_TTL, Session, create_session
from app.db import Conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 検知の定期処理。DETECT_EVERY_S=0 で止める（テスト・検知を手で回すとき）
    every = int(os.environ.get("DETECT_EVERY_S", "300"))
    stop = threading.Event()
    if every:
        threading.Thread(target=detect_job.loop, args=(every, stop), daemon=True).start()
    yield
    stop.set()


app = FastAPI(title="Aid Spot Finder API", version="0.1.0", lifespan=lifespan)

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
    source: Literal["detected", "report"]


class AnswerIn(BaseModel):
    kind: AnswerKind  # none = わからない、ended = もう終わっていた
    note: Note = None


class ReportIn(BaseModel):
    lat: Lat
    lon: Lon
    kind: rules.Kind
    note: Note = None


class ReportOut(BaseModel):
    candidate_id: int


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
    # 回答を消してピンの状態を決め直してから、セッションを消す（位置・通報は ON DELETE CASCADE で消える）
    spots.forget_session(conn, sid)
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
    return [Question(candidate_id=cid, lat=la, lon=lo, source=src) for cid, la, lo, src in rows]


@app.post("/v1/candidates/{candidate_id}/answers", status_code=status.HTTP_201_CREATED)
def post_answer(candidate_id: int, body: AnswerIn, conn: Conn, sid: Session) -> AnswerIn:
    try:
        spots.record_answer(conn, candidate_id, sid, body.kind, body.note)
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate not found")
    return body


@app.post("/v1/reports", status_code=status.HTTP_201_CREATED)
def post_report(body: ReportIn, conn: Conn, sid: Session) -> ReportOut:
    """今いる場所にあるものを知らせる。近くの候補にまとめるか、新しい候補を作る。"""
    cid = spots.find_or_create_report(conn, body.lat, body.lon)
    spots.record_answer(conn, cid, sid, body.kind, body.note)
    return ReportOut(candidate_id=cid)


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


# 画面（web/）を / で配信する。API と同じオリジンなので CORS の設定は要らない
mimetypes.add_type("application/manifest+json", ".webmanifest")


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "web", html=True), name="web")
