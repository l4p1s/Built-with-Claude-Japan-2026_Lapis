"""公式拠点の CSV 取り込み（scripts/import_official.py）。DB を使う部分は実 DB で確かめる"""

import csv
import io
from datetime import UTC, datetime

import psycopg
import pytest

from app.db import DATABASE_URL
from scripts.import_official import rows, upsert

NOW = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)


def read(text):
    return csv.DictReader(io.StringIO(text))


def test_rows_fill_defaults():
    out = rows(read("name,kind,lat,lon\nA,water,32.5,130.6\n"), now=NOW)
    assert out == [("A", "A", None, "water", 130.6, 32.5, NOW, None)]


def test_rows_use_key_and_times():
    text = "key,name,kind,lat,lon,address,opened_at,closed_at\n7,A,shelter,32.5,130.6,八代市,2026-07-28T16:27+09:00,2026-08-01T00:00+09:00\n"
    (row,) = rows(read(text), now=NOW)
    assert row[0] == "7" and row[2] == "八代市"
    assert row[6] == "2026-07-28T16:27+09:00" and row[7] == "2026-08-01T00:00+09:00"


@pytest.mark.parametrize(
    "text",
    [
        "name,kind,lat,lon\n,water,32.5,130.6\n",  # 名前なし
        "name,kind,lat,lon\nA,food,32.5,130.6\n",  # 種類が変
        "name,kind,lat,lon\nA,water,abc,130.6\n",  # 数でない
        "name,kind,lat,lon\nA,water,95,130.6\n",  # 範囲外
    ],
)
def test_rows_reject_bad_input(text):
    with pytest.raises(ValueError):
        rows(read(text))


def test_upsert_is_idempotent_and_updates():
    source = "test-csv"
    with psycopg.connect(DATABASE_URL) as conn:
        try:
            upsert(conn, source, rows(read("name,kind,lat,lon\nX,water,29.0,135.0\n"), now=NOW))
            upsert(conn, source, rows(read("name,kind,lat,lon\nX,water,29.1,135.0\n"), now=NOW))
            got = conn.execute(
                "SELECT count(*), max(ST_Y(geom::geometry)) FROM official_spots WHERE source = %s",
                (source,),
            ).fetchone()
            assert got == (1, 29.1)
        finally:
            conn.execute("DELETE FROM official_spots WHERE source = %s", (source,))
