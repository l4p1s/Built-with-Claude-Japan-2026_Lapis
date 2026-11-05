"""地図の背景に出す八代市の施設を web/places.geojson にまとめる（docs/web-ui-design.md §1.7）。

    python scripts/make_places.py

- 入力: data/bodik の公共施設・観光施設（CC BY 4.0）と data/osm/poi_yatsushiro.csv（© OpenStreetMap contributors, ODbL）
- 同じ種類の施設が 80m 以内、または名前が重なって 300m 以内（「市立代陽小学校」と「代陽小学校」）にあるときは、
  先に読んだ八代市のデータ（名前が正式）を残す
- z = 名前を出し始めるズーム。災害時に探すもの（店・病院・役所・給油）は 14、それ以外は 15
"""

import csv
import json
import math

LAT_M = 110574.0
LON_M = 111320.0 * math.cos(math.radians(32.5))
Z = {
    "shop": 14,
    "medical": 14,
    "public": 14,
    "fuel": 14,
    "school": 15,
    "park": 15,
    "food": 15,
    "sight": 15,
}
BODIK = {  # POI コード → 種類
    "1302": "public",
    "1304": "public",
    "1305": "public",
    "1309": "public",
    "1390": "public",  # 市役所・支所・公民館など
    "1503": "school",
    "1504": "school",
    "1003": "park",
    "1005": "park",
    "1106": "park",  # 公園・グラウンド
}
OSM = {
    "supermarket": "shop",
    "convenience": "shop",
    "pharmacy": "shop",
    "hospital": "medical",
    "community_centre": "public",
    "fuel": "fuel",
    "school": "school",
    "park": "park",
    "restaurant": "food",
    "cafe": "food",
    "bakery": "food",
}  # 美容室・工場・居酒屋・服屋は災害時に探すものではないので出さない


def main():
    places = []

    def add(name, kind, lat, lon):
        lat, lon = (
            float(lat),
            float(lon.split(",")[0]),
        )  # 観光施設の 3 件は経度の後ろに「,13」などが付いている
        dist = lambda p: math.hypot((p[2] - lat) * LAT_M, (p[3] - lon) * LON_M)  # noqa: E731

        def same(p):
            return p[1] == kind and (
                dist(p) < 80 or (dist(p) < 300 and (name in p[0] or p[0] in name))
            )

        if name and not any(same(p) for p in places):
            places.append((name, kind, lat, lon))

    for r in csv.DictReader(open("data/bodik/432024_public_facility.csv", encoding="utf-8-sig")):
        kind = BODIK.get(r["POIコード"]) or (
            "medical"
            if r["POIコード"] == "1399" and ("保健" in r["名称"] or "診療所" in r["名称"])
            else None
        )
        if kind and r["緯度"]:
            add(r["名称"], kind, r["緯度"], r["経度"])
    for r in csv.DictReader(open("data/bodik/432024_tourism.csv", encoding="utf-8-sig")):
        if r["緯度"]:
            add(
                r["名称"],
                "shop" if "物産館" in r["名称"] or "直売" in r["名称"] else "sight",
                r["緯度"],
                r["経度"],
            )
    for r in csv.DictReader(open("data/osm/poi_yatsushiro.csv", encoding="utf-8")):
        if r["kind"] in OSM:
            add(r["name"], OSM[r["kind"]], r["lat"], r["lon"])

    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {"name": name, "kind": kind, "z": Z[kind]},
        }
        for name, kind, lat, lon in places
    ]
    with open("web/places.geojson", "w", encoding="utf-8") as f:
        json.dump(
            {"type": "FeatureCollection", "features": features},
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    counts = {k: sum(p[1] == k for p in places) for k in Z}
    assert all(counts.values()), (
        counts
    )  # どの種類も 1 件以上ある（入力の列名・コードが変わったら気づく）
    print(len(places), counts)


if __name__ == "__main__":
    main()
