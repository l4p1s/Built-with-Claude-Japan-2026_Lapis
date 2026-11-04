"""地域メッシュ（JIS X 0410）。検知の集計は 1/2 地域メッシュ（約 500m）で行う。"""


def mesh_code(lat: float, lon: float, level: int = 2) -> str:
    """緯度経度 → メッシュ番号。level 0=1次(80km) 1=3次(1km) 2=1/2(500m) 3=1/4(250m)"""
    p, a = divmod(lat * 60, 40)
    u, f = divmod(lon - 100, 1)
    q, b = divmod(a, 5)
    v, g = divmod(f * 60, 7.5)
    r, c = divmod(b * 60, 30)
    w, h = divmod(g * 60, 45)
    code = f"{int(p):02d}{int(u):02d}{int(q)}{int(v)}{int(r)}{int(w)}"
    hy, hx = 30.0, 45.0  # 3次メッシュの高さ・幅（秒）
    for _ in range(level - 1):
        hy, hx = hy / 2, hx / 2
        m = (1 if c < hy else 3) + (0 if h < hx else 1)
        c, h = c % hy, h % hx
        code += str(m)
    return code


def mesh_center(code: str) -> tuple[float, float]:
    """メッシュ番号 → 中心の緯度経度"""
    lat = int(code[0:2]) / 1.5 + int(code[4]) * 5 / 60 + int(code[6]) * 30 / 3600
    lon = int(code[2:4]) + 100 + int(code[5]) * 7.5 / 60 + int(code[7]) * 45 / 3600
    hy, hx = 30 / 3600, 45 / 3600
    for d in code[8:]:
        hy, hx = hy / 2, hx / 2
        m = int(d) - 1
        lat += (m // 2) * hy
        lon += (m % 2) * hx
    return lat + hy / 2, lon + hx / 2
