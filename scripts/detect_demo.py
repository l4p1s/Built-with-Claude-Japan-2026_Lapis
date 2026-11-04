"""合成データ（data/out）に検知（app/detect.py）を流し、events.csv と答え合わせする。

    python scripts/detect_demo.py --out data/out

出力: data/out/detect_result.md（検知率・検知までの時間・誤報）と detect_candidates.csv
"""

import argparse
import csv
import glob
import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.detect import (  # noqa: E402
    Person,
    baseline_share,
    detect,
    dist_m,
    learn_person,
    stays_from_track,
)
from app.mesh import mesh_code  # noqa: E402

NIGHT = 3 * 60  # 自宅を決める時刻（3:00）
MATCH_M = 300  # 候補の中心が集まりからこの距離なら当たり
OFFICIAL_M = 150  # 公式の拠点からこの距離の候補は「公式」として分ける


KEEP = None  # --sample で間引いたとき、残す uid の集合（None なら全員）


def read_day(path):
    """points_*.csv.gz → uid → (lats, lons)。1 分おき 1440 点の前提"""
    lats, lons = defaultdict(list), defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        for uid, _, la, lo in r:
            if KEEP is not None and uid not in KEEP:
                continue
            lats[uid].append(float(la))
            lons[uid].append(float(lo))
    return {u: (lats[u], lons[u]) for u in lats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/out")
    ap.add_argument(
        "--no-baseline",
        action="store_true",
        help="平常期を使わない「当日だけ」版: その日の 3:00 の位置を自宅として除き、期待値はメッシュ人口で配分する",
    )
    ap.add_argument("--data", default="data")
    ap.add_argument(
        "--learn-days", type=int, default=0, help="平常期のうち直近の何日で学習するか（0 = 全部）"
    )
    ap.add_argument(
        "--quake-at", default="16:27", help="地震の時刻。誤報は地震より後のものだけ数える"
    )
    ap.add_argument(
        "--sample",
        type=float,
        default=1.0,
        help="参加率の見立て: この割合の人だけを残して検知する（0.05 = 5%%）。残す人は --sample-seed で決める",
    )
    ap.add_argument("--sample-seed", type=int, default=1)
    a = ap.parse_args()
    tag = "当日だけ" if a.no_baseline else f"平常期で学習（{a.learn_days or '全'}日）"
    if a.sample < 1:
        import random

        global KEEP
        uids = [r["uid"] for r in csv.DictReader(open(f"{a.out}/people.csv", encoding="utf-8"))]
        KEEP = set(random.Random(a.sample_seed).sample(uids, max(1, round(len(uids) * a.sample))))
        tag += f"、参加率 {a.sample:.0%}（{len(KEEP)} 人）"
    events = list(csv.DictReader(open(f"{a.out}/events.csv", encoding="utf-8")))
    places = list(csv.DictReader(open(f"{a.out}/places.csv", encoding="utf-8")))
    official = [(float(p["lat"]), float(p["lon"])) for p in places if p["official_shelter"] == "1"]
    d0 = date.fromisoformat(min(e["date"] for e in events if e["day"] == "D0")).toordinal()
    files = sorted(glob.glob(f"{a.out}/points_*.csv.gz"))
    normal = [f for f in files if date.fromisoformat(f[-17:-7]).toordinal() < d0]
    after = [f for f in files if date.fromisoformat(f[-17:-7]).toordinal() >= d0]
    if a.learn_days:
        normal = normal[-a.learn_days :]

    # 1. 平常期: 人ごとの滞在と深夜の位置 → いつもの場所・自宅・探索型。マス別の滞在件数 → 期待値の配分
    stays_by = defaultdict(list)
    nights = defaultdict(list)
    cell_counts = Counter()
    for f in [] if a.no_baseline else normal:
        for uid, (la, lo) in read_day(f).items():
            st = stays_from_track(la, lo)
            stays_by[uid].append(st)
            nights[uid].append((la[NIGHT], lo[NIGHT]))
            cell_counts.update(mesh_code(s.lat, s.lon) for s in st)
        print("learned", f[-17:-7])
    people = {u: learn_person(stays_by[u], nights[u]) for u in stays_by}
    if a.no_baseline:
        # 期待値の配分は国勢調査のメッシュ人口（250m → 500m にまとめる）
        from make_demo_data import load_home_weights

        cells, _ = load_home_weights(a.data)
        for lat, lon, pop, _, _ in cells:
            cell_counts[mesh_code(lat, lon)] += pop
    share = baseline_share(cell_counts)
    explorers = sum(p.explorer for p in people.values())

    # 2. 地震後: いつもと違う滞在 → 検知
    if a.no_baseline:
        # 自宅は地震当日の 3:00 の位置（地震前の最後の夜。1 晩ぶんのデータだけ使う）
        people = {
            uid: Person((la[NIGHT], lo[NIGHT]), [], False)
            for uid, (la, lo) in read_day(after[0]).items()
        }
    cands = []
    for f in after:
        d = f[-17:-7]
        unusual = []
        for uid, (la, lo) in read_day(f).items():
            p = people[uid]
            for s in stays_from_track(la, lo):
                if not p.is_usual(s.lat, s.lon):
                    unusual.append((uid, s, p.weight))
        found = detect(unusual, share)
        for c in found:
            c.date = d
            c.official = any(dist_m(c.lat, c.lon, *o) <= OFFICIAL_M for o in official)
        cands += found
        print("detected", d, "unusual stays", len(unusual))

    # 3. 答え合わせ（公式の集まりは除外: 当たっても外れても数えない）
    targets = [e for e in events if e["official"] == "0"]
    hm = lambda s: int(s[:2]) * 60 + int(s[3:5])  # noqa: E731
    rows, hit = [], 0
    matched = set()
    for e in targets:
        found = [
            c
            for c in cands
            if c.date == e["date"]
            and dist_m(c.lat, c.lon, float(e["lat"]), float(e["lon"])) <= MATCH_M
            and c.window_start < hm(e["end"]) + 30
            and c.window_end > hm(e["start"])
        ]
        if found:
            hit += 1
            # 集まりが始まってから、最初に候補になった時間帯の終わりまで
            ends = [w1 for c in found for w0, w1 in c.windows if w1 > hm(e["start"])]
            delay = min(ends) - hm(e["start"])
            matched |= {id(c) for c in found}
            rows.append((e, f"検知（{delay} 分後、人数 {max(c.observed for c in found):.0f}）"))
        else:
            rows.append((e, "未検知"))
    official_ev = [e for e in events if e["official"] == "1"]
    for e in official_ev:
        for c in cands:
            if (
                c.date == e["date"]
                and dist_m(c.lat, c.lon, float(e["lat"]), float(e["lon"])) <= MATCH_M
            ):
                matched.add(id(c))
                c.official = True
    quake_min = hm(a.quake_at)
    d0_date = date.fromordinal(d0).isoformat()
    false_alarms = [
        c
        for c in cands
        if id(c) not in matched
        and not c.official
        and (
            c.date != d0_date or c.window_start >= quake_min
        )  # 地震より前に始まった候補（出勤など）は数えない
    ]
    official_cands = [c for c in cands if c.official and id(c) not in matched]

    suffix = "_nobase" if a.no_baseline else (f"_{a.learn_days}d" if a.learn_days else "")
    if a.sample < 1:
        suffix += f"_s{round(a.sample * 100)}"
    with open(f"{a.out}/detect_candidates{suffix}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "date",
                "cell",
                "lat",
                "lon",
                "start",
                "end",
                "observed",
                "expected",
                "p_value",
                "official",
                "matched",
            ]
        )
        for c in sorted(cands, key=lambda c: (c.date, c.window_start)):
            w.writerow(
                [
                    c.date,
                    c.cell,
                    f"{c.lat:.5f}",
                    f"{c.lon:.5f}",
                    f"{c.window_start // 60:02d}:{c.window_start % 60:02d}",
                    f"{c.window_end // 60:02d}:{c.window_end % 60:02d}",
                    f"{c.observed:.1f}",
                    f"{c.expected:.2f}",
                    f"{c.p_value:.2e}",
                    int(c.official),
                    int(id(c) in matched),
                ]
            )
    with open(f"{a.out}/detect_result{suffix}.md", "w", encoding="utf-8") as f:
        f.write(f"# 検知の答え合わせ（合成データ、{tag}）\n\n")
        f.write(
            f"人数 {len(people) or len(after and read_day(after[0]))} / 平常 {len(normal) if not a.no_baseline else 0} 日で学習 / 探索型と判定 {explorers} 人 / 地震後 {len(after)} 日に検知\n\n"
        )
        f.write(
            f"**検知率 {hit}/{len(targets)}**（公式の {len(official_ev)} 件は除外）、誤報 {len(false_alarms)} 件、公式の拠点に当たった候補 {len(official_cands)} 件\n\n"
        )
        f.write("| 日 | 場所 | 種類 | 時間 | 人数 | 結果 |\n|---|---|---|---|---|---|\n")
        for e, r in rows:
            f.write(
                f"| {e['date']} | {e['place']} | {e['kind']} | {e['start']}–{e['end']} | {e['visitors']} | {r} |\n"
            )
        f.write("\n## 誤報（集まりでない候補）\n\n")
        for c in false_alarms:
            f.write(
                f"- {c.date} {c.window_start // 60:02d}:{c.window_start % 60:02d}–{c.window_end // 60:02d}:{c.window_end % 60:02d} マス {c.cell} 人数 {c.observed:.0f}（期待 {c.expected:.2f}）\n"
            )
        f.write("\n## 公式の拠点に当たった候補\n\n")
        for c in official_cands:
            f.write(
                f"- {c.date} {c.window_start // 60:02d}:{c.window_start % 60:02d} マス {c.cell} 人数 {c.observed:.0f}\n"
            )
    json.dump(
        {
            "tag": tag,
            "sample": a.sample,
            "targets": [
                {
                    "place": e["place"],
                    "kind": e["kind"],
                    "date": e["date"],
                    "visitors": int(e["visitors"]),
                    "hit": not r.startswith("未検知"),
                }
                for e, r in rows
            ],
            "false_alarms": len(false_alarms),
        },
        open(f"{a.out}/detect_result{suffix}.json", "w", encoding="utf-8"),
        ensure_ascii=False,
    )
    print(
        f"[{tag}] 検知 {hit}/{len(targets)} 誤報 {len(false_alarms)} → {a.out}/detect_result{suffix}.md"
    )


if __name__ == "__main__":
    main()
