"""画面のデモ（/?demo=1）で再生する素材を web/demo/ に作る。

    python scripts/synth/make_demo_assets.py --out data/out --day D1

- timeline.json: 検知が見つけた候補（data/out/detect_candidates.csv。scripts/synth/detect_demo.py の出力）に、
  正解（events.csv）の名前・種類を付けたもの。公式の拠点に当たった候補は official=1
- people.bin: その日の人の位置（5 分おき、scripts/synth/make_heatmap.py の出力をそのまま）
デモは API を使わず、この 2 つだけで動く。
"""

import argparse
import csv
import json
import math
import os
import shutil

LAT_M = 110574.0
LON_M = 111320.0 * math.cos(math.radians(32.5))
HM = lambda s: int(s[:2]) * 60 + int(s[3:5])  # noqa: E731


def dist_m(lat1, lon1, lat2, lon2):
    return math.hypot((lat1 - lat2) * LAT_M, (lon1 - lon2) * LON_M)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/out")
    ap.add_argument("--day", default="D1")
    ap.add_argument("--dest", default="web/demo")
    ap.add_argument(
        "--scenario", help="台本（bbox を timeline.json に入れて、デモの最初の表示範囲にする）"
    )
    a = ap.parse_args()
    events = list(csv.DictReader(open(f"{a.out}/events.csv", encoding="utf-8")))
    date = next(e["date"] for e in events if e["day"] == a.day)
    events = [e for e in events if e["date"] == date]
    places = list(csv.DictReader(open(f"{a.out}/places.csv", encoding="utf-8")))
    cands = [
        c
        for c in csv.DictReader(open(f"{a.out}/detect_candidates.csv", encoding="utf-8"))
        if c["date"] == date
    ]
    out = []
    for i, c in enumerate(sorted(cands, key=lambda c: c["start"])):
        lat, lon = float(c["lat"]), float(c["lon"])
        s, e = HM(c["start"]), HM(c["end"])
        hit = [
            ev
            for ev in events
            if dist_m(lat, lon, float(ev["lat"]), float(ev["lon"])) <= 300
            and s < HM(ev["end"]) + 30
            and e > HM(ev["start"])
        ]
        hit.sort(key=lambda ev: dist_m(lat, lon, float(ev["lat"]), float(ev["lon"])))
        near = min(places, key=lambda p: dist_m(lat, lon, float(p["lat"]), float(p["lon"])))
        official = c["official"] == "1"
        out.append(
            {
                "id": i + 1,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "detect": s + 30,  # 最初の時間帯（30 分）の終わりで候補になる
                "end": e,
                "observed": round(float(c["observed"])),
                "official": official,
                # 公式なら種類は決まっている（避難所か公式給水）。それ以外は答えてもらうまで出さない
                "kind": ("water" if hit and hit[0]["kind"] == "water" else "shelter")
                if official
                else (hit[0]["kind"] if hit else None),
                "name": hit[0]["place"] if hit else near["name"],
            }
        )
    bbox = json.load(open(a.scenario, encoding="utf-8")).get("bbox") if a.scenario else None
    heat = f"{a.out}/heatmap/{date}.bin"
    idx = json.load(open(f"{a.out}/heatmap/index.json", encoding="utf-8"))
    os.makedirs(a.dest, exist_ok=True)
    shutil.copy(heat, f"{a.dest}/people.bin")
    json.dump(
        {
            "date": date,
            "label": {"D0": "地震当日", "D1": "地震の翌日", "D2": "地震の 2 日後"}.get(
                a.day, a.day
            ),
            "origin": idx["origin"],
            "scale": idx["scale"],
            "step": idx["step"],
            "people": next(d["people"] for d in idx["days"] if d["date"] == date),
            "bounds": [[bbox[2], bbox[0]], [bbox[3], bbox[1]]]
            if bbox
            else None,  # [[西, 南], [東, 北]]
            "candidates": out,
        },
        open(f"{a.dest}/timeline.json", "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=0,
    )
    print(
        f"{len(out)} 候補（公式 {sum(c['official'] for c in out)}）→ {a.dest}/timeline.json, people.bin"
    )


if __name__ == "__main__":
    main()
