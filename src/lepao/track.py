"""跑道建模：N 个顶点 -> 闭合路径 -> 按里程插值。"""
import math

from .geo import haversine, meters_per_degree


class Track:
    def __init__(self, waypoints, closed=True, corner_radius_m=6.0, corner_segments=6):
        pts = [(float(p[0]), float(p[1])) for p in waypoints]
        if len(pts) < 2:
            raise ValueError("至少需要 2 个顶点")
        if closed and pts[0] != pts[-1]:
            pts.append(pts[0])
        self.raw = pts
        self.closed = closed
        path = self._round_corners(pts, corner_radius_m, corner_segments) if corner_radius_m > 0 else pts
        self.path = self._dedupe(path)
        self.cum = [0.0]
        for i in range(1, len(self.path)):
            a, b = self.path[i - 1], self.path[i]
            self.cum.append(self.cum[-1] + haversine(a[0], a[1], b[0], b[1]))
        self.length = self.cum[-1]
        if self.length <= 0:
            raise ValueError("路径长度为 0")

    # ---------- geometry helpers ----------
    @staticmethod
    def _dedupe(pts, eps=1e-9):
        out = [pts[0]]
        for p in pts[1:]:
            if abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
                out.append(p)
        return out

    @staticmethod
    def _round_corners(pts, radius_m, segments):
        """用二次贝塞尔把每个拐角抹圆，跑出来更像真实轨迹。"""
        if len(pts) < 4:
            return pts
        core = pts[:-1] if pts[0] == pts[-1] else pts
        n = len(core)
        out = []
        for i in range(n):
            p_prev = core[(i - 1) % n]
            p_cur = core[i]
            p_next = core[(i + 1) % n]
            d1 = haversine(p_cur[0], p_cur[1], p_prev[0], p_prev[1])
            d2 = haversine(p_cur[0], p_cur[1], p_next[0], p_next[1])
            r = min(radius_m, d1 / 2.5 if d1 > 0 else radius_m, d2 / 2.5 if d2 > 0 else radius_m)
            if r <= 0.5:
                out.append(p_cur)
                continue
            a = Track._toward(p_cur, p_prev, r, d1)
            b = Track._toward(p_cur, p_next, r, d2)
            for k in range(segments + 1):
                t = k / segments
                lat = (1 - t) ** 2 * a[0] + 2 * (1 - t) * t * p_cur[0] + t * t * b[0]
                lon = (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * p_cur[1] + t * t * b[1]
                out.append((lat, lon))
        out.append(out[0])
        return out

    @staticmethod
    def _toward(src, dst, dist, total):
        if total <= 0:
            return src
        t = min(1.0, dist / total)
        return (src[0] + (dst[0] - src[0]) * t, src[1] + (dst[1] - src[1]) * t)

    # ---------- sampling ----------
    def position_at(self, distance_m):
        """按里程取点，自动循环。"""
        d = distance_m % self.length
        lo, hi = 0, len(self.cum) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= d:
                lo = mid
            else:
                hi = mid
        seg = self.cum[hi] - self.cum[lo]
        t = 0.0 if seg <= 0 else (d - self.cum[lo]) / seg
        a, b = self.path[lo], self.path[hi]
        return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t

    def bearing_at(self, distance_m, probe_m=6.0):
        a = self.position_at(distance_m)
        b = self.position_at(distance_m + probe_m)
        mlat, mlon = meters_per_degree(a[0])
        dn = (b[0] - a[0]) * mlat
        de = (b[1] - a[1]) * mlon
        if dn == 0 and de == 0:
            return 0.0
        return (math.degrees(math.atan2(de, dn)) + 360.0) % 360.0

    def bbox(self):
        lats = [p[0] for p in self.path]
        lons = [p[1] for p in self.path]
        return min(lats), min(lons), max(lats), max(lons)