"""アカウントなしの匿名セッション。Authorization: Bearer <token> で送る。"""

import hashlib
import secrets
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db import Conn

# 最終利用からこの期間で失効する
# ponytail: 失効したセッションと位置データの削除は未実装。検知の定期処理に相乗りさせる
SESSION_TTL = timedelta(days=30)

_bearer = HTTPBearer(auto_error=False)


def _hash(token: str) -> bytes:
    # DB にはハッシュだけ置く。DB が漏れてもトークンは復元できない
    return hashlib.sha256(token.encode()).digest()


def create_session(conn: Conn) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token_hash) VALUES (%s)", (_hash(token),))
    return token


def current_session(
    conn: Conn,
    cred: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> UUID:
    row = None
    if cred is not None:
        row = conn.execute(
            """
            UPDATE sessions SET last_seen_at = now()
            WHERE token_hash = %s AND last_seen_at > now() - %s
            RETURNING id
            """,
            (_hash(cred.credentials), SESSION_TTL),
        ).fetchone()
    if row is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return row[0]


Session = Annotated[UUID, Depends(current_session)]
