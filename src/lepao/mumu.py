"""MuMu 模拟器控制层。

已探明的三条通道（全部无需鼠标、无需前台窗口）：

1) 虚拟定位
   POST http://127.0.0.1:<gateway_port>/player/window
   {"id": uuid, "action": "location", "params": {"address":"", "latitude":.., "longitude":..}}
   注意：MuMu 按 BD-09 解释该坐标，内部做 BD09->GCJ02->WGS84。本层接收 WGS-84，发送前预补偿。

2) 摇一摇（= 加速度计脉冲 = App 记 1 步）
   POST .../player/window {"id": uuid, "action": "tool_click", "params": {"type": "shake"}}
   一次 shake 让加速度计产生 9.81 -> ~40 -> ~30 -> 9.81 的完整峰谷，
   步道乐跑 z9.a 的峰值检测正好判为 1 步，同时把"运动状态"从静止拉到跑步。

3) 回退通道  MuMuManager.exe control -v <idx> tool location --longitude <> --latitude <>
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

from .geo import wgs84_to_bd09

APP_NAME = "MuMuPlayer"
DEFAULT_GATEWAY_PORT = 24576
MANAGER_REL = os.path.join("nx_main", "MuMuManager.exe")


def _candidate_roots():
    roots = []
    env = os.environ.get("MUMU_HOME")
    if env:
        roots.append(env)
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key in (
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Netease\MuMuPlayer"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Netease\MuMuPlayer"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Netease\MuMuPlayer"),
            ):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        val, _ = winreg.QueryValueEx(k, "InstallDir")
                        if val:
                            roots.append(val)
                except OSError:
                    pass
        except Exception:
            pass
        for drive in ("C:", "D:", "E:", "F:"):
            for sub in (r"\Program Files\NetEase\MuMu", r"\Program Files\MuMuPlayer-12.0",
                        r"\Program Files (x86)\Netease\MuMuPlayer-12.0", r"\MuMuPlayer-12.0",
                        r"\Netease\MuMu"):
                roots.append(drive + sub)
    roots.append(r"C:\Program Files\NetEase\MuMu")
    seen, out = set(), []
    for r in roots:
        if r and r not in seen and os.path.isdir(r):
            seen.add(r)
            out.append(r)
    return out


def _find_first(*patterns):
    for root in _candidate_roots():
        for pat in patterns:
            p = os.path.join(root, pat)
            if os.path.exists(p):
                return p
    return None


def discover_mumu():
    """自动定位 MuMu 安装、adb、MuMuManager、网关端口。"""
    info = {"root": None, "manager": None, "adb": None, "gateway_port": None, "vm_index": 0, "vms": []}

    manager = _find_first(MANAGER_REL)
    if not manager:
        for root in _candidate_roots():
            for dp, _dn, fn in os.walk(root):
                if "MuMuManager.exe" in fn:
                    manager = os.path.join(dp, "MuMuManager.exe")
                    break
            if manager:
                break
    info["manager"] = manager
    if manager:
        info["root"] = os.path.dirname(os.path.dirname(manager))

    adb = _find_first(os.path.join("nx_device", "12.0", "shell", "adb.exe"), "adb.exe")
    if adb:
        info["adb"] = adb

    for root in _candidate_roots():
        vms = os.path.join(root, "vms")
        if os.path.isdir(vms):
            for name in sorted(os.listdir(vms)):
                cfg = os.path.join(vms, name, "configs", "vm_config.json")
                if os.path.exists(cfg):
                    info["vms"].append(cfg)

    for cfg in info["vms"]:
        try:
            with open(cfg, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            port = data.get("vm", {}).get("nat", {}).get("port_forward", {}).get("gateway", {}).get("host_port")
            if port:
                info["gateway_port"] = int(port)
                break
        except Exception:
            continue
    if info["gateway_port"] is None:
        info["gateway_port"] = DEFAULT_GATEWAY_PORT

    if manager:
        try:
            out = subprocess.run([manager, "info"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=20)
            data = json.loads(out.stdout)
            for item in (data if isinstance(data, list) else [data]):
                if item.get("is_main") or item.get("index") == "0":
                    info["vm_index"] = int(item.get("index", 0))
                    break
        except Exception:
            pass
    return info


class MuMuGateway:
    """网关直连客户端。"""

    def __init__(self, gateway_port=None, manager_path=None, adb_path=None,
                 vm_index=0, timeout=3.0, address=""):
        self.timeout = timeout
        self.address = address
        self._disc = None
        if gateway_port is None or manager_path is None or adb_path is None:
            self._disc = discover_mumu()
        self.gateway_port = gateway_port or (self._disc or {}).get("gateway_port") or DEFAULT_GATEWAY_PORT
        self.manager_path = manager_path or (self._disc or {}).get("manager")
        self.adb_path = adb_path or (self._disc or {}).get("adb")
        self.vm_index = vm_index if vm_index is not None else (self._disc or {}).get("vm_index", 0)
        self.last_transport = None

    # ---------- low level ----------
    def post(self, action, params):
        payload = {"id": str(uuid.uuid4()), "action": action, "params": params}
        data = json.dumps(payload).encode("utf-8")
        url = "http://127.0.0.1:%d/player/window" % int(self.gateway_port)
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        try:
            obj = json.loads(body)
            if obj.get("errcode") not in (0, None):
                raise RuntimeError("MuMu 返回错误: %s" % body)
        except ValueError:
            pass
        return body

    def manager(self, *args, timeout=20.0):
        if not self.manager_path or not os.path.exists(self.manager_path):
            raise RuntimeError("未找到 MuMuManager.exe，请确认已安装 MuMu 模拟器")
        out = subprocess.run([self.manager_path, *args], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=timeout)
        text = (out.stdout or "") + (out.stderr or "")
        if out.returncode != 0:
            raise RuntimeError("MuMuManager 失败: %s" % text.strip())
        return text

    # ---------- high level ----------
    def set_location_bd(self, lat_bd, lon_bd):
        """直接下发 BD-09 坐标。"""
        try:
            self.post("location", {"address": self.address, "latitude": lat_bd, "longitude": lon_bd})
            self.last_transport = "http"
            return True
        except Exception:
            self.manager("control", "-v", str(self.vm_index), "tool", "location",
                         "--longitude", "%.7f" % lon_bd, "--latitude", "%.7f" % lat_bd)
            self.last_transport = "cli"
            return True

    def shake(self):
        """触发一次摇一摇，产生一个加速度计峰谷。"""
        try:
            self.post("tool_click", {"type": "shake"})
            self.last_transport = "http"
            return True
        except Exception:
            self.manager("control", "-v", str(self.vm_index), "tool", "func", "--name", "shake")
            self.last_transport = "cli"
            return True

    def main_window_handle(self):
        """取 MuMu 窗口句柄（用于置顶/取消置顶等宿主侧操作）。"""
        try:
            out = self.manager("info", "-v", str(self.vm_index))
            data = json.loads(out)
            item = data[0] if isinstance(data, list) else data
            h = item.get("main_wnd")
            return int(str(h), 16) if h else None
        except Exception:
            return None

    def set_not_topmost(self):
        """把 MuMu 窗口取消置顶，避免挡住桌面。"""
        if sys.platform != "win32":
            return False
        hwnd = self.main_window_handle()
        if not hwnd:
            return False
        try:
            import ctypes
            HWND_NOTOPMOST = -2
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
            ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(HWND_NOTOPMOST),
                                              0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
            return True
        except Exception:
            return False

    # ---------- 窗口"后台化"：移到屏幕外 ----------
    _saved_rect = None

    def _win(self):
        try:
            import ctypes
            return ctypes.windll.user32
        except Exception:
            return None

    def park_window(self):
        """把 MuMu 窗口挪到屏幕外。

        为什么不用 hide_window：shake 会把隐藏的窗口重新显示并置顶。
        窗口移到屏幕外后，shake 依然能产生加速度计波形，但用户完全看不到，
        也不挡桌面、不抢焦点。
        """
        if sys.platform != "win32":
            return False
        u32 = self._win()
        hwnd = self.main_window_handle()
        if not u32 or not hwnd:
            return False
        try:
            import ctypes

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            rc = RECT()
            u32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rc))
            # 已经不在屏幕内时不要覆盖已保存的位置，否则"显示"会失效
            if rc.left > -30000 and rc.top > -30000:
                self._saved_rect = (rc.left, rc.top)
            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
            u32.SetWindowPos(ctypes.c_void_p(hwnd), None, -32000, -32000, 0, 0,
                             SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            return True
        except Exception:
            return False

    def unpark_window(self):
        """把 MuMu 窗口挪回原位。"""
        if sys.platform != "win32":
            return False
        u32 = self._win()
        hwnd = self.main_window_handle()
        if not u32 or not hwnd:
            return False
        try:
            import ctypes
            SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
            x, y = self._saved_rect or (120, 120)
            if x <= -30000 or y <= -30000:
                x, y = 120, 120
            u32.SetWindowPos(ctypes.c_void_p(hwnd), None, x, y, 0, 0,
                             SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            self._saved_rect = None
            return True
        except Exception:
            return False

    def hide_window(self):
        """把模拟器窗口藏到后台：不影响 guest 运行，也不占用桌面/键鼠。"""
        ok = False
        try:
            self.manager("control", "-v", str(self.vm_index), "hide_window")
            ok = True
        except Exception:
            ok = False
        try:
            self.set_not_topmost()
        except Exception:
            pass
        return ok

    def show_window(self):
        try:
            self.manager("control", "-v", str(self.vm_index), "show_window")
            return True
        except Exception:
            return False

    def kick_location(self, delay_s=0.9):
        """关/开位置总开关，强制 GNSS 引擎重取当前虚拟位置。"""
        if not self.adb_path or not os.path.exists(self.adb_path):
            raise RuntimeError("未找到 adb")
        subprocess.run([self.adb_path, "shell", "cmd location set-location-enabled false"],
                       capture_output=True, timeout=15)
        time.sleep(delay_s)
        subprocess.run([self.adb_path, "shell", "cmd location set-location-enabled true"],
                       capture_output=True, timeout=15)
        return True


class MuMuLocationClient(MuMuGateway):
    """兼容层：接收 WGS-84，内部转 BD-09 下发。"""

    def __init__(self, vm_index=0, manager_path=None, gateway_port=None,
                 vm_config_path=None, timeout=3.0, prefer_http=True, address="", adb_path=None):
        super().__init__(gateway_port=gateway_port, manager_path=manager_path,
                         adb_path=adb_path, vm_index=vm_index, timeout=timeout, address=address)
        self.prefer_http = prefer_http
        self.errors = 0

    def set_wgs84(self, lat, lon):
        lon_bd, lat_bd = wgs84_to_bd09(lon, lat)
        try:
            return self.set_location_bd(lat_bd, lon_bd)
        except Exception:
            self.errors += 1
            raise