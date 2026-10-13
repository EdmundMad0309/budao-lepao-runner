"""步数 / 运动状态模拟（对抗 cheatMonitoringPlan1/4/5/6）。

逆向结论（com.lptiyu.tanke）：
  z9.a.onSensorChanged  监听 TYPE_ACCELEROMETER，取幅值 sqrt(x^2+y^2+z^2)
  z9.a.b(幅值)          相邻采样"先升后降"且 (连续下降>=2 或 峰值>=18) 视为一个峰
                        再要求 距上一步 >=250ms 且 (峰-谷) >= 动态阈值(1.3~4.3) -> 记 1 步
  RunHelper.f43775o1    180 个幅值样本的平均绝对偏差 MAD -> 运动状态
                        >5 跑步 / (2,5] 走路 / <1.5 静止 / [1.5,2] 异常
  cheatMonitoringPlan1  状态均值==3 累计作弊
  cheatMonitoringPlan4/5/6  新增步数 <= step_num 累计作弊

MuMu 默认加速度计恒为 9.81 -> 0 步 + 静止 -> 必被抓。
实测可行的注入方式只有"摇一摇"，它会让加速度计产生
9.8 -> 31 -> 48 -> 21 -> 38 -> 11 的完整波形（约 500ms），正好判为 1 步。

重要限制（已实测）：
  * MuMu 的 shake 是"摇窗口"动画，没有纯传感器版本；窗口隐藏后依然生效。
  * MuMu 固定 576ms 动画，且每 ~655ms 才消化一次请求。
    发得比这快只会让请求排队，窗口会一直抖下去（这是之前"App 白屏"的根因）。
    因此这里把步频硬性限制在安全上限内，保证队列不堆积。

安全上限: 60 / 0.70s ≈ 85 步/分
"""
import random
import threading
import time

# 实测 MuMu 单次 shake 处理耗时 ~655ms；取 700ms 留余量
MIN_SHAKE_INTERVAL_S = 0.70
MAX_CADENCE = 84
MIN_CADENCE = 45


def cadence_for_speed(speed_kmh):
    """由配速推步频，并夹到 MuMu 能稳定消化的范围内。"""
    c = 128.0 + speed_kmh * 5.0
    return int(max(MIN_CADENCE, min(MAX_CADENCE, c)))


class StepFeeder(threading.Thread):
    """后台线程：按安全步频触发 shake，一次 shake = 一步。"""

    _instances = 0
    _guard = threading.Lock()

    def __init__(self, gateway, cadence=80, logger=print, start_delay=0.0):
        super().__init__(daemon=True)
        self.gateway = gateway
        self.cadence = max(MIN_CADENCE, min(MAX_CADENCE, int(cadence)))
        self.log = logger
        self.start_delay = start_delay
        self._stop = threading.Event()
        self._paused = threading.Event()
        self.steps = 0
        self.failures = 0
        with StepFeeder._guard:
            StepFeeder._instances += 1
            if StepFeeder._instances > 1:
                self.log("警告：检测到多个步数发生器实例，已限流（不要同时开两个本程序）")

    def stop(self):
        self._stop.set()

    def pause(self):
        """临时暂停脉冲（例如用户要看模拟器画面时）。"""
        self._paused.set()

    def resume(self):
        self._paused.clear()

    @property
    def paused(self):
        return self._paused.is_set()

    def set_cadence(self, cadence):
        self.cadence = max(MIN_CADENCE, min(MAX_CADENCE, int(cadence)))

    @property
    def interval(self):
        return max(MIN_SHAKE_INTERVAL_S, 60.0 / max(1, self.cadence))

    def run(self):
        if self.start_delay > 0:
            self._stop.wait(self.start_delay)
        next_at = time.time()
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.1)
                next_at = time.time()
                continue
            try:
                self.gateway.shake()
                self.steps += 1
            except Exception as exc:  # noqa: BLE001
                self.failures += 1
                if self.failures <= 3 or self.failures % 20 == 0:
                    self.log("步数脉冲失败 #%d: %s" % (self.failures, exc))
            iv = self.interval * (1.0 + random.uniform(-0.02, 0.02))
            next_at += iv
            sleep_s = next_at - time.time()
            if sleep_s > 0:
                time.sleep(sleep_s)
            elif sleep_s < -0.35:
                # 落后太多说明被别的东西挤了，重新对齐，避免追帧式连发
                next_at = time.time()