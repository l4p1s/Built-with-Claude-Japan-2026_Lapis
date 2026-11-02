"""BODIK 令和8年熊本地震の避難所データを official_spots に取り込む。何度実行しても重複しない。

    docker compose run --rm api python scripts/import_bodik.py

データの利用条件を確認できるまで、データ自体はリポジトリに入れない。
"""

import json
import os
import urllib.request

import psycopg

URL = "https://odcs.bodik.jp/kumamoto-r8/data/portal/shelters.geojson"
SOURCE = "bodik-kumamoto-r8"


def rows(geojson: dict) -> list[tuple]:
    """取り込む行。キーは「市町村コード:名前」（BODIK のデータには ID がない）。

    同じ施設が2行で入っていることがあるので、開設の記録がある行、その中でも開設中（閉鎖日時なし）または閉鎖が遅い行を後ろに並べる。取り込みは後の行で上書きするので、その行が残る。
    """
    features = sorted(
        geojson["features"],
        key=lambda f: (
            f["properties"].get("opened_at") is not None,
            f["properties"].get("closed_at") is None,
            f["properties"].get("closed_at") or "",
        ),
    )
    out = []
    for f in features:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        key = f"{p['municipality_code']}:{p['name']}"
        out.append(
            (SOURCE, key, p["name"], p.get("address"), lon, lat, p.get("opened_at"), p.get("closed_at"))
        )
    return out


# ponytail: 取り込み元から消えた行は消さない。取り下げられた避難所が残るようなら、取り込みのたびに source の行を入れ替える
def main() -> None:
    with urllib.request.urlopen(URL, timeout=60) as res:
        data = rows(json.load(res))
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO official_spots
                (source, source_key, name, address, kind, geom, opened_at, closed_at)
            VALUES (%s, %s, %s, %s, 'shelter', ST_MakePoint(%s, %s)::geography, %s, %s)
            ON CONFLICT (source, source_key) DO UPDATE SET
                name = EXCLUDED.name, address = EXCLUDED.address, geom = EXCLUDED.geom,
                opened_at = EXCLUDED.opened_at, closed_at = EXCLUDED.closed_at
            """,
            data,
        )
    print(f"{len(data)} 件を取り込みました（{URL}）")


if __name__ == "__main__":
    main()
