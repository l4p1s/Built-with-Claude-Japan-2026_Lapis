"""GPS の「いつもと違う滞在」から人の集まりを見つける（CLAUDE.md §3 の方式）。

流れ: 点 → 10分以上の滞在 → その人のいつもの場所（自宅の周り 200m を含む）でない滞在だけ数える
     → 500m メッシュ × 30 分で人数を数える → その時間帯の街全体の量から期待値 → ポアソンで大きく超えたら候補。
純関数だけなので、CSV（scripts/detect_demo.py）でも DB でも同じ関数を使える。
"""

import math
from collections import defaultdict
from dataclasses import dataclass

from app.mesh import mesh_code

LAT_M = 110574.0
LON_M = 111320.0 * math.cos(math.radians(32.5))

# 判定の基準。仮置きし、答え合わせ（scripts/detect_demo.py）で調整する
STAY_RADIUS_M = 100  # この半径にとどまっていれば同じ滞在
STAY_MIN = 10  # 滞在と数える最短の長さ（分）
PLACE_RADIUS_M = 150  # いつもの場所の半径
HOME_RADIUS_M = 200  # 自宅の周りは対象外
USUAL_DAYS = 2  # 平常期にこの日数以上行った場所は「いつもの場所」
EXPLORER_NEW_PLACES = 6  # 平常期に 1 回だけ行った場所がこの数以上の人は探索型（重み 0.5）
WINDOW = 30  # 時間帯の長さ（分）
STEP = 10  # 時間帯をずらす幅（分）
MIN_OVERLAP = 10  # 滞在が時間帯にこれだけ重なれば数える
P_THRESHOLD = 1e-3
MIN_OBSERVED = 5
MIN_EXPECTED = 0.3  # 期待値の下限（平常時に誰もいないマスで p が 0 になるのを防ぐ）


def dist_m(lat1, lon1, lat2, lon2):
    return math.hypot((lat1 - lat2) * LAT_M, (lon1 - lon2) * LON_M)


@dataclass
class Stay:
    lat: float
    lon: float
    start: int  # 分（その日の 0:00 から）
    end: int


def stays_from_track(lats, lons, minutes=None, radius_m=STAY_RADIUS_M, min_minutes=STAY_MIN):
    """1 人 1 日の点列（1 分おき想定）→ 滞在の一覧。最初の点を基準にし、radius を出たら区切る"""
    n = len(lats)
    if minutes is None:
        minutes = range(n)
    out = []
    a_lat, a_lon, a_t, k = lats[0], lons[0], minutes[0], 1
    for i in range(1, n):
        if dist_m(lats[i], lons[i], a_lat, a_lon) <= radius_m:
            # 基準点を少しずつ平均に寄せる（GPS 誤差で端に基準が付くのを防ぐ）
            k += 1
            a_lat += (lats[i] - a_lat) / k
            a_lon += (lons[i] - a_lon) / k
            continue
        if minutes[i - 1] - a_t + 1 >= min_minutes:
            out.append(Stay(a_lat, a_lon, a_t, minutes[i - 1] + 1))
        a_lat, a_lon, a_t, k = lats[i], lons[i], minutes[i], 1
    if minutes[n - 1] - a_t + 1 >= min_minutes:
        out.append(Stay(a_lat, a_lon, a_t, minutes[n - 1] + 1))
    return out


def home_of(night_points):
    """平常日の深夜の位置（例: 3:00）の中央値を自宅とする"""
    lats = sorted(p[0] for p in night_points)
    lons = sorted(p[1] for p in night_points)
    return lats[len(lats) // 2], lons[len(lons) // 2]


def places_of(stays_by_day, radius_m=PLACE_RADIUS_M):
    """平常期の滞在を場所にまとめ、場所ごとに「行った日数」を返す: [(lat, lon, days)]"""
    places = []  # [lat, lon, n, days(set)]
    for day, stays in enumerate(stays_by_day):
        for s in stays:
            for pl in places:
                if dist_m(s.lat, s.lon, pl[0], pl[1]) <= radius_m:
                    pl[2] += 1
                    pl[0] += (s.lat - pl[0]) / pl[2]
                    pl[1] += (s.lon - pl[1]) / pl[2]
                    pl[3].add(day)
                    break
            else:
                places.append([s.lat, s.lon, 1, {day}])
    return [(pl[0], pl[1], len(pl[3])) for pl in places]


@dataclass
class Person:
    home: tuple
    usual: list  # [(lat, lon)]
    explorer: bool

    def is_usual(self, lat, lon):
        if self.home and dist_m(lat, lon, *self.home) <= HOME_RADIUS_M:
            return True
        return any(dist_m(lat, lon, *u) <= PLACE_RADIUS_M for u in self.usual)

    @property
    def weight(self):
        return 0.5 if self.explorer else 1.0


def learn_person(stays_by_day, night_points):
    """平常期の滞在と深夜の位置から、その人の自宅・いつもの場所・探索型かを決める。
    深夜の位置がなければ自宅は None（自宅の除外をしない）、平常期がなければいつもの場所は空"""
    home = home_of(night_points) if night_points else None
    places = places_of(stays_by_day)
    usual = [(la, lo) for la, lo, d in places if d >= USUAL_DAYS]
    once = sum(
        1
        for la, lo, d in places
        if d == 1 and (home is None or dist_m(la, lo, *home) > HOME_RADIUS_M)
    )
    return Person(home, usual, once >= EXPLORER_NEW_PLACES)


def poisson_sf(k, lam):
    """P(X >= k)。k 個未満の確率を足して 1 から引く"""
    if k <= 0:
        return 1.0
    term, cdf = math.exp(-lam), 0.0
    for i in range(k):
        cdf += term
        term *= lam / (i + 1)
    return max(0.0, 1.0 - cdf)


@dataclass
class Candidate:
    cell: str
    lat: float
    lon: float
    window_start: int
    window_end: int
    observed: float
    expected: float
    p_value: float
    uids: set
    windows: list = None  # 候補になった時間帯の一覧 [(start, end)]（検知までの時間を測る）


def baseline_share(cell_counts):
    """平常期の滞在のマス別の件数 → 割合（期待値の配分に使う）"""
    total = sum(cell_counts.values()) or 1
    return {c: n / total for c, n in cell_counts.items()}


def detect(unusual, share, window=WINDOW, step=STEP, t_start=0, t_end=1440):
    """unusual: [(uid, Stay, weight)] 「いつもと違う滞在」（時刻は分。t_start〜t_end の範囲を調べる）。
    share: baseline_share の結果（空なら期待値は下限だけ）。
    → 時間帯ごとに p < P_THRESHOLD かつ人数 >= MIN_OBSERVED のマスを候補として返す（時間帯が続けば 1 つにまとめる）"""
    by_cell = defaultdict(list)
    for uid, s, w in unusual:
        by_cell[mesh_code(s.lat, s.lon)].append((uid, s, w))
    # share が空（平常の滞在がまだない）なら期待値は下限 MIN_EXPECTED だけになり、
    # MIN_OBSERVED 人以上の「いつもと違う滞在」の集まりはそのまま候補になる（確認で消す前提）
    raw = []
    for w0 in range(t_start, t_end - window + 1, step):
        w1 = w0 + window
        per_cell = {}
        total = 0.0
        for cell, items in by_cell.items():
            got = [
                (uid, s, w)
                for uid, s, w in items
                if min(s.end, w1) - max(s.start, w0) >= MIN_OVERLAP
            ]
            if got:
                obs = sum(w for _, _, w in got)
                per_cell[cell] = (obs, got)
                total += obs
        for cell, (obs, got) in per_cell.items():
            lam = max(MIN_EXPECTED, total * share.get(cell, 0.0))
            k = math.ceil(obs)
            p = poisson_sf(k, lam)
            if p < P_THRESHOLD and k >= MIN_OBSERVED:
                lat = sum(s.lat for _, s, _ in got) / len(got)
                lon = sum(s.lon for _, s, _ in got) / len(got)
                raw.append(
                    Candidate(
                        cell, lat, lon, w0, w1, obs, lam, p, {uid for uid, _, _ in got}, [(w0, w1)]
                    )
                )
    # 同じマスで時間帯が続くものをまとめる（最初の時間帯で「検知」、人数は最大）
    merged = []
    for c in sorted(raw, key=lambda c: (c.cell, c.window_start)):
        last = merged[-1] if merged else None
        if last and last.cell == c.cell and c.window_start <= last.window_end:
            last.window_end = c.window_end
            last.uids |= c.uids
            last.windows += c.windows
            if c.observed > last.observed:
                last.observed, last.expected, last.p_value, last.lat, last.lon = (
                    c.observed,
                    c.expected,
                    c.p_value,
                    c.lat,
                    c.lon,
                )
        else:
            merged.append(c)
    return merged
