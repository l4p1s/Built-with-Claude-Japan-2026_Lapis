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
