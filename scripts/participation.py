"""参加率（アプリを入れている人の割合）を変えて、何人の集まりまで見つかるかを測る。

    python scripts/participation.py --out data/out <scratchpad>/seed2 ... --rates 0.05 0.1 0.2 0.5 1

合成データは 1000 人 = 市民の縮図（集まりの人数は実際の規模）。参加率 p では、集まりに来る人のうち p だけがアプリ利用者になる。
人を間引いて scripts/detect_demo.py を回し（平常期の学習も間引いた人だけで行う）、集まりの大きさ × 参加率 ごとの検知率を表にする。
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict

BINS = [
    (0, 30, "〜29 人"),
    (30, 60, "30〜59 人"),
    (60, 10**9, "60 人〜"),
]  # 台本の集まりは最大 90 人


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        nargs="+",
        default=["data/out"],
        help="合成データの置き場（複数可。seed を変えたもの）",
    )
    ap.add_argument("--rates", nargs="+", type=float, default=[0.05, 0.1, 0.2, 0.5, 1.0])
    ap.add_argument(
        "--skip-run", action="store_true", help="検知を回さず、残っている結果だけ集計する"
    )
    a = ap.parse_args()
    cell = defaultdict(lambda: [0, 0])  # (bin, rate) → [検知, 件数]
    alarms = defaultdict(list)  # rate → 誤報の件数
    for out in a.out:
        for rate in a.rates:
            suffix = "" if rate >= 1 else f"_s{round(rate * 100)}"
            if not a.skip_run:
                subprocess.run(
                    [sys.executable, "scripts/detect_demo.py", "--out", out, "--sample", str(rate)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
            res = json.load(open(f"{out}/detect_result{suffix}.json", encoding="utf-8"))
            alarms[rate].append(res["false_alarms"])
            for t in res["targets"]:
                for lo, hi, label in BINS:
                    if lo <= t["visitors"] < hi:
                        c = cell[(label, rate)]
                        c[1] += 1
                        c[0] += t["hit"]
    print("| 集まりの人数 | " + " | ".join(f"参加率 {r:.0%}" for r in a.rates) + " |")
    print("|---|" + "---|" * len(a.rates))
    for _, _, label in BINS:
        row = [label]
        for r in a.rates:
            hit, n = cell[(label, r)]
            row.append(f"{hit}/{n}" if n else "—")
        print("| " + " | ".join(row) + " |")
    print(
        "| 誤報（1 本あたり、3 日） | "
        + " | ".join(f"{sum(alarms[r]) / len(alarms[r]):.1f}" for r in a.rates)
        + " |"
    )


if __name__ == "__main__":
    main()
