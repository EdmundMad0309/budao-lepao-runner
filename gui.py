#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""步道乐跑 · MuMu 后台代跑助手（图形界面）。"""
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

def app_dir():
    """打包成 exe 后以 exe 所在目录为工作目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = app_dir()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from lepao.geo import bd09_to_wgs84, gcj02_to_wgs84  # noqa: E402
from lepao.mumu import MuMuLocationClient, discover_mumu  # noqa: E402
from lepao.sensors import cadence_for_speed  # noqa: E402
from lepao.sim import RunConfig, RunSimulator  # noqa: E402
from lepao.track import Track  # noqa: E402

HERE = APP_DIR
CFG_PATH = os.path.join(HERE, "config.json")
_MUTEX = None

def _appdata_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "LePaoRunner")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def portable_config_path():
    return os.path.join(HERE, "config.json")


def config_path():
    """正式存档固定放 %APPDATA%\\LePaoRunner\\config.json。

    这样重新打包 exe、换目录、甚至删掉 dist 都不会丢配置；
    如果 exe 同目录存在 config.json，则把它当作便携覆盖（优先读取，保存时同步更新）。
    """
    return os.path.join(_appdata_dir(), "config.json")



def acquire_single_instance():
    """单实例锁：防止同时开两份导致步数脉冲翻倍（曾因此把 App 摇白屏）。"""
    global _MUTEX
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        _MUTEX = k32.CreateMutexW(None, False, "Local\\\\LePaoRunner_SingleInstance_v2")
        ERROR_ALREADY_EXISTS = 183
        if k32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        return True
    except Exception:
        return True

# 默认 6 点（GCJ-02，高德/腾讯地图直接复制即可）
DEFAULT_POINTS_GCJ = [
    [31.489662, 120.283575],
    [31.490962, 120.283575],
    [31.491464, 120.284976],
    [31.490965, 120.286376],
    [31.489665, 120.286376],
    [31.489163, 120.284976],
]

SYS_OPTIONS = {
    "高德/腾讯地图 (GCJ-02)": "gcj02",
    "百度地图 (BD-09)": "bd09",
    "手机GPS/谷歌地球 (WGS-84)": "wgs84",
}


def to_wgs84(lat, lon, kind):
    if kind == "gcj02":
        return gcj02_to_wgs84(lon, lat)
    if kind == "bd09":
        return bd09_to_wgs84(lon, lat)
    return lon, lat


class App:
    def __init__(self, root):
        self.root = root
        self.sim = None
        self.worker = None
        self.stop_flag = threading.Event()
        self.msgq = queue.Queue()
        self.disc = discover_mumu()
        self.client = None
        self._build()
        self.root.after(120, self._drain)
        self._log("环境探测：")
        self._log("  MuMu 目录   : %s" % (self.disc.get("root") or "未找到"))
        self._log("  MuMuManager : %s" % (self.disc.get("manager") or "未找到"))
        self._log("  adb         : %s" % (self.disc.get("adb") or "未找到"))
        self._log("  网关端口    : %s" % self.disc.get("gateway_port"))
        if not self.disc.get("manager"):
            self._log("警告：没找到 MuMuManager.exe，请确认已安装 MuMu 模拟器。")

    # ---------- UI ----------
    def _build(self):
        r = self.root
        r.title("步道乐跑 · MuMu 后台代跑助手  v2.0")
        r.geometry("680x880")
        r.minsize(620, 800)

        pad = dict(padx=10, pady=4)
        head = ttk.Label(r, text="跑道顶点（按顺序连成闭合跑道，顺时针或逆时针都行）",
                         font=("Microsoft YaHei UI", 10, "bold"))
        head.pack(anchor="w", **pad)

        frm = ttk.Frame(r)
        frm.pack(fill="x", **pad)
        ttk.Label(frm, text="纬度", width=8, anchor="center").grid(row=0, column=1)
        ttk.Label(frm, text="经度", width=16, anchor="center").grid(row=0, column=2)
        self.pt_lat, self.pt_lon = [], []
        for i in range(6):
            ttk.Label(frm, text="点 %d" % (i + 1), width=6).grid(row=i + 1, column=0, pady=2)
            e1 = ttk.Entry(frm, width=16)
            e2 = ttk.Entry(frm, width=20)
            e1.insert(0, "%.6f" % DEFAULT_POINTS_GCJ[i][0])
            e2.insert(0, "%.6f" % DEFAULT_POINTS_GCJ[i][1])
            e1.grid(row=i + 1, column=1, padx=6, pady=2)
            e2.grid(row=i + 1, column=2, padx=6, pady=2)
            self.pt_lat.append(e1)
            self.pt_lon.append(e2)

        cs = ttk.Frame(r)
        cs.pack(fill="x", **pad)
        ttk.Label(cs, text="坐标系").pack(side="left")
        self.csys = ttk.Combobox(cs, values=list(SYS_OPTIONS.keys()), state="readonly", width=26)
        self.csys.current(0)
        self.csys.pack(side="left", padx=8)
        ttk.Label(cs, text="（按你复制坐标的来源选）").pack(side="left")

        opt = ttk.LabelFrame(r, text="跑步参数")
        opt.pack(fill="x", **pad)
        g = ttk.Frame(opt)
        g.pack(fill="x", padx=8, pady=6)

        ttk.Label(g, text="跑步速度 km/h").grid(row=0, column=0, sticky="w", pady=4)
        self.var_speed = tk.StringVar(value="8.0")
        ttk.Entry(g, textvariable=self.var_speed, width=10).grid(row=0, column=1, padx=8)

        ttk.Label(g, text="跑几圈").grid(row=0, column=2, sticky="w", padx=(16, 0))
        self.var_laps = tk.StringVar(value="5")
        ttk.Entry(g, textvariable=self.var_laps, width=8).grid(row=0, column=3, padx=8)

        ttk.Label(g, text="下发频率 Hz").grid(row=1, column=0, sticky="w", pady=4)
        self.var_hz = tk.StringVar(value="1.0")
        ttk.Entry(g, textvariable=self.var_hz, width=10).grid(row=1, column=1, padx=8)

        ttk.Label(g, text="开始前等待 秒").grid(row=1, column=2, sticky="w", padx=(16, 0))
        self.var_delay = tk.StringVar(value="5")
        ttk.Entry(g, textvariable=self.var_delay, width=8).grid(row=1, column=3, padx=8)

        ttk.Label(g, text="步频 步/分（0=自动,上限84）").grid(row=2, column=0, sticky="w", pady=4)
        self.var_cad = tk.StringVar(value="0")
        ttk.Entry(g, textvariable=self.var_cad, width=10).grid(row=2, column=1, padx=8)

        self.var_steps = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text="模拟步数/运动状态（强烈建议开启）",
                        variable=self.var_steps).grid(row=2, column=2, columnspan=2, sticky="w", padx=(16, 0))
        self.var_hide = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text="后台运行：把模拟器窗口移到屏幕外",
                        variable=self.var_hide).grid(row=3, column=2, columnspan=2, sticky="w", padx=(16, 0))
        self.var_kick = tk.BooleanVar(value=False)
        ttk.Checkbutton(g, text="保底刷新（轨迹不动时才勾）", variable=self.var_kick).grid(row=4, column=2, columnspan=2, sticky="w", padx=(16, 0))

        btns = ttk.Frame(r)
        btns.pack(fill="x", **pad)
        self.btn_start = ttk.Button(btns, text="▶  开 始 代 跑", command=self.on_start)
        self.btn_start.pack(side="left", ipadx=24, ipady=8)
        self.btn_stop = ttk.Button(btns, text="■  停止", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=10, ipadx=18, ipady=8)
        ttk.Button(btns, text="自检", command=self.on_check).pack(side="left", padx=8, ipady=8)
        ttk.Button(btns, text="保存默认", command=self.on_save).pack(side="left", padx=8, ipady=8)

        btns2 = ttk.Frame(r)
        btns2.pack(fill="x", **pad)
        ttk.Button(btns2, text="隐藏模拟器（后台）", command=self.on_hide_mumu).pack(side="left", ipadx=10, ipady=5)
        ttk.Button(btns2, text="显示模拟器（看进度）", command=self.on_show_mumu).pack(side="left", padx=8, ipadx=10, ipady=5)
        self.var_pause_when_shown = tk.BooleanVar(value=False)
        ttk.Checkbutton(btns2, text="查看时暂停摇一摇（窗口不抖）",
                        variable=self.var_pause_when_shown).pack(side="left", padx=(16, 0))

        self.pb = ttk.Progressbar(r, mode="determinate", maximum=100)
        self.pb.pack(fill="x", padx=12, pady=(2, 6))

        box = ttk.LabelFrame(r, text="运行日志")
        box.pack(fill="both", expand=True, **pad)
        self.txt = tk.Text(box, height=14, wrap="word", font=("Consolas", 9))
        self.txt.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, command=self.txt.yview)
        sb.pack(side="right", fill="y")
        self.txt.config(yscrollcommand=sb.set)

        self._apply_config(self._load_config())
        r.bind("<Return>", lambda e: self.on_start())
        r.bind("<Escape>", lambda e: self.on_stop())
        r.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- helpers ----------
    def get_client(self):
        if self.client is None:
            self.client = MuMuLocationClient(gateway_port=self.disc.get("gateway_port"),
                                             manager_path=self.disc.get("manager"),
                                             adb_path=self.disc.get("adb"))
        return self.client

    def _log(self, msg):
        self.msgq.put(("log", "[%s] %s" % (time.strftime("%H:%M:%S"), msg)))

    def _drain(self):
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                if kind == "log":
                    self.txt.insert("end", payload + "\n")
                    self.txt.see("end")
                elif kind == "progress":
                    self.pb["value"] = payload
                elif kind == "done":
                    self.btn_start["state"] = "normal"
                    self.btn_stop["state"] = "disabled"
                    self.pb["value"] = 0
        except queue.Empty:
            pass
        self.root.after(150, self._drain)

    def read_points(self):
        kind = SYS_OPTIONS[self.csys.get()]
        pts = []
        for i in range(6):
            try:
                lat = float(self.pt_lat[i].get().strip())
                lon = float(self.pt_lon[i].get().strip())
            except ValueError:
                raise ValueError("点 %d 的经纬度不是数字" % (i + 1))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("点 %d 超出经纬度范围" % (i + 1))
            lo, la = to_wgs84(lat, lon, kind)
            pts.append([la, lo])
        return pts

    def build_cfg(self, points):
        speed = float(self.var_speed.get())
        laps = float(self.var_laps.get())
        hz = float(self.var_hz.get())
        delay = float(self.var_delay.get())
        cad = int(float(self.var_cad.get()))
        if not (3.0 <= speed <= 20.0):
            raise ValueError("跑步速度请填 3~20 km/h")
        if laps <= 0 or laps > 200:
            raise ValueError("圈数请填 0~200")
        return RunConfig(
            speed_kmh=speed, laps=laps, update_hz=max(0.2, min(5.0, hz)),
            start_delay_s=max(0.0, delay), cadence=max(0, cad),
            enable_steps=self.var_steps.get(), hide_window=self.var_hide.get(),
            kick_every_s=20.0 if self.var_kick.get() else 0.0,
        )

    # ---------- actions ----------

    # ---------- 配置读写 ----------
    def _load_config(self):
        # %APPDATA% 为主存档（重新打包/换目录都不丢），exe 同目录作为回退
        cands = [config_path(), portable_config_path()]
        for p in cands:
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.cfg_path = p
                self._log("已载入配置：%s" % p)
                return data
            except Exception:
                continue
        self.cfg_path = config_path()
        self._log("未找到历史配置，使用默认值（保存位置：%s）" % self.cfg_path)
        return {}

    def _apply_config(self, cfg):
        gui = cfg.get("gui", {}) or {}
        kind = gui.get("coord_system")
        if kind:
            for label, val in SYS_OPTIONS.items():
                if val == kind:
                    self.csys.set(label)
                    break
        track = cfg.get("track", {}) or {}
        pts = track.get("points_as_entered") or track.get("waypoints_wgs84")
        if isinstance(pts, list) and len(pts) >= 6:
            for i in range(6):
                la, lo = float(pts[i][0]), float(pts[i][1])
                self.pt_lat[i].delete(0, "end")
                self.pt_lat[i].insert(0, "%.6f" % la)
                self.pt_lon[i].delete(0, "end")
                self.pt_lon[i].insert(0, "%.6f" % lo)
        run = cfg.get("run", {}) or {}
        pairs = ((self.var_speed, "speed_kmh", "%.1f"), (self.var_laps, "laps", "%g"),
                 (self.var_hz, "update_hz", "%g"), (self.var_delay, "start_delay_s", "%g"),
                 (self.var_cad, "cadence", "%d"))
        for var, key, fmt in pairs:
            if key in run and run[key] is not None:
                var.set(fmt % float(run[key]) if fmt != "%d" else "%d" % int(run[key]))
        for var, key in ((self.var_steps, "enable_steps"), (self.var_hide, "hide_window"),
                         (self.var_kick, "kick_enabled"), (self.var_pause_when_shown, "pause_when_shown")):
            if key in run:
                var.set(bool(run[key]))

    # ---------- 模拟器窗口 显示 / 隐藏 ----------
    def _feeder(self):
        return getattr(self.sim, "feeder", None) if self.sim else None

    def on_hide_mumu(self):
        try:
            cli = self.get_client()
            if cli.park_window():
                self._log("已把模拟器窗口移到屏幕外（后台运行）")
                f = self._feeder()
                if f is not None:
                    f.resume()
            else:
                self._log("隐藏失败：没找到模拟器窗口（MuMu 是否在运行？）")
        except Exception as exc:  # noqa: BLE001
            self._log("隐藏失败：%s" % exc)

    def on_show_mumu(self):
        try:
            cli = self.get_client()
            ok = cli.unpark_window()
            if not ok:
                try:
                    cli.manager("control", "-v", str(cli.vm_index), "show_window")
                    cli.set_not_topmost()
                    ok = True
                except Exception:
                    ok = False
            if ok:
                self._log("已把模拟器窗口放回屏幕")
                if self.var_pause_when_shown.get():
                    f = self._feeder()
                    if f is not None:
                        f.pause()
                        self._log("已暂停摇一摇（窗口不抖）；点「隐藏模拟器」会自动恢复")
            else:
                self._log("显示失败：没找到模拟器窗口")
        except Exception as exc:  # noqa: BLE001
            self._log("显示失败：%s" % exc)

    def on_check(self):
        try:
            points = self.read_points()
        except ValueError as exc:
            messagebox.showerror("参数有误", str(exc))
            return
        track = Track(points, corner_radius_m=6.0)
        cad = cadence_for_speed(float(self.var_speed.get()))
        self._log("自检：跑道周长 %.1f m，共 %d 个顶点" % (track.length, len(points)))
        self._log("自检：预计步频 %d 步/分" % cad)

        def work():
            try:
                cli = self.get_client()
                self._log("自检：网关端口 %s" % cli.gateway_port)
                cli.set_wgs84(points[0][0], points[0][1])
                self._log("自检：定位下发成功（通道 %s）" % cli.last_transport)
                cli.shake()
                self._log("自检：步数脉冲下发成功")
            except Exception as exc:  # noqa: BLE001
                self._log("自检失败：%s" % exc)
        threading.Thread(target=work, daemon=True).start()

    def on_save(self, silent=False):
        """把界面上的所有内容存盘；下次打开自动读回。"""
        try:
            points = self.read_points()
            cfg = self.build_cfg(points)
            entered = [[float(self.pt_lat[i].get().strip()), float(self.pt_lon[i].get().strip())]
                       for i in range(6)]
        except ValueError as exc:
            if not silent:
                messagebox.showerror("参数有误", str(exc))
            return False
        cfg.kick_enabled = bool(self.var_kick.get())
        cfg.pause_when_shown = bool(self.var_pause_when_shown.get())
        cfg.coord_system = SYS_OPTIONS[self.csys.get()]
        data = {
            "gui": {"coord_system": SYS_OPTIONS[self.csys.get()]},
            "mumu": {"vm_index": 0, "gateway_port": None, "prefer_http": True},
            "run": cfg.__dict__,
            "track": {"points_as_entered": entered, "waypoints_wgs84": points},
        }
        target = config_path()
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        # 如果 exe 旁边有便携 config.json，一起更新，保持两份一致
        portable = portable_config_path()
        if portable != target:
            try:
                with open(portable, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
            except Exception:
                pass
        self.cfg_path = target
        self._log("已保存配置：%s" % target)
        if not silent:
            messagebox.showinfo("已保存", "配置已保存到：\n%s\n\n下次打开会自动载入。" % target)
        return True

    def on_start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            points = self.read_points()
            cfg = self.build_cfg(points)
            track = Track(points, corner_radius_m=cfg.corner_radius_m)
        except ValueError as exc:
            messagebox.showerror("参数有误", str(exc))
            return

        self.on_save(silent=True)          # 开始前自动存一份
        self.stop_flag.clear()
        self.btn_start["state"] = "disabled"
        self.btn_stop["state"] = "normal"
        self._log("=" * 52)
        self._log("开始代跑，请确认 App 已经点了「开始乐跑」。")

        def work():
            try:
                cli = self.get_client()
                sim = RunSimulator(track, cli, cfg, logger=self._log)
                self.sim = sim
                total = cfg.laps * track.length

                def stop_flag():
                    if self.stop_flag.is_set():
                        return True
                    if total > 0:
                        self.msgq.put(("progress", min(100.0, sim.stats.distance_m / total * 100.0)))
                    return False

                sim.run(stop_flag=stop_flag)
            except Exception as exc:  # noqa: BLE001
                self._log("运行出错：%s" % exc)
            finally:
                self.msgq.put(("done", None))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def on_stop(self):
        if self.stop_flag.is_set():
            return
        self.stop_flag.set()
        self._log("正在停止…")

    def on_close(self):
        try:
            self.on_save(silent=True)      # 关窗前自动存一份
        except Exception:
            pass
        self.stop_flag.set()
        time.sleep(0.2)
        self.root.destroy()



def selftest(out_path):
    """打包后自检：不弹界面，跑一小段并把结果写入文件（用于验证 exe 可用）。"""
    lines = []

    def log(msg):
        lines.append("[%s] %s" % (time.strftime("%H:%M:%S"), msg))
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
        except Exception:
            pass

    try:
        disc = discover_mumu()
        log("root=%s" % disc.get("root"))
        log("manager=%s" % disc.get("manager"))
        log("adb=%s" % disc.get("adb"))
        log("gateway_port=%s" % disc.get("gateway_port"))
        pts = [list(to_wgs84(la, lo, "gcj02")[::-1]) for la, lo in DEFAULT_POINTS_GCJ]
        track = Track(pts, corner_radius_m=6.0)
        log("track perimeter=%.1f m" % track.length)
        cfg = RunConfig(speed_kmh=9.0, laps=0.015, start_delay_s=0.0,
                        enable_steps=True, hide_window=True, log_every_s=3.0)
        cli = MuMuLocationClient(gateway_port=disc.get("gateway_port"),
                                 manager_path=disc.get("manager"), adb_path=disc.get("adb"))
        sim = RunSimulator(track, cli, cfg, logger=log)
        sim.run()
        log("RESULT points=%d errors=%d steps=%d distance=%.1f m"
            % (sim.stats.points, sim.stats.errors, sim.stats.steps, sim.stats.distance_m))
        log("SELFTEST_OK")
    except Exception as exc:  # noqa: BLE001
        import traceback
        log("SELFTEST_FAIL %s\n%s" % (exc, traceback.format_exc()))

def main():
    if not acquire_single_instance():
        r = tk.Tk()
        r.withdraw()
        messagebox.showwarning("已在运行", "本程序已经开着一份了。\n\n同时运行两份会让步数脉冲翻倍，导致模拟器/App 异常，请先关掉那一份。")
        r.destroy()
        return
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--dumpconfig":
        import tkinter as _tk
        _r = _tk.Tk()
        _r.withdraw()
        _a = App(_r)
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            fh.write("config=%s\n" % getattr(_a, "cfg_path", ""))
            fh.write("csys=%s\n" % _a.csys.get())
            for _i in range(6):
                fh.write("p%d=%s,%s\n" % (_i + 1, _a.pt_lat[_i].get(), _a.pt_lon[_i].get()))
            fh.write("speed=%s laps=%s hz=%s delay=%s cad=%s\n" % (
                _a.var_speed.get(), _a.var_laps.get(), _a.var_hz.get(),
                _a.var_delay.get(), _a.var_cad.get()))
        _r.destroy()
        raise SystemExit(0)
    if len(sys.argv) >= 3 and sys.argv[1] == "--configpath":
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            fh.write("portable=%s\n" % portable_config_path())
            fh.write("config=%s\n" % config_path())
            fh.write("exists=%s\n" % os.path.exists(config_path()))
        raise SystemExit(0)
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2])
        raise SystemExit(0)
    try:
        main()
    except Exception:
        import traceback
        detail = traceback.format_exc()
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("启动失败", detail[-1500:])
            r.destroy()
        except Exception:
            pass
        raise
