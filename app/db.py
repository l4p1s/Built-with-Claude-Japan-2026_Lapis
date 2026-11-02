import os
from typing import Annotated

import psycopg
from fastapi import Depends

DATABASE_URL = os.environ["DATABASE_URL"]


def get_conn():
    # ponytail: リクエストごとに接続。負荷が問題になったら psycopg_pool に替える
    # with を抜けるとき、例外がなければ commit、あれば rollback
    with psycopg.connect(DATABASE_URL) as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]
