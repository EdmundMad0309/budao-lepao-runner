#!/usr/bin/env python3
"""端到端验证：证明我们下发的坐标会变成系统 GPS provider 的"非 mock"定位。

流程:
  1. 推入探针 probe.jar（如缺失）
  2. 通过 MuMu 网关下发目标 WGS-84 坐标
  3. 重启定位引擎（关/开位置总开关），让系统抓取新的虚拟位置
  4. 在设备上运行探针，打印 App 视角能拿到的 Location（含 isMock 标记）
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

from lepao.geo import haversine, wgs84_to_bd09  # noqa: E402
from lepao.mumu import MuMuLocationClient  # noqa: E402

ADB = r"C:\Program Files\NetEase\MuMu\nx_device\12.0\shell\adb.exe"
PROBE_JAR = os.path.join(HERE, "tools", "probe", "probe.jar")


def sh(args, timeout=60):
    return subprocess.run([ADB] + args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=31.493651)
    ap.add_argument("--lon", type=float, default=120.281542)
    ap.add_argument("--skip-restart", action="store_true")
    ap.add_argument("--keep", action="store_true", help="验证后不回填原位置")
    args = ap.parse_args()

    if not os.path.exists(PROBE_JAR):
        print("缺少探针:", PROBE_JAR)
        return 1

    r = sh(["shell", "ls", "/data/local/tmp/probe.jar"])
    if "probe.jar" not in r.stdout:
        print("推送探针…")
        sh(["push", PROBE_JAR, "/data/local/tmp/probe.jar"])
        sh(["shell", "chmod", "644", "/data/local/tmp/probe.jar"])

    client = MuMuLocationClient()
    lon_bd, lat_bd = wgs84_to_bd09(args.lon, args.lat)
    print("目标 WGS-84 : %.7f, %.7f" % (args.lat, args.lon))
    print("下发 BD-09  : %.7f, %.7f" % (lat_bd, lon_bd))
    print("网关端口    :", client.gateway_port)
    print("下发结果    :", str(client.set_wgs84(args.lat, args.lon)).strip()[:120])
    time.sleep(1.5)

    if not args.skip_restart:
        print("重启定位引擎（关/开位置总开关）…")
        sh(["shell", "cmd location set-location-enabled false"])
        time.sleep(1.5)
        sh(["shell", "cmd location set-location-enabled true"])
        time.sleep(2.0)

    print("运行设备探针…")
    r = sh(["shell", "timeout 22 env CLASSPATH=/data/local/tmp/probe.jar app_process /system/bin LProbe"], timeout=90)
    lines = [l for l in (r.stdout + r.stderr).splitlines() if "CALLBACK[item]" in l]
    if not lines:
        print("未取到回点。原始输出:")
        print(r.stdout[-800:])
        return 2
    print("\n=== App 视角收到的 Location ===")
    for l in lines:
        print(l.strip())
    last = lines[-1]
    try:
        lat = float(last.split("lat=")[1].split()[0])
        lon = float(last.split("lon=")[1].split()[0])
        is_mock = last.split("isMock=")[1].split()[0]
        dist = haversine(args.lat, args.lon, lat, lon)
        print("\n与目标点偏差: %.1f m    isMock=%s" % (dist, is_mock))
        if is_mock == "false" and dist < 60:
            print("结论: 通过 —— 高德 setMockEnable(false) 的校验不会拦截该定位。")
        else:
            print("结论: 需要检查（偏差过大或仍为 mock）。")
    except Exception as exc:  # noqa: BLE001
        print("解析失败:", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())