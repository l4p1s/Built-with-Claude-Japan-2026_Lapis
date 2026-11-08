"""合成データの性質を、小さい人数・日数で確かめる。実行には data/ の入力（BODIK・OSM の CSV）が要る。"""

import csv
import gzip
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "synth"))
from make_demo_data import MINUTES, PARAMS, dist_m, mesh_center, track  # noqa: E402


def test_mesh_center_250m():
    # 10桁 = 1/4 メッシュ（250m）。八代市役所（北緯32.507 東経130.602）は 4830-64-08 の中
    lat, lon = mesh_center("4830640831")
    assert dist_m(lat, lon, 32.5072, 130.6016) < 300
    # 1/2 メッシュの桁（9桁目）が 3→4 なら東隣: 経度だけ 22.5 秒ずれる
    lat2, lon2 = mesh_center("4830640841")
    assert lat2 == pytest.approx(lat) and (lon2 - lon) == pytest.approx(22.5 / 3600)


def test_track_no_teleport_and_stay_lengths():
    home = (32.507, 130.602)
    shop = (32.52, 130.61)
    speed = 30 * 1000 / 60  # 車
    lat, lon, stays, last = track(home, [(shop[0], shop[1], 600, 30, "shop")], speed, end_pos=home)
    assert len(lat) == MINUTES
    v = [dist_m(lat[i], lon[i], lat[i + 1], lon[i + 1]) for i in range(MINUTES - 1)]
    assert max(v) <= speed + 1e-6  # 1分で速度以上に飛ばない
    assert stays == [("shop", 600, 630)]
    assert dist_m(*last, *home) < 1  # 最後は家に帰る
    assert dist_m(lat[0], lon[0], *home) < 1 and dist_m(lat[-1], lon[-1], *home) < 1


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("out")
    subprocess.run(
        [
            sys.executable,
            "scripts/synth/make_demo_data.py",
            "--people",
            "300",
            "--normal-days",
            "3",
            "--seed",
            "7",
            "--out",
            str(d),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return d


def read_points(path):
    pts = defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pts[r["uid"]].append((r["time"], float(r["lat"]), float(r["lon"])))
    return pts


def test_every_person_every_minute(out):
    files = sorted(out.glob("points_*.csv.gz"))
    assert len(files) == 3 + 1 + 2
    pts = read_points(files[0])
    assert len(pts) == 300
    for uid, rows in pts.items():
        assert len(rows) == MINUTES
        assert rows[0][0].endswith("T00:00+09:00") and rows[-1][0].endswith("T23:59+09:00")


def test_speed_limit_with_noise(out):
    pts = read_points(sorted(out.glob("points_*.csv.gz"))[0])
    limit = PARAMS["speed_kmh"]["car"] * 1000 / 60 + 8 * PARAMS["gps_noise_m"]
    for rows in pts.values():
        for a, b in zip(rows, rows[1:]):
            assert dist_m(a[1], a[2], b[1], b[2]) <= limit


def stayed(pts, lat, lon, start, end, radius=100, minutes=10):
    """時間帯 start–end（分）に、場所の radius m 以内に minutes 分以上いた人数"""
    n = 0
    for rows in pts.values():
        c = sum(1 for t, la, lo in rows[start:end] if dist_m(la, lo, lat, lon) <= radius)
        n += c >= minutes
    return n


def test_events_are_visible_only_after_quake(out):
    events = list(csv.DictReader(open(out / "events.csv", encoding="utf-8")))
    assert events, "events.csv が空"
    files = {p.name: p for p in out.glob("points_*.csv.gz")}
    normal = read_points(sorted(files.values())[0])  # 最初の平常日
    by_date = defaultdict(list)
    for e in events:
        by_date[e["date"]].append(e)
    for d, evs in by_date.items():
        pts = read_points(files[f"points_{d}.csv.gz"])
        for e in evs:
            n = int(e["visitors"])
            if n < 5:
                continue  # 300人では周りに人が少ない場所もある
            h0, m0 = map(int, e["start"].split(":"))
            h1, m1 = map(int, e["end"].split(":"))
            s, t = h0 * 60 + m0, h1 * 60 + m1 + 30
            got = stayed(pts, float(e["lat"]), float(e["lon"]), s, t)
            assert abs(got - n) <= max(5, n * 0.2), (e["place"], got, n)
            # 平常日の同じ場所・同じ時間帯にはその集まりがない（店の行列は店なので平常日にも人がいる）
            if e["kind"] != "shop":
                assert stayed(normal, float(e["lat"]), float(e["lon"]), s, t) < max(2, n * 0.3)


def weekday_file(out):
    from datetime import date

    for p in sorted(out.glob("points_*.csv.gz")):
        if date.fromisoformat(p.stem[7:17]).weekday() < 5:
            return p


def test_normal_day_unusual_stays_are_few(out):
    """平常の平日、家・職場・いつもの店以外での10分以上の滞在は少ない（探索型だけが作る）"""
    people = {r["uid"]: r for r in csv.DictReader(open(out / "people.csv", encoding="utf-8"))}
    places = {r["place_id"]: r for r in csv.DictReader(open(out / "places.csv", encoding="utf-8"))}
    pts = read_points(weekday_file(out))
    unusual = total = 0
    for uid, rows in pts.items():
        p = people[uid]
        usual = [(float(p["home_lat"]), float(p["home_lon"]))]
        for pid in ([p["work"]] if p["work"] else []) + p["usual_shop_ids"].split():
            usual.append((float(places[pid]["lat"]), float(places[pid]["lon"])))
        run, prev = 0, None
        for _, la, lo in rows + [(None, 0.0, 0.0)]:
            if prev and dist_m(la, lo, *prev) < 60:
                run += 1
                continue
            if run >= 10 and prev:
                total += 1
                unusual += not any(dist_m(*prev, *u) < 150 for u in usual)
            run, prev = 0, (la, lo)
    assert total > 0 and unusual / total < 0.15, (unusual, total)


def test_evacuation_rate_and_water_zone(out):
    """地震当日の夜に家以外で寝る人は 5〜15%（避難所 3% + 車中泊 6% の前後）。給水に来た人の家は断水区域の中"""
    from make_demo_data import CENTER

    events = list(csv.DictReader(open(out / "events.csv", encoding="utf-8")))
    d0 = min(e["date"] for e in events if e["day"] == "D0")  # 地震当日
    pts = read_points(out / f"points_{d0}.csv.gz")
    people = {r["uid"]: r for r in csv.DictReader(open(out / "people.csv", encoding="utf-8"))}
    away = sum(
        dist_m(rows[-1][1], rows[-1][2], float(people[u]["home_lat"]), float(people[u]["home_lon"]))
        > 200
        for u, rows in pts.items()
    )
    assert 0.05 * len(pts) <= away <= 0.15 * len(pts), away
    water = [e for e in events if e["kind"] == "water"]
    assert water
    for e in water:
        assert dist_m(float(e["lat"]), float(e["lon"]), *CENTER) <= PARAMS["water_radius_m"] + 1500


# ---------- 回答の型（scripts/synth/survey_templates.py、--templates） ----------
ANSWERS = [
    {
        "answer": {
            "name": "A",
            "home": [32.52, 130.58],
            "days": [
                {
                    "label": "いつもの平日",
                    "date": None,
                    "stays": [
                        {
                            "lat": 32.52,
                            "lon": 130.58,
                            "from": "00:00",
                            "to": "07:30",
                            "type": "home",
                            "usual": True,
                        },
                        # 既定の「15分」を直し忘れた職場 → 帰宅の 15 分前まで延びる
                        {
                            "lat": 32.53,
                            "lon": 130.55,
                            "from": "07:45",
                            "to": "08:00",
                            "type": "work",
                            "usual": True,
                            "mode": "car",
                        },
                        {
                            "lat": 32.52,
                            "lon": 130.58,
                            "from": "17:00",
                            "to": "00:00",
                            "type": "home",
                            "usual": True,
                        },
                    ],
                },
                {"label": "いつもの休日", "date": None, "stays": []},
                {
                    "label": "地震の日",
                    "date": "2026-07-28",
                    "copied_from": "いつもの平日",
                    "stays": [],
                },
                {
                    "label": "地震の後の日",
                    "date": "2026-07-30",
                    "stays": [
                        # 重なり: 友達の家 13:00–14:00 のあと家が 13:30 から → 13:30 で詰める
                        {
                            "lat": 32.5245,
                            "lon": 130.5833,
                            "from": "13:00",
                            "to": "14:00",
                            "type": "friend",
                            "usual": False,
                        },
                        {
                            "lat": 32.52,
                            "lon": 130.58,
                            "from": "13:30",
                            "to": "00:00",
                            "type": "home",
                            "usual": True,
                        },
                        {
                            "lat": 32.53,
                            "lon": 130.60,
                            "from": "17:00",
                            "to": "17:15",
                            "type": "water",
                            "usual": False,
                        },
                    ],
                },
            ],
        }
    },
    {"answer": {"name": "動作確認（消してください）", "home": [32.52, 130.58], "days": []}},
    {
        "answer": {"name": "B", "home": [32.507, 130.602], "days": []}
    },  # 地図の初期位置のまま → 家が分からない
]


def test_survey_templates_cleaning():
    from datetime import date

    from survey_templates import templates_from_answers

    t = templates_from_answers(ANSWERS, date(2026, 7, 28))
    assert [p["id"] for p in t["people"]] == ["s01"]  # 動作確認と初期位置の家は除く
    p = t["people"][0]
    assert p["car"]
    assert p["days"]["weekday"] == [
        {"type": "work", "from": 465, "to": 1005, "usual": True, "band": 2, "mode": "car"}
    ]
    assert p["days"]["D0"] == []  # 写しただけで中身が空なら空のまま（生成側で平日に戻す）
    (after,) = p["days"]["after"]
    assert [(s["type"], s["from"], s["to"], s["band"]) for s in after] == [
        ("friend", 780, 810, 1),
        ("water", 1020, 1035, 2),
    ]
    assert t["aid_stay_min"] == [15]


@pytest.fixture(scope="module")
def out_tpl(tmp_path_factory):
    import json

    d = tmp_path_factory.mktemp("out_tpl")
    tpl = d / "templates.json"
    tpl.write_text(
        json.dumps(
            {
                "source": "test",
                "quake_date": "2026-07-28",
                # 120 は台本の 90 分の集まりより長い（集まりの長さに収める）
                "aid_stay_min": [15, 120],
                "people": [
                    {
                        "id": "s01",
                        "car": True,
                        "days": {
                            "weekday": [
                                {
                                    "type": "work",
                                    "from": 480,
                                    "to": 1020,
                                    "usual": True,
                                    "band": 2,
                                    "mode": None,
                                }
                            ],
                            "weekend": [
                                {
                                    "type": "shop",
                                    "from": 600,
                                    "to": 660,
                                    "usual": True,
                                    "band": 1,
                                    "mode": None,
                                }
                            ],
                            "D0": [
                                {
                                    "type": "work",
                                    "from": 480,
                                    "to": 1020,
                                    "usual": True,
                                    "band": 2,
                                    "mode": None,
                                }
                            ],
                            "after": [
                                [
                                    {
                                        "type": "friend",
                                        "from": 780,
                                        "to": 840,
                                        "usual": False,
                                        "band": 1,
                                        "mode": None,
                                    }
                                ]
                            ],
                        },
                    },
                    {
                        "id": "s02",
                        "car": False,
                        "days": {"weekday": [], "weekend": [], "D0": [], "after": [[]]},
                    },
                ],
            }
        )
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/synth/make_demo_data.py",
            "--people",
            "100",
            "--normal-days",
            "2",
            "--seed",
            "5",
            "--templates",
            str(tpl),
            "--out",
            str(d),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return d


def test_templates_mode_follows_templates(out_tpl):
    """型どおり: 職場のある型の人は worker で 9 時間いる、ない型の人は家にいる。集まりは台本どおり埋め込まれる"""
    people = list(csv.DictReader(open(out_tpl / "people.csv", encoding="utf-8")))
    assert {p["role"] for p in people} == {"worker", "home"} and all(
        p["explorer"] == "0" for p in people
    )
    places = {
        r["place_id"]: r for r in csv.DictReader(open(out_tpl / "places.csv", encoding="utf-8"))
    }
    pts = read_points(weekday_file(out_tpl))
    for p in people:
        rows = pts[p["uid"]]
        if p["work"]:
            w = places[p["work"]]
            at_work = sum(
                dist_m(la, lo, float(w["lat"]), float(w["lon"])) < 100 for _, la, lo in rows
            )
            assert 480 <= at_work <= 600, (p["uid"], at_work)  # 8:00–17:00、予定ごと ±60 分のずらし
        else:
            assert all(
                dist_m(la, lo, float(p["home_lat"]), float(p["home_lon"])) < 100
                for _, la, lo in rows
            )
    events = list(csv.DictReader(open(out_tpl / "events.csv", encoding="utf-8")))
    assert any(int(e["visitors"]) >= 5 for e in events)
