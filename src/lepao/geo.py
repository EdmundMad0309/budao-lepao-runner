"""坐标换算与地理计算（WGS-84 / GCJ-02 / BD-09）。"""
import math

A = 6378245.0
EE = 0.00669342162296594323
X_PI = math.pi * 3000.0 / 180.0
R_EARTH = 6371008.8


def out_of_china(lng, lat):
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(lng, lat):
    r = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    r += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    r += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    r += (160.0 * math.sin(lat / 12.0 * math.pi) + 320.0 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return r


def _transform_lng(lng, lat):
    r = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    r += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    r += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    r += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return r


def wgs84_to_gcj02(lng, lat):
    if out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng, lat, max_iter=8, eps=1e-11):
    """迭代反解，避免单次近似的米级残差。"""
    wlng, wlat = lng, lat
    for _ in range(max_iter):
        glng, glat = wgs84_to_gcj02(wlng, wlat)
        dlng, dlat = lng - glng, lat - glat
        wlng += dlng
        wlat += dlat
        if abs(dlng) < eps and abs(dlat) < eps:
            break
    return wlng, wlat


def gcj02_to_bd09(lng, lat):
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * X_PI)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * X_PI)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def bd09_to_gcj02(lng, lat):
    x = lng - 0.0065
    y = lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * X_PI)
    return z * math.cos(theta), z * math.sin(theta)


def wgs84_to_bd09(lng, lat):
    return gcj02_to_bd09(*wgs84_to_gcj02(lng, lat))


def bd09_to_wgs84(lng, lat):
    return gcj02_to_wgs84(*bd09_to_gcj02(lng, lat))


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(a)))


def meters_per_degree(lat):
    mlat = 111132.92 - 559.82 * math.cos(2 * math.radians(lat)) + 1.175 * math.cos(4 * math.radians(lat))
    mlon = 111412.84 * math.cos(math.radians(lat)) - 93.5 * math.cos(3 * math.radians(lat))
    return mlat, mlon


def offset_meters(lat, lon, dn, de):
    mlat, mlon = meters_per_degree(lat)
    return lat + dn / mlat, lon + de / mlon