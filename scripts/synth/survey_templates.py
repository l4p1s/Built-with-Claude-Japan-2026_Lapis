"""記入フォームの回答（data/survey-answers-*.json、リポジトリには入れない）から「行動の型」を取り出す。

    python scripts/synth/survey_templates.py data/survey-answers-2026-11-06.json --out data/out/survey_templates.json

型には名前・座標・場所の名前を残さず、「何の種類の場所に、何時から何時まで、ふだんも行く所か、家からどのくらいの距離か（3 段階）」だけを残す。
make_demo_data.py --templates に渡すと、PARAMS の確率の代わりにこの型から 1 日の予定を作る（docs/demo-data-design.md §3b）。
"""

import argparse
import json
import math
from datetime import date

DEFAULT_HOME = (
    32.507,
    130.602,
)  # 記入フォームの地図の初期位置（市役所）。動かさなかった回答は家が分からない
BANDS = (300, 2000)  # 家からの距離の段階: 0 = 300m 未満、1 = 2km 未満、2 = それ以上
AID = ("water", "food", "goods", "shelter")  # 支援拠点への訪問。場所は台本の集まりが決める


def dist_m(a, b):
    la = math.radians((a[0] + b[0]) / 2)
    return math.hypot((a[0] - b[0]) * 110574, (a[1] - b[1]) * 111320 * math.cos(la))


def hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def clean_stays(stays, home):
    """家以外の滞在を時刻順に。重なりは前の「出た」を次の「着いた」に詰め、to < from は翌日まで（1440）とみなす。
    1 時間未満の職場は、次の場所（家に帰った時刻）の 15 分前まで延ばす（フォームの既定の「15分」を直さなかった回答が 2 件）"""
    seq = sorted(stays, key=lambda s: hm(s["from"]))
    out = []
    for s, nxt in zip(seq, seq[1:] + [None]):
        f, t = hm(s["from"]), hm(s["to"])
        if t <= f:
            t = 1440
        if (
            s["type"] == "work" and t - f < 60 and nxt
        ):  # ponytail: 既定値の直し忘れの救済。回答が増えて要らなければ消す
            t = max(t, hm(nxt["from"]) - 15)
        if out and out[-1]["to"] > f:
            out[-1]["to"] = f  # 家に帰った時刻とも重なりを詰める
        if s["type"] == "home":
            continue
        d = dist_m(home, (s["lat"], s["lon"]))
        out.append(
            {
                "type": s["type"],
                "from": f,
                "to": t,
                "usual": bool(s.get("usual")),
                "band": sum(d >= b for b in BANDS),
                "mode": s.get("mode"),
            }
        )
    return [s for s in out if s["to"] > s["from"]]


def day_kind(day, quake):
    label = day["label"]
    if label == "いつもの平日":
        return "weekday"
    if label == "いつもの休日":
        return "weekend"
    if label == "地震の日":
        return "D0"
    if not day.get("date"):
        return None
    d = date.fromisoformat(day["date"])
    if d > quake:
        return "after"
    return "weekend" if d.weekday() >= 5 else "weekday"


def templates_from_answers(answers, quake):
    people, aid = [], []
    for r in answers:
        a = r["answer"]
        home = tuple(a["home"])
        if "動作確認" in a.get("name", "") or tuple(round(x, 3) for x in home) == DEFAULT_HOME:
            continue
        days = {"after": []}
        for day in a["days"]:
            k = day_kind(day, quake)
            if not k:
                continue
            stays = clean_stays(day["stays"], home)
            if k == "after":
                days["after"].append(stays)
                aid += [s["to"] - s["from"] for s in stays if s["type"] in AID]
            else:
                days[k] = stays  # 同じ種類の日が 2 つあれば後の方
        if "weekday" not in days:
            continue
        all_days = [d for k, d in days.items() if k != "after"] + days["after"]
        car = any(s["mode"] == "car" for d in all_days for s in d)
        people.append({"id": f"s{len(people) + 1:02d}", "car": car, "days": days})
    return {"quake_date": quake.isoformat(), "people": people, "aid_stay_min": sorted(aid)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("answers")
    ap.add_argument("--quake-date", default="2026-07-28")
    ap.add_argument("--out", default="data/out/survey_templates.json")
    a = ap.parse_args()
    answers = json.load(open(a.answers, encoding="utf-8"))
    t = templates_from_answers(answers, date.fromisoformat(a.quake_date))
    t["source"] = a.answers
    json.dump(t, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_after = sum(len(p["days"]["after"]) for p in t["people"])
    print(
        f"回答 {len(answers)} 件 → 型 {len(t['people'])} 人（災害後の日 {n_after} 日、支援拠点の滞在 {len(t['aid_stay_min'])} 件）→ {a.out}"
    )


if __name__ == "__main__":
    main()
