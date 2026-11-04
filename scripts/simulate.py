"""合成データ（data/out/points_*.csv.gz）を REST API 経由で投入し、検知〜地図までを通して動かす。

    python scripts/simulate.py --api http://localhost:18080 --people 300 --days 8 --stride 2

日付は「地震の翌日（D1）= 今日」になるようにずらす（--no-shift でそのまま）。
今日の分も丸 1 日ぶん入れるので、時間が進むにつれて検知が「今」の集まりを見つける（再生になる）。
投入後: docker compose exec api python -m app.detect_job → http://localhost:18080 で候補が出る。
"""

import argparse
import csv
import glob
import gzip
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))


def call(api, path, body, token=None):
    req = urllib.request.Request(
        f"{api}/v1{path}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as res:
        return json.load(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:18080")
    ap.add_argument("--out", default="data/out")
    ap.add_argument("--people", type=int, default=300, help="先頭から何人ぶん入れるか")
    ap.add_argument(
        "--days",
        type=int,
        default=8,
        help="最後の何日ぶんを入れるか（平常 5 + 地震当日 + 2 日 = 8）",
    )
    ap.add_argument(
        "--stride",
        type=int,
        default=2,
        help="何分おきの点を入れるか（画面は 30 秒おきに記録するが、量を抑える）",
    )
    ap.add_argument(
        "--no-shift", action="store_true", help="日付をずらさない（地震の翌日を今日にしない）"
    )
    a = ap.parse_args()

    events = list(csv.DictReader(open(f"{a.out}/events.csv", encoding="utf-8")))
    d1 = date.fromisoformat(min(e["date"] for e in events if e["day"] == "D1"))
    shift = timedelta(0) if a.no_shift else datetime.now(JST).date() - d1
    files = sorted(glob.glob(f"{a.out}/points_*.csv.gz"))[-a.days :]
    print(
        f"{len(files)} 日ぶん（{files[0][-17:-7]}〜{files[-1][-17:-7]}）を {shift.days} 日ずらして入れる"
    )

    # セッション（1 人 1 つ）。2 回目以降は同じトークンを使う
    tok_path = f"{a.out}/simulate_sessions.json"
    tokens = json.load(open(tok_path)) if os.path.exists(tok_path) else {}
    uids = [f"u{i + 1:04d}" for i in range(a.people)]
    for uid in uids:
        if uid not in tokens:
            tokens[uid] = call(a.api, "/sessions", {"consent": True})["token"]
    json.dump(tokens, open(tok_path, "w"))

    def send(uid, pts):
        for k in range(0, len(pts), 1000):
            call(a.api, "/locations", {"points": pts[k : k + 1000]}, tokens[uid])
        return len(pts)

    want = set(uids)
    total = 0
    for fn in files:
        by_uid = {u: [] for u in uids}
        with gzip.open(fn, "rt", encoding="utf-8") as f:
            r = csv.reader(f)
            next(r)
            for uid, t, lat, lon in r:
                if uid in want and int(t[14:16]) % a.stride == 0:
                    at = (datetime.fromisoformat(t) + shift).isoformat()
                    by_uid[uid].append({"lat": float(lat), "lon": float(lon), "recorded_at": at})
        with ThreadPoolExecutor(8) as ex:
            total += sum(ex.map(lambda u: send(u, by_uid[u]), uids))
        print(
            fn[-17:-7],
            "→",
            (date.fromisoformat(fn[-17:-7]) + shift).isoformat(),
            f"累計 {total} 点",
        )
    print("done. 次: docker compose exec api python -m app.detect_job")


if __name__ == "__main__":
    main()
