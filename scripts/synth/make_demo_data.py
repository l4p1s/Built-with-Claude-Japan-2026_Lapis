"""八代市の1000人の1分ごとの位置を合成する（デモ・検知の実験用）。

    python scripts/synth/make_demo_data.py --quake-at 2026-07-28T16:00 --people 1000 --out data/out

入力は data/（BODIK 公共施設・観光施設・避難所、OSM の店、e-Stat のメッシュ人口。無ければ小字の代表点で代用）。
出力は data/out/points_YYYY-MM-DD.csv.gz（uid,time,lat,lon）と、答え合わせ用の people/places/events.csv、summary.md。
行動の割合は出典のない仮定値で、PARAMS にまとめている。設計は docs/demo-data-design.md。
"""

import argparse
import csv
import glob
import gzip
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import numpy as np

# 仮定値（出典なし。設計書 docs/demo-data-design.md に理由を書く）
PARAMS = {
    "role": {"worker": 0.55, "student": 0.15, "home": 0.30},  # 役割の割合
    "car": 0.8,  # 車で動く人の割合
    "explorer": 0.2,  # 探索型（平常日にも新しい場所へ行く人）の割合。Pappalardo 2015 の2分類を根拠に、割合は仮定
    "explore_per_day": 0.3,  # 探索型が1日に新しい場所へ行く回数の期待値
    "shop_after_work": 0.35,  # 平日、帰りにいつもの店へ寄る割合
    "home_shop": 0.6,  # 在宅の人が平日に買い物に出る割合
    "weekend_mall": 0.5,  # 休日に大型店へ行く割合
    "weekend_out": 0.2,  # 休日に公園・飲食店へ行く割合
    # 出典から置いた値: 2026年は避難指示 25.8 万人に対し避難所 8,698 人（熊本県 第1報）→ 約3%。
    # 2016年は避難者の 68.3% が車中泊（熊本県アンケート）→ 車中泊は避難所の 2 倍
    "evacuate_shelter": 0.03,  # 地震当日の夜に公式避難所へ行く割合
    "evacuate_car": 0.06,  # 地震当日の夜に車中泊する割合
    "shelter_home_visit_D1": 0.6,  # 翌日、避難所から昼に家の様子を見に帰る割合
    "shelter_return_D2": 0.3,  # 2日後に避難をやめて家に戻る割合
    "car_stop_D2": 0.5,  # 2日後に車中泊をやめる割合
    "work_D2": 0.3,  # 2日後に出勤する割合
    "queue_D2": 0.15,  # 2日後、再開した店の行列（60分）に並ぶ割合
    "visit_relative": {
        "D1": 0.25,
        "D2": 0.15,
    },  # 地震の翌日以降、親戚・知人の家（他の人の家）に 1〜3 時間いる割合
    "water_radius_m": 3500,  # 市役所からこの距離に家がある人を「断水」とし、給水に行くのはその人だけ（断水した校区の近似）
    "speed_kmh": {"car": 30, "walk": 4, "car_after": 20},  # 移動速度。地震後の車は渋滞で遅い
    "gps_noise_m": 10,  # GPS 誤差（標準偏差）
}
MINUTES = 1440
LAT_M = 110574.0  # 緯度1度の長さ（m）
LON_M = 111320.0 * math.cos(math.radians(32.5))  # 経度1度の長さ（八代の緯度）
BBOX = (32.42, 32.62, 130.52, 130.72)
CENTER = (32.5072, 130.6016)  # 八代市役所
POI = {
    "park": {"1003", "1005"},
    "community": {"1305", "1309", "1390"},
    "school": {"1503", "1504"},
    "danchi": {"401"},
}


def dist_m(lat1, lon1, lat2, lon2):
    return math.hypot((lat1 - lat2) * LAT_M, (lon1 - lon2) * LON_M)


def hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


# ---------- 入力 ----------
def load_places(data):
    """施設の一覧。kind: super/mall/conv/shop/park/community/bussan/school/work/shelter"""
    places, seen = [], set()

    def add(name, kind, lat, lon, source, official=0):
        key = (name, round(lat, 4), round(lon, 4))
        if key in seen or not (BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]):
            return
        seen.add(key)
        places.append(
            {
                "id": f"p{len(places):04d}",
                "name": name,
                "kind": kind,
                "lat": lat,
                "lon": lon,
                "official": official,
                "source": source,
            }
        )

    sh = json.load(open(f"{data}/bodik/shelters.geojson", encoding="utf-8"))
    for f in sh["features"]:
        p = f["properties"]
        if p["municipality_code"] == "432024" and p.get("opened_at"):
            lon, lat = f["geometry"]["coordinates"]
            add(p["name"], "shelter", lat, lon, "bodik-kumamoto-r8", 1)
    shelters = [p for p in places]

    def is_shelter(lat, lon):
        return any(dist_m(lat, lon, s["lat"], s["lon"]) < 150 for s in shelters)

    for r in csv.DictReader(open(f"{data}/bodik/432024_public_facility.csv", encoding="utf-8-sig")):
        if not r["緯度"]:
            continue
        lat, lon = float(r["緯度"]), float(r["経度"])
        for kind, codes in POI.items():
            if r["POIコード"] in codes and not is_shelter(lat, lon):
                add(r["名称"], kind, lat, lon, "bodik-public-facility")
    for r in csv.DictReader(open(f"{data}/bodik/432024_tourism.csv", encoding="utf-8-sig")):
        if r["緯度"] and ("物産館" in r["名称"] or "直売" in r["名称"]):
            add(r["名称"], "bussan", float(r["緯度"]), float(r["経度"]), "bodik-tourism")
    osm_kind = {
        "supermarket": "super",
        "department_store": "mall",
        "mall": "mall",
        "convenience": "conv",
        "restaurant": "shop",
        "cafe": "shop",
        "pub": "shop",
        "clothes": "shop",
        "bakery": "shop",
        "pharmacy": "shop",
        "industrial": "work",
        "office": "work",
        "hospital": "work",
        "fuel": "work",
        "hairdresser": "shop",
    }
    for r in csv.DictReader(open(f"{data}/osm/poi_yatsushiro.csv", encoding="utf-8")):
        k = osm_kind.get(r["kind"])
        if k and not re.search(
            r"pylon|substation", r["name"], re.IGNORECASE
        ):  # 送電塔などは職場にしない
            lat, lon = float(r["lat"]), float(r["lon"])
            if k == "super" and ("ゆめタウン" in r["name"] or "イオン" in r["name"]):
                k = "mall"
            add(r["name"], k, lat, lon, "osm")
    return places


def mesh_center(code):
    """地域メッシュ番号（8〜10桁）の中心の緯度経度。10桁 = 1/4 メッシュ（250m）"""
    lat = int(code[0:2]) / 1.5
    lon = int(code[2:4]) + 100
    lat += int(code[4]) * 5 / 60
    lon += int(code[5]) * 7.5 / 60
    lat += int(code[6]) * 30 / 3600
    lon += int(code[7]) * 45 / 3600
    h, w = 30 / 3600, 45 / 3600
    for d in code[8:]:
        h, w = h / 2, w / 2
        m = int(d) - 1
        lat += (m // 2) * h
        lon += (m % 2) * w
    return lat + h / 2, lon + w / 2


def load_home_weights(data):
    """家を置く点とその重み。e-Stat の 250m メッシュ人口があればそれ、無ければ小字の代表点（団地は重く）"""
    files = glob.glob(
        f"{data}/estat/tblT0011*"
    )  # T001142（2020 国勢調査）か T001102（同、JGD2000 版）
    cells = []
    for fn in files:
        raw = open(fn, "rb").read()
        text = (
            raw.decode("cp932", errors="replace")
            if b"\xff\xfe" not in raw[:2]
            else raw.decode("utf-16")
        )
        rows = list(csv.reader(text.splitlines()))
        head = rows[0]
        ci, pi = head.index("KEY_CODE"), 4  # 5列目が「人口（総数）」
        for r in rows[1:]:
            if len(r) <= pi or not r[ci].isdigit() or not r[pi].isdigit():
                continue
            lat, lon = mesh_center(r[ci])
            if BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]:
                cells.append((lat, lon, int(r[pi]), 125 / LAT_M, 125 / LON_M))
    if cells:
        return cells, f"e-Stat 国勢調査2020 250m メッシュ人口（{os.path.basename(files[0])}）"
    pts = json.load(open(f"{data}/addresses/yatsushiro.json", encoding="utf-8"))["data"]
    for p in pts:
        if not p.get("point"):
            continue
        lon, lat = p["point"]
        if BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]:
            cells.append(
                (lat, lon, 5 if "団地" in p.get("koaza", "") else 1, 150 / LAT_M, 150 / LON_M)
            )
    return cells, "小字の代表点（団地は重み5）。e-Stat のメッシュ人口が無いときの代用"


# ---------- 人 ----------
def make_people(n, rng, places, cells):
    by = defaultdict(list)
    for p in places:
        by[p["kind"]].append(p)
    w = np.array([c[2] for c in cells], float)
    w /= w.sum()
    roles = list(PARAMS["role"])
    people = []
    for i in range(n):
        c = cells[rng.choice(len(cells), p=w)]
        lat = c[0] + rng.uniform(-c[3], c[3])
        lon = c[1] + rng.uniform(-c[4], c[4])
        role = roles[rng.choice(len(roles), p=[PARAMS["role"][r] for r in roles])]
        work = None
        if role == "worker":
            pool = by["work"] + by["super"] + by["mall"] + by["school"] + by["community"]
            work = pool[rng.integers(len(pool))]
        elif role == "student":
            work = min(
                by["school"],
                key=lambda p: dist_m(lat, lon, p["lat"], p["lon"]) + rng.uniform(0, 400),
            )
        shops = by["super"] + by["mall"] + by["conv"]
        d = np.array([dist_m(lat, lon, p["lat"], p["lon"]) for p in shops])
        pw = np.exp(-d / 1200) * np.array([0.3 if p["kind"] == "conv" else 1.0 for p in shops])
        pw /= pw.sum()
        usual = [
            shops[j] for j in rng.choice(len(shops), size=rng.integers(1, 4), replace=False, p=pw)
        ]
        people.append(
            {
                "uid": f"u{i + 1:04d}",
                "lat": lat,
                "lon": lon,
                "role": role,
                "work": work,
                "usual": usual,
                "car": rng.random() < PARAMS["car"],
                "explorer": rng.random() < PARAMS["explorer"],
                "work_start": 8 * 60 + 30 + rng.integers(-30, 31),
                "work_len": 9 * 60 + rng.integers(-60, 61),
                "shelter": None,
                "car_spot": None,
                "sleep": None,  # 地震後の状態
            }
        )
    return people, by


# ---------- 1日の予定 → 1分ごとの位置 ----------
def track(start, visits, speed_m_min, end_pos=None):
    """visits: [(lat, lon, 着きたい時刻(分), いる長さ(分), 名前)]。直線で移動し、1分ごとの位置と、実際の滞在の一覧を返す"""
    lat = np.empty(MINUTES)
    lon = np.empty(MINUTES)
    t, pos = 0, start
    stays = []
    seq = sorted(visits, key=lambda v: v[2])
    if end_pos is not None:
        seq.append(
            (end_pos[0], end_pos[1], MINUTES + 600, 0, "end")
        )  # 最後は帰る（時刻は「できるだけ早く」ではなく最後）
    for vlat, vlon, want, dur, name in seq:
        travel = dist_m(pos[0], pos[1], vlat, vlon) / speed_m_min
        depart = max(t, want - travel)
        if name == "end":
            depart = t
        arrive = depart + travel
        if depart >= MINUTES:
            break
        d0, a0 = int(depart), min(int(math.ceil(arrive)), MINUTES)
        lat[t:d0] = pos[0]
        lon[t:d0] = pos[1]
        if a0 > d0:
            f = np.linspace(0, 1, a0 - d0, endpoint=False)
            lat[d0:a0] = pos[0] + (vlat - pos[0]) * f
            lon[d0:a0] = pos[1] + (vlon - pos[1]) * f
        leave = min(int(math.ceil(arrive + dur)), MINUTES)
        lat[a0:leave] = vlat
        lon[a0:leave] = vlon
        if arrive < MINUTES and name != "end":
            stays.append((name, int(arrive), leave))
        t, pos = max(a0, leave), (vlat, vlon)
    lat[t:] = pos[0]
    lon[t:] = pos[1]
    return lat, lon, stays, pos


def plan_day(p, kind, quake_min, rng, by, scen, day_tag, event_visits):
    """その人のその日の予定。(visits, 夜に寝る場所 or None=家)"""
    H = (p["lat"], p["lon"])
    V = list(event_visits)  # 台本で決まった集まりへの参加

    def pos(q):
        return (q["lat"], q["lon"])

    sleep = None

    def shop(after, pool=None):
        s = (pool or p["usual"])[rng.integers(len(pool or p["usual"]))]
        dur = rng.integers(5, 13) if s["kind"] == "conv" else rng.integers(20, 41)  # コンビニは短い
        V.append((*pos(s), after + rng.integers(5, 30), dur, s["name"]))

    def explore(lo, hi):
        pool = by["shop"] + by["park"] + by["mall"]
        s = pool[rng.integers(len(pool))]
        V.append((*pos(s), rng.integers(lo, hi), rng.integers(40, 91), s["name"]))

    if kind in ("weekday", "D0"):
        end_work = p["work_start"] + p["work_len"]
        if p["role"] in ("worker", "student"):
            ws = p["work_start"] if p["role"] == "worker" else 8 * 60 + rng.integers(-15, 16)
            wl = p["work_len"] if p["role"] == "worker" else 7 * 60 + 30 + rng.integers(-30, 31)
            if kind == "D0" and ws < quake_min:
                wl = min(wl, quake_min + rng.integers(0, 60) - ws)  # 地震で帰る
            V.append((*pos(p["work"]), ws, wl, p["work"]["name"]))
            end_work = ws + wl
            if kind == "weekday" and rng.random() < PARAMS["shop_after_work"]:
                shop(end_work)
        elif rng.random() < PARAMS["home_shop"] and (kind == "weekday" or rng.random() < 0.5):
            shop(
                rng.integers(10 * 60, 16 * 60)
                if kind == "weekday"
                else rng.integers(9 * 60, quake_min - 60)
            )
        if p["explorer"] and rng.random() < PARAMS["explore_per_day"] and kind == "weekday":
            explore(10 * 60, 20 * 60)
    elif kind == "weekend":
        r = rng.random()
        if r < PARAMS["weekend_mall"]:
            s = by["mall"][rng.integers(len(by["mall"]))]
            V.append((*pos(s), rng.integers(11 * 60, 16 * 60), rng.integers(60, 121), s["name"]))
        elif r < PARAMS["weekend_mall"] + PARAMS["weekend_out"]:
            explore(10 * 60, 17 * 60)
        if rng.random() < 0.3:
            shop(rng.integers(10 * 60, 18 * 60))
        if p["explorer"] and rng.random() < PARAMS["explore_per_day"]:
            explore(10 * 60, 20 * 60)

    # 地震後の避難
    if kind == "D0":
        r = rng.random()
        if r < PARAMS["evacuate_shelter"]:
            p["shelter"] = min(
                by["shelter"], key=lambda s: dist_m(*H, s["lat"], s["lon"]) + rng.uniform(0, 500)
            )
            V.append(
                (
                    *pos(p["shelter"]),
                    rng.integers(
                        18 * 60 + 30, 21 * 60 + 30
                    ),  # 避難所の開設は 16 時台〜22 時台（BODIK）
                    MINUTES,
                    p["shelter"]["name"],
                )
            )
            sleep = pos(p["shelter"])
        elif r < PARAMS["evacuate_shelter"] + PARAMS["evacuate_car"] and p["car"]:
            p["car_spot"] = scen["car_spots"][rng.integers(len(scen["car_spots"]))]
            V.append(
                (
                    *pos(p["car_spot"]),
                    rng.integers(18 * 60 + 30, 21 * 60 + 30),
                    MINUTES,
                    p["car_spot"]["name"],
                )
            )
            sleep = pos(p["car_spot"])
    elif kind in ("D1", "D2"):
        if not p["shelter"] and rng.random() < PARAMS["visit_relative"][kind]:
            # 親戚・知人の家。他の人の家から 5km 以内のものを引く
            for _ in range(10):
                h = scen["homes"][rng.integers(len(scen["homes"]))]
                if 50 < dist_m(*H, *h) < 5000:
                    V.append(
                        (*h, rng.integers(10 * 60, 15 * 60), rng.integers(60, 181), "relative")
                    )
                    break
        if p["shelter"]:
            if kind == "D2" and rng.random() < PARAMS["shelter_return_D2"]:
                V.append((*H, 9 * 60 + rng.integers(0, 120), MINUTES, "home"))
                p["shelter"] = None
            else:
                if rng.random() < PARAMS["shelter_home_visit_D1"]:
                    V.append((*H, 10 * 60 + rng.integers(0, 120), rng.integers(60, 150), "home"))
                V.append(
                    (
                        *pos(p["shelter"]),
                        17 * 60 + rng.integers(0, 120),
                        MINUTES,
                        p["shelter"]["name"],
                    )
                )
                sleep = pos(p["shelter"])
        elif p["car_spot"]:
            V.append((*H, 7 * 60 + rng.integers(0, 60), 60, "home"))
            if kind == "D2" and rng.random() < PARAMS["car_stop_D2"]:
                p["car_spot"] = None
            else:
                V.append(
                    (
                        *pos(p["car_spot"]),
                        20 * 60 + rng.integers(0, 90),
                        MINUTES,
                        p["car_spot"]["name"],
                    )
                )
                sleep = pos(p["car_spot"])
        if (
            kind == "D2" and not event_visits
        ):  # 集まりに行く人は、その日は他の予定を入れない（時間がぶつかるので）
            if p["role"] == "worker" and rng.random() < PARAMS["work_D2"]:
                V.append(
                    (*pos(p["work"]), p["work_start"] + 60, p["work_len"] - 120, p["work"]["name"])
                )
            if rng.random() < PARAMS["queue_D2"] and not p["shelter"]:
                s = scen["reopen"][rng.integers(len(scen["reopen"]))]
                V.append((*pos(s), rng.integers(9 * 60, 12 * 60), 60, s["name"]))
    return V, sleep


def pick_visitors(ev, people, rng, busy):
    """集まりに来る人: 家が radius 内で、その日に避難所にいない人から、近いほど高い確率で選ぶ。
    busy は uid → その日に参加が決まった集まりの時間帯。時間が重なる集まりには出ない（1日 2 件まで）。
    給水（water）は断水区域（市役所から water_radius_m）の人だけ"""
    w0, w1 = hm(ev["start"]), hm(ev["end"])
    cand = [
        i
        for i, p in enumerate(people)
        if len(busy.get(i, ())) < 2
        and all(w1 <= s or e <= w0 for s, e in busy.get(i, ()))
        and not p["shelter"]
        and dist_m(p["lat"], p["lon"], ev["lat"], ev["lon"]) <= ev["radius_m"]
        and (ev["kind"] != "water" or p["water_out"])
    ]
    if not cand:
        return []
    d = np.array([dist_m(people[i]["lat"], people[i]["lon"], ev["lat"], ev["lon"]) for i in cand])
    w = np.exp(-d / 700)
    w /= w.sum()
    n = min(ev["visitors"], len(cand))
    return [cand[j] for j in rng.choice(len(cand), size=n, replace=False, p=w)]


# ---------- 本体 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--quake-at",
        default="2026-07-28T16:27",
        help="地震の日時（JST）。令和8年熊本地震の発生は 2026-07-28 16:27（気象庁）",
    )
    ap.add_argument("--people", type=int, default=1000)
    ap.add_argument("--normal-days", type=int, default=14)
    ap.add_argument("--after-days", type=int, default=2)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="data/out")
    ap.add_argument(
        "--scenario",
        default=os.path.join(os.path.dirname(__file__), "demo_scenario.json"),
        help="集まりの台本（例: scripts/demo_scenario_video.json）",
    )
    ap.add_argument(
        "--bbox",
        help="家を置く範囲 lat0,lat1,lon0,lon1（省略すると台本の bbox、なければ市全体）",
    )
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    os.makedirs(a.out, exist_ok=True)

    quake = datetime.fromisoformat(a.quake_at)
    quake_min = quake.hour * 60 + quake.minute
    places = load_places(a.data)
    cells, home_source = load_home_weights(a.data)
    scen_json = json.load(open(a.scenario, encoding="utf-8"))
    bbox = [float(x) for x in a.bbox.split(",")] if a.bbox else scen_json.get("bbox")
    if bbox:
        lat0, lat1, lon0, lon1 = bbox
        cells = [c for c in cells if lat0 <= c[0] <= lat1 and lon0 <= c[1] <= lon1]
        home_source += f"、家は {bbox} の範囲だけ"
    people, by = make_people(a.people, rng, places, cells)
    byname = {p["name"]: p for p in places}
    missing = [
        n
        for n in [e["place"] for e in scen_json["events"]]
        + scen_json["car_sleeping"]
        + scen_json["reopen_D2"]
        if n not in byname
    ]
    if missing:
        raise SystemExit(f"台本の場所が places にない: {missing}")
    scen = {
        "car_spots": [byname[n] for n in scen_json["car_sleeping"]],
        "reopen": [byname[n] for n in scen_json["reopen_D2"]],
        "homes": [(p["lat"], p["lon"]) for p in people],
    }
    for p in people:
        p["water_out"] = dist_m(p["lat"], p["lon"], *CENTER) <= PARAMS["water_radius_m"]
    events = []
    for k, e in enumerate(scen_json["events"]):
        pl = byname[e["place"]]
        events.append(
            {
                **e,
                "event_id": f"e{k + 1:02d}",
                "place_id": pl["id"],
                "lat": pl["lat"],
                "lon": pl["lon"],
                "official": pl["official"],
            }
        )

    days = [
        (quake.date() - timedelta(days=a.normal_days - i), "weekday") for i in range(a.normal_days)
    ]
    days = [(d, "weekend" if d.weekday() >= 5 else "weekday") for d, _ in days]
    days.append((quake.date(), "D0"))
    days += [(quake.date() + timedelta(days=i), f"D{i}") for i in range(1, a.after_days + 1)]

    noise_lat = PARAMS["gps_noise_m"] / LAT_M
    noise_lon = PARAMS["gps_noise_m"] / LON_M
    stay_count = []  # (day, place, uid) 10分以上の滞在
    extra = []  # 台本にない集まり（車中泊・店の行列）。events.csv に足す
    car_names = {p["name"] for p in scen["car_spots"]}
    reopen_names = {p["name"] for p in scen["reopen"]}
    start_pos = {p["uid"]: (p["lat"], p["lon"]) for p in people}
    for d, kind in days:
        tag = kind if kind.startswith("D") else d.strftime("%a")
        day_events = [e for e in events if e["day"] == kind]
        ev_visits = defaultdict(list)
        busy = defaultdict(list)
        for e in day_events:
            chosen = pick_visitors(e, people, rng, busy)
            for i in chosen:
                busy[i].append((hm(e["start"]), hm(e["end"])))
            e.setdefault("chosen", {})[d] = len(chosen)
            for i in chosen:
                arrive = rng.integers(hm(e["start"]), hm(e["end"]) - e["stay_min"][0])
                ev_visits[i].append(
                    (e["lat"], e["lon"], int(arrive), int(rng.integers(*e["stay_min"])), e["place"])
                )
        times = [f"{d.isoformat()}T{m // 60:02d}:{m % 60:02d}+09:00" for m in range(MINUTES)]
        after = kind.startswith("D")
        day_stays = []
        # 朝まで車中泊している人（その日の予定を決める前の状態）
        morning = Counter(p["car_spot"]["name"] for p in people if p["car_spot"])
        with gzip.open(
            f"{a.out}/points_{d.isoformat()}.csv.gz", "wt", newline="", encoding="utf-8"
        ) as f:
            f.write("uid,time,lat,lon\n")
            for i, p in enumerate(people):
                visits, sleep = plan_day(
                    p, kind, quake_min, rng, by, scen, tag, ev_visits.get(i, [])
                )
                mode = "car" if p["car"] else "walk"
                speed = (
                    PARAMS["speed_kmh"]["car_after" if (after and mode == "car") else mode]
                    * 1000
                    / 60
                )
                home = (p["lat"], p["lon"])
                lat, lon, stays, last = track(
                    start_pos[p["uid"]], visits, speed, end_pos=None if sleep else home
                )
                start_pos[p["uid"]] = sleep or last
                lat = lat + rng.normal(0, noise_lat, MINUTES)
                lon = lon + rng.normal(0, noise_lon, MINUTES)
                f.writelines(
                    f"{p['uid']},{times[m]},{lat[m]:.6f},{lon[m]:.6f}\n" for m in range(MINUTES)
                )
                stay_count += [
                    (d.isoformat(), kind, s[0])
                    for s in stays
                    if s[2] - s[1] >= 10 and s[0] != "home"
                ]
                day_stays += stays
        if after:
            # 台本にない集まりも正解に足す: 車中泊（夜と翌朝）と、再開した店の行列
            night = Counter(
                s[0]
                for s in day_stays
                if s[0] in car_names and s[1] >= 18 * 60 and s[2] - s[1] >= 60
            )
            queue = (
                Counter(s[0] for s in day_stays if s[0] in reopen_names and s[2] - s[1] >= 30)
                if kind == "D2"
                else Counter()
            )
            for cnt, kd, st, en in (
                (night, "car_sleeping", "20:00", "23:59"),
                (morning, "car_sleeping", "00:00", "07:00"),
                (queue, "shop", "09:00", "12:30"),
            ):
                for name, n in cnt.items():
                    if n >= 3:
                        extra.append(
                            {
                                "day": kind,
                                "date": d,
                                "place": name,
                                "kind": kd,
                                "start": st,
                                "end": en,
                                "n": n,
                            }
                        )
        print(d, kind, "done")

    with open(f"{a.out}/people.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "uid",
                "home_lat",
                "home_lon",
                "role",
                "work",
                "usual_shop_ids",
                "car",
                "explorer",
                "water_out",
            ]
        )
        for p in people:
            w.writerow(
                [
                    p["uid"],
                    f"{p['lat']:.6f}",
                    f"{p['lon']:.6f}",
                    p["role"],
                    p["work"]["id"] if p["work"] else "",
                    " ".join(s["id"] for s in p["usual"]),
                    int(p["car"]),
                    int(p["explorer"]),
                    int(p["water_out"]),
                ]
            )
    with open(f"{a.out}/places.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["place_id", "name", "kind", "lat", "lon", "official_shelter", "source"])
        for p in places:
            w.writerow(
                [
                    p["id"],
                    p["name"],
                    p["kind"],
                    f"{p['lat']:.6f}",
                    f"{p['lon']:.6f}",
                    p["official"],
                    p["source"],
                ]
            )
    with open(f"{a.out}/events.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "event_id",
                "day",
                "date",
                "place_id",
                "place",
                "kind",
                "lat",
                "lon",
                "start",
                "end",
                "visitors",
                "official",
            ]
        )
        for e in events:
            for d, n in e.get("chosen", {}).items():
                w.writerow(
                    [
                        e["event_id"],
                        e["day"],
                        d.isoformat(),
                        e["place_id"],
                        e["place"],
                        e["kind"],
                        f"{e['lat']:.6f}",
                        f"{e['lon']:.6f}",
                        e["start"],
                        e["end"],
                        n,
                        e["official"],
                    ]
                )

        for k, x in enumerate(extra):
            pl = byname[x["place"]]
            w.writerow(
                [
                    f"x{k + 1:02d}",
                    x["day"],
                    x["date"].isoformat(),
                    pl["id"],
                    x["place"],
                    x["kind"],
                    f"{pl['lat']:.6f}",
                    f"{pl['lon']:.6f}",
                    x["start"],
                    x["end"],
                    x["n"],
                    pl["official"],
                ]
            )
    # まとめ
    per_day = defaultdict(Counter)
    for d, kind, name in stay_count:
        per_day[(d, kind)][name] += 1
    with open(f"{a.out}/summary.md", "w", encoding="utf-8") as f:
        f.write(
            f"# 合成データのまとめ\n\n人数 {a.people} / 日数 {len(days)} / 地震 {a.quake_at} / seed {a.seed}\n\n家の重み: {home_source}\n\n"
        )
        f.write("## 日ごとの、家以外での10分以上の滞在が多い場所（上位8）\n\n")
        for (d, kind), c in per_day.items():
            f.write(f"- {d} {kind}: " + ", ".join(f"{n} {k}" for n, k in c.most_common(8)) + "\n")
        f.write("\n## 埋め込んだ集まり（events.csv）\n\n")
        for e in events:
            for d, n in e.get("chosen", {}).items():
                f.write(f"- {d} {e['place']} {e['kind']} {e['start']}–{e['end']} {n}人\n")
        f.write("\n## 仮定値（PARAMS）\n\n")
        for k, v in PARAMS.items():
            f.write(f"- {k}: {v}\n")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
