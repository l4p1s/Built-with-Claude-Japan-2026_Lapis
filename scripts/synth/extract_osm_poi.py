"""OSM 由来の POI データベース（別プロジェクトの places.db）から八代市中心部の施設を抜き出す。1回だけ実行する。

    python scripts/synth/extract_osm_poi.py [/path/to/places.db]

出力 data/osm/poi_yatsushiro.csv は © OpenStreetMap contributors（ODbL）。
"""

import csv
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "/home/lapis/TempGPS/data/places.db"
OUT = "data/osm/poi_yatsushiro.csv"
BBOX = (32.42, 32.62, 130.52, 130.72)  # 八代市の平野部（南北 32.42–32.62、東西 130.52–130.72）
KINDS = (
    "supermarket",
    "convenience",
    "department_store",
    "mall",
    "restaurant",
    "cafe",
    "pub",
    "industrial",
    "office",
    "hospital",
    "school",
    "park",
    "community_centre",
    "fuel",
    "pharmacy",
    "clothes",
    "bakery",
    "hairdresser",
)


def main() -> None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = f"""SELECT name, kind, lat, lng FROM poi
            WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? AND kind IN ({",".join("?" * len(KINDS))})
            ORDER BY kind, lat, lng"""
    rows = con.execute(q, (BBOX[0], BBOX[1], BBOX[2], BBOX[3], *KINDS)).fetchall()
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "kind", "lat", "lon"])
        for name, kind, lat, lon in rows:
            w.writerow([name.replace("　", " ").strip(), kind, f"{lat:.6f}", f"{lon:.6f}"])
    print(f"{len(rows)} 件 → {OUT}")


if __name__ == "__main__":
    main()
