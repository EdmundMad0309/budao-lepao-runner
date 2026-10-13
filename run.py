#!/usr/bin/env python3
"""步道乐跑 · MuMu 后台代跑引擎入口。

用法:
  python run.py --init                 生成默认 config.json
  python run.py --check                自检：坐标换算 / 跑道 / MuMu 通道
  python run.py --set 31.4916 120.2790 单点下发测试（WGS-84）
  python run.py --preview              生成跑道预览图 track_preview.html
  python run.py --dry-run              只算轨迹不下发
  python run.py                        按 config.json 正式开跑
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from lepao.geo import haversine, wgs84_to_bd09, bd09_to_wgs84  # noqa: E402
from lepao.mumu import MuMuLocationClient  # noqa: E402
from lepao.sim import RunConfig, RunSimulator  # noqa: E402
from lepao.track import Track  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")

DEFAULT_TRACK = [
    [31.491600, 120.279000],
    [31.492900, 120.279000],
    [31.493400, 120.280400],
    [31.492900, 120.281800],
    [31.491600, 120.281800],
    [31.491100, 120.280400],
]


def default_config():
    return {
        "mumu": {
            "vm_index": 0,
            "gateway_port": None,
            "manager_path": r"C:\Program Files\NetEase\MuMu\nx_main\MuMuManager.exe",
            "vm_config_path": r"C:\Program Files\NetEase\MuMu\vms\MuMuPlayer-12.0-0\configs\vm_config.json",
            "prefer_http": True,
        },
        "run": {
            "speed_kmh": 8.0,
            "speed_jitter_pct": 5.0,
            "update_hz": 1.0,
            "gps_noise_m": 1.5,
            "laps": 5,
            "start_delay_s": 3.0,
            "corner_radius_m": 6.0,
            "loop_forever": False,
            "log_every_s": 10.0,
            "kick_every_s": 0.0,
        },
        "track": {
            "waypoints_wgs84": DEFAULT_TRACK,
        },
        "_note": "waypoints_wgs84 为手机 GPS 坐标系(WGS-84) 的顶点，按顺序连成闭合跑道；顶点数可任意。",
    }


def load_config(path):
    if not os.path.exists(path):
        cfg = default_config()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        print("已生成默认配置:", path)
        return cfg
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def make_client(cfg):
    m = cfg.get("mumu", {})
    return MuMuLocationClient(
        vm_index=int(m.get("vm_index", 0)),
        gateway_port=m.get("gateway_port"),
        manager_path=m.get("manager_path"),
        vm_config_path=m.get("vm_config_path"),
        prefer_http=bool(m.get("prefer_http", True)),
    )


def cmd_check(cfg):
    print("== 坐标换算自检 ==")
    lat, lon = 31.493651, 120.281542
    bd = wgs84_to_bd09(lon, lat)
    back = bd09_to_wgs84(*bd)
    drift = haversine(lat, lon, back[1], back[0])
    print("  WGS84 -> BD09 -> WGS84 漂移: %.3f m" % drift)
    print("== 跑道自检 ==")
    track = Track(cfg["track"]["waypoints_wgs84"], corner_radius_m=cfg["run"].get("corner_radius_m", 6.0))
    print("  顶点数 %d -> 路径点 %d, 周长 %.1f m" % (len(track.raw), len(track.path), track.length))
    print("== MuMu 通道自检 ==")
    client = make_client(cfg)
    print("  网关端口:", client.gateway_port)
    try:
        print("  下发返回:", str(client.set_wgs84(lat, lon)).strip()[:120], "通道:", client.last_transport)
        print("  OK")
    except Exception as exc:  # noqa: BLE001
        print("  失败:", exc)
        return 1
    return 0


def cmd_preview(cfg):
    track = Track(cfg["track"]["waypoints_wgs84"], corner_radius_m=cfg["run"].get("corner_radius_m", 6.0))
    lats = [p[0] for p in track.path]
    lons = [p[1] for p in track.path]
    la0, la1, lo0, lo1 = min(lats), max(lats), min(lons), max(lons)
    w = 900.0
    h = max(300.0, w * (la1 - la0) / max(1e-9, lo1 - lo0))
    pts = " ".join("%.2f,%.2f" % ((lo - lo0) / max(1e-9, lo1 - lo0) * w,
                                   h - (la - la0) / max(1e-9, la1 - la0) * h) for la, lo in track.path)
    speed = cfg["run"].get("speed_kmh", 8.0)
    laps = cfg["run"].get("laps", 5)
    html = """<!doctype html><meta charset="utf-8"><title>跑道预览</title>
<style>body{background:#111;color:#eee;font-family:Consolas,monospace;margin:20px}
svg{background:#181818;border:1px solid #333}</style>
<h2>跑道预览 (WGS-84)</h2>
<p>周长 %.1f m · 目标 %.0f 圈 = %.2f km · 速度 %.1f km/h · 预计 %s</p>
<svg width="%d" height="%d"><polyline fill="none" stroke="#39d353" stroke-width="3" points="%s"/>
%s</svg>""" % (
        track.length, laps, track.length * laps / 1000.0, speed,
        time.strftime("%H:%M:%S", time.gmtime(track.length * laps / (speed / 3.6))),
        int(w), int(h), pts,
        "".join('<circle cx="%.2f" cy="%.2f" r="5" fill="#f0c040"/>' % (
            (lo - lo0) / max(1e-9, lo1 - lo0) * w, h - (la - la0) / max(1e-9, la1 - la0) * h)
            for la, lo in cfg["track"]["waypoints_wgs84"]))
    out = os.path.join(HERE, "track_preview.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("已生成:", out)


def main():
    ap = argparse.ArgumentParser(description="步道乐跑 MuMu 后台代跑")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--init", action="store_true", help="生成默认 config.json")
    ap.add_argument("--check", action="store_true", help="自检")
    ap.add_argument("--preview", action="store_true", help="生成跑道预览")
    ap.add_argument("--set", nargs=2, type=float, metavar=("LAT", "LON"), help="单点下发(WGS-84)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--laps", type=float)
    ap.add_argument("--speed", type=float)
    ap.add_argument("--hz", type=float)
    ap.add_argument("--forever", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.init:
        print("配置就绪:", args.config)
        return 0
    if args.check:
        return cmd_check(cfg)
    if args.preview:
        cmd_preview(cfg)
        return 0
    if args.set:
        client = make_client(cfg)
        client.set_wgs84(args.set[0], args.set[1])
        print("已下发 WGS-84 (%.6f, %.6f) 通道=%s" % (args.set[0], args.set[1], client.last_transport))
        return 0

    if args.laps is not None:
        cfg["run"]["laps"] = args.laps
    if args.speed is not None:
        cfg["run"]["speed_kmh"] = args.speed
    if args.hz is not None:
        cfg["run"]["update_hz"] = args.hz
    if args.forever:
        cfg["run"]["loop_forever"] = True

    track = Track(cfg["track"]["waypoints_wgs84"], corner_radius_m=cfg["run"].get("corner_radius_m", 6.0))
    run_cfg = RunConfig(
        speed_kmh=cfg["run"]["speed_kmh"],
        speed_jitter_pct=cfg["run"]["speed_jitter_pct"],
        update_hz=cfg["run"]["update_hz"],
        gps_noise_m=cfg["run"]["gps_noise_m"],
        laps=cfg["run"]["laps"],
        start_delay_s=cfg["run"]["start_delay_s"],
        corner_radius_m=cfg["run"]["corner_radius_m"],
        loop_forever=cfg["run"].get("loop_forever", False),
        log_every_s=cfg["run"].get("log_every_s", 10.0),
        kick_every_s=cfg["run"].get("kick_every_s", 0.0),
    )

    logdir = os.path.join(HERE, "logs")
    os.makedirs(logdir, exist_ok=True)
    logfile = os.path.join(logdir, time.strftime("run-%Y%m%d-%H%M%S.log"))
    fh = open(logfile, "a", encoding="utf-8")

    def logger(msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()

    if args.dry_run:
        client = type("NullClient", (), {"last_transport": "dry-run", "set_wgs84": lambda self, a, b: None})()
    else:
        client = make_client(cfg)

    sim = RunSimulator(track, client, run_cfg, logger=logger)
    logger("日志文件: %s" % logfile)
    try:
        sim.run()
    except KeyboardInterrupt:
        logger("已手动中断，累计 %.3f km" % (sim.stats.distance_m / 1000.0))
    finally:
        fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())