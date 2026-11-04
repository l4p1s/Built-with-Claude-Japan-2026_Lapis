"""検知（app/detect.py）の純関数の確認"""

from app.detect import (
    Person,
    Stay,
    baseline_share,
    detect,
    learn_person,
    poisson_sf,
    stays_from_track,
)
from app.mesh import mesh_center, mesh_code


def test_mesh_roundtrip():
    code = mesh_code(32.5072, 130.6016)
    assert len(code) == 9 and code.startswith("4830")
    lat, lon = mesh_center(code)
    assert abs(lat - 32.5072) < 0.003 and abs(lon - 130.6016) < 0.004  # 500m マスの中
    assert mesh_code(lat, lon) == code


def test_poisson_sf():
    assert poisson_sf(0, 2.0) == 1.0
    assert abs(poisson_sf(1, 2.0) - (1 - 2.718281828**-2)) < 1e-9
    assert poisson_sf(5, 0.3) < 1e-4 and poisson_sf(5, 5.0) > 0.4


def test_stays_from_track():
    # 家に 600 分 → 移動 20 分 → 店に 30 分 → 移動 → 家
    home, shop = (32.50, 130.60), (32.51, 130.60)
    lats = (
        [home[0]] * 600
        + [home[0] + (shop[0] - home[0]) * i / 20 for i in range(20)]
        + [shop[0]] * 30
    )
    lons = [home[1]] * 650
    lats += [shop[0] - (shop[0] - home[0]) * i / 20 for i in range(20)] + [home[0]] * (1440 - 670)
    lons += [home[1]] * (1440 - 650)
    st = stays_from_track(lats, lons)
    want = [(0, 600), (620, 650), (670, 1440)]
    assert len(st) == 3 and all(
        abs(s.start - a) <= 3 and abs(s.end - b) <= 3 for s, (a, b) in zip(st, want)
    )  # 100m 以内の最初の数点は滞在に含まれるので数分ずれる
    assert abs(st[1].lat - shop[0]) < 1e-4  # 約 10m


def test_learn_person_and_usual():
    home, work, once = (32.50, 130.60), (32.52, 130.62), (32.55, 130.65)
    days = [[Stay(*work, 540, 1020)] for _ in range(5)] + [[Stay(*once, 600, 700)]]
    p = learn_person(days, [home] * 6)
    assert p.home == home and not p.explorer
    assert p.is_usual(*work) and p.is_usual(home[0] + 0.001, home[1]) and not p.is_usual(*once)


def test_detect_finds_gathering_and_ignores_scatter():
    share = baseline_share({f"4830640{i:02d}": 10 for i in range(60)})  # 平常時は 60 マスに散らばる
    spot = (32.5072, 130.6016)
    unusual = [(f"u{i}", Stay(*spot, 600, 640), 1.0) for i in range(12)]
    # 別のマスに 1 人ずつ散らばった滞在は候補にならない
    unusual += [(f"v{i}", Stay(32.48 + i * 0.006, 130.58, 600, 640), 1.0) for i in range(4)]
    cands = detect(unusual, share)
    assert len(cands) == 1
    c = cands[0]
    assert c.cell == mesh_code(*spot) and c.observed == 12 and c.window_start <= 600 < c.window_end
    assert len(c.uids) == 12


def test_explorer_weight():
    p = Person((32.5, 130.6), [], True)
    assert p.weight == 0.5
