"""公式の拠点（避難所・給水所）を CSV から official_spots に取り込む。どの地域でも使える。何度実行しても重複しない。

    docker compose exec api python scripts/import_official.py data/official/<file>.csv --source <取り込み元の名前>

CSV の列（1 行目が見出し。順番は自由、文字コードは UTF-8）:
    name       必須  名前（同じ source の中で一意。key 列があればそちらを一意のキーにする）
    kind       必須  shelter（避難所）か water（給水所）
    lat, lon   必須  緯度・経度（10 進）
    address    任意  住所
    opened_at  任意  開設日時（例 2026-07-28T16:27+09:00）。空なら取り込んだ時刻から開いている扱い
    closed_at  任意  閉鎖日時。空なら開設中
    key        任意  一意のキー（自治体の施設 ID など）
形式の説明と例は data/official/README.md。
"""

import argparse
import csv
import os
from datetime import UTC, datetime

import psycopg

KINDS = ("shelter", "water")


def rows(reader, now=None):
    """CSV の行 → official_spots に入れる値の組。足りない列や変な値は ValueError"""
    now = now or datetime.now(UTC)
    out = []
    for i, r in enumerate(reader, start=2):
        name = (r.get("name") or "").strip()
        kind = (r.get("kind") or "").strip()
        if not name:
            raise ValueError(f"{i} 行目: name が空です")
        if kind not in KINDS:
            raise ValueError(
                f"{i} 行目: kind は {'/'.join(KINDS)} のどれかにしてください（{kind!r}）"
            )
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (KeyError, ValueError, TypeError):
            raise ValueError(f"{i} 行目: lat, lon は 10 進の数にしてください") from None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError(f"{i} 行目: lat, lon の範囲が変です（{lat}, {lon}）")
        key = (r.get("key") or "").strip() or name
        opened = (r.get("opened_at") or "").strip() or now
        closed = (r.get("closed_at") or "").strip() or None
        out.append(
            (key, name, (r.get("address") or "").strip() or None, kind, lon, lat, opened, closed)
        )
    return out


def upsert(conn, source, data):
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO official_spots
                (source, source_key, name, address, kind, geom, opened_at, closed_at)
            VALUES (%s, %s, %s, %s, %s, ST_MakePoint(%s, %s)::geography, %s, %s)
            ON CONFLICT (source, source_key) DO UPDATE SET
                name = EXCLUDED.name, address = EXCLUDED.address, kind = EXCLUDED.kind,
                geom = EXCLUDED.geom, opened_at = EXCLUDED.opened_at, closed_at = EXCLUDED.closed_at
            """,
            [(source, *row) for row in data],
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--source", required=True, help="取り込み元の名前。例: yatsushiro-water-2026")
    a = ap.parse_args()
    with open(a.csv, encoding="utf-8-sig", newline="") as f:
        data = rows(csv.DictReader(f))
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        upsert(conn, a.source, data)
    print(f"{len(data)} 件を取り込みました（source={a.source}）")


if __name__ == "__main__":
    main()
