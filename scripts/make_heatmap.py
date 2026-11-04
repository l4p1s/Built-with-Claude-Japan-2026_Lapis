"""合成データ（data/out/points_*.csv.gz）を 5 分おきに間引いて、時刻を動かせるヒートマップ用のデータと画面を作る。

    python scripts/make_heatmap.py --out data/out
    python3 -m http.server 18091 -d data/out   # → http://localhost:18091/heatmap.html

出力: data/out/heatmap/<日付>.bin（int16 の格子座標。1 日 288 コマ × 人数 × (lat, lon)）、index.json、heatmap.html
"""

import argparse
import csv
import glob
import gzip
import json
import os
import shutil
from datetime import date, timedelta

import numpy as np

STEP = 5  # 分
ORIGIN = (
    32.40,
    130.50,
)  # 格子の原点。座標は (deg - 原点) × 10000 を int16 で持つ（約 11m 刻み、0.2° まで）
SCALE = 10000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/out")
    a = ap.parse_args()
    hdir = f"{a.out}/heatmap"
    os.makedirs(hdir, exist_ok=True)
    days = []
    for fn in sorted(glob.glob(f"{a.out}/points_*.csv.gz")):
        d = os.path.basename(fn)[7:17]
        uids, frames = {}, {}
        with gzip.open(fn, "rt", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r)
            for uid, t, lat, lon in r:
                m = int(t[11:13]) * 60 + int(t[14:16])
                if m % STEP:
                    continue
                i = uids.setdefault(uid, len(uids))
                frames.setdefault(m // STEP, {})[i] = (float(lat), float(lon))
        n, k = len(uids), 1440 // STEP
        arr = np.zeros((k, n, 2), np.int16)
        for fi, pts in frames.items():
            for i, (lat, lon) in pts.items():
                arr[fi, i] = (round((lat - ORIGIN[0]) * SCALE), round((lon - ORIGIN[1]) * SCALE))
        arr.tofile(f"{hdir}/{d}.bin")
        days.append({"date": d, "people": n})
        print(d, n, "人")
    events = list(csv.DictReader(open(f"{a.out}/events.csv", encoding="utf-8")))
    # 家の位置（uid 順）。画面で「家の周りを除く」に使う。検知も自宅の周り 200m は対象外にする（CLAUDE.md §2）
    homes = [
        [float(r["home_lat"]), float(r["home_lon"])]
        for r in csv.DictReader(open(f"{a.out}/people.csv", encoding="utf-8"))
    ]
    d1 = next((e["date"] for e in events if e["day"] == "D1"), None)
    quake = str(date.fromisoformat(d1) - timedelta(days=1)) if d1 else days[-1]["date"]
    json.dump(
        {
            "step": STEP,
            "origin": ORIGIN,
            "scale": SCALE,
            "quake": quake,
            "days": days,
            "events": events,
            "homes": homes,
        },
        open(f"{hdir}/index.json", "w", encoding="utf-8"),
        ensure_ascii=False,
    )
    shutil.copy(os.path.join(os.path.dirname(__file__), "heatmap.html"), f"{a.out}/heatmap.html")
    print("wrote", hdir)


if __name__ == "__main__":
    main()
