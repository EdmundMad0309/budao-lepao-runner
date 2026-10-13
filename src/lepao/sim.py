"""跑步主循环：按配速沿跑道推进 + 动态扰动 + 同步步数脉冲。"""
import math
import random
import time
from dataclasses import dataclass, field

from .geo import offset_meters
from .sensors import StepFeeder, cadence_for_speed


@dataclass
class RunConfig:
    speed_kmh: float = 8.0
    speed_jitter_pct: float = 6.0
    update_hz: float = 1.0
    gps_noise_m: float = 1.8
    laps: float = 5.0
    start_delay_s: float = 5.0
    corner_radius_m: float = 6.0
    loop_forever: bool = False
    log_every_s: float = 10.0
    kick_every_s: float = 0.0
    # 真实性 / 抗检测
    enable_steps: bool = True
    cadence: int = 0                 # 0 = 自动按配速推算
    lap_line_drift_m: float = 1.6    # 每圈跑线横向漂移幅度
    warmup_s: float = 45.0           # 起步加速时间
    cooldown_s: float = 20.0         # 结尾减速时间
    traffic_events: bool = True      # 偶发减速（路口/人流）
    hide_window: bool = True


@dataclass
class RunStats:
    started_at: float = field(default_factory=time.time)
    distance_m: float = 0.0
    points: int = 0
    errors: int = 0
    steps: int = 0
    min_speed: float = 1e9
    max_speed: float = 0.0

    @property
    def pace_min_per_km(self):
        if self.distance_m < 50:
            return 0.0
        return (time.time() - self.started_at) / 60.0 / (self.distance_m / 1000.0)


class RunSimulator:
    def __init__(self, track, client, config, logger=print):
        self.track = track
        self.client = client
        self.cfg = config
        self.log = logger
        self.stats = RunStats()
        self._traffic_until = 0.0
        self._next_traffic = random.uniform(180.0, 420.0)
        self._speed_state = 0.0

    # ---------- 运动学 ----------
    def _target_speed(self, elapsed, remaining):
        cfg = self.cfg
        base = cfg.speed_kmh / 3.6
        # 起步加速 / 收尾减速，像真人一样不是瞬间到位
        factor = 1.0
        if cfg.warmup_s > 0 and elapsed < cfg.warmup_s:
            factor *= 0.55 + 0.45 * (elapsed / cfg.warmup_s)
        if not cfg.loop_forever and cfg.cooldown_s > 0 and remaining < base * cfg.cooldown_s:
            factor *= 0.60 + 0.40 * max(0.0, remaining / (base * cfg.cooldown_s))
        # 偶发减速（路口、人流）
        if cfg.traffic_events:
            if elapsed > self._next_traffic and elapsed > self._traffic_until:
                self._traffic_until = elapsed + random.uniform(5.0, 14.0)
                self._next_traffic = elapsed + random.uniform(200.0, 480.0)
                self.log("模拟路口/人流减速 %.0f 秒" % (self._traffic_until - elapsed))
            if elapsed < self._traffic_until:
                factor *= random.uniform(0.45, 0.7)
        return max(0.35, base * factor)

    def _speed_mps(self, dt, elapsed, remaining):
        target = self._target_speed(elapsed, remaining)
        # 一阶惯性：速度连续变化，不做白噪声跳变
        tau = 6.0
        self._speed_state += (target - self._speed_state) * min(1.0, dt / tau)
        jitter = self.cfg.speed_jitter_pct / 100.0
        return max(0.30, self._speed_state * (1.0 + random.uniform(-jitter, jitter) * 0.35))

    # ---------- 主循环 ----------
    def run(self, stop_flag=None):
        cfg, stats = self.cfg, self.stats
        target_distance = cfg.laps * self.track.length
        feeder = None

        self.log("跑道周长 %.1f m | 目标 %.2f km | 平均配速 %.1f km/h (%.2f min/km)"
                 % (self.track.length, target_distance / 1000.0, cfg.speed_kmh, 60.0 / cfg.speed_kmh))

        if cfg.hide_window:
            if self.client.park_window():
                self.log("已把模拟器窗口移到屏幕外（后台运行，不挡桌面、不抢焦点；步数脉冲仍生效）")
            elif self.client.hide_window():
                self.log("已隐藏模拟器窗口（注意：shake 可能会把它重新显示）")
            else:
                self.log("窗口后台化失败，将保持原位置")

        if cfg.enable_steps:
            cadence = cfg.cadence or cadence_for_speed(cfg.speed_kmh)
            feeder = StepFeeder(self.client, cadence=cadence, logger=self.log)
            self.feeder = feeder
            feeder.start()
            self.log("步数模拟已开启：目标步频 %d 步/分" % cadence)

        if cfg.start_delay_s > 0:
            self.log("倒计时 %.0f 秒后开始下发定位…" % cfg.start_delay_s)
            time.sleep(cfg.start_delay_s)

        distance = 0.0
        t0 = time.time()
        next_tick = t0
        next_log = t0 + cfg.log_every_s
        next_kick = t0 + cfg.kick_every_s if cfg.kick_every_s > 0 else None
        lap_index = -1
        drift_n = 0.0
        drift_e = 0.0

        try:
            while True:
                if stop_flag is not None and stop_flag():
                    self.log("收到停止信号，退出")
                    return stats
                now = time.time()
                elapsed = now - t0
                remaining = max(0.0, target_distance - distance)

                # 每次采样间隔本身也加一点抖动，避免严格等间隔
                dt = (1.0 / max(0.2, cfg.update_hz)) * (1.0 + random.uniform(-0.08, 0.08))
                speed = self._speed_mps(dt, elapsed, remaining)
                distance += speed * dt

                lap_now = int(distance // self.track.length)
                if lap_now != lap_index:
                    lap_index = lap_now
                    a = random.uniform(0, 2 * math.pi)
                    r = random.uniform(0, cfg.lap_line_drift_m)
                    drift_n, drift_e = r * math.cos(a), r * math.sin(a)

                lat, lon = self.track.position_at(distance)
                bearing = math.radians(self.track.bearing_at(distance))
                # 跑线漂移（垂直方向）+ GPS 抖动
                lat, lon = offset_meters(lat, lon,
                                         drift_n + random.gauss(0, cfg.gps_noise_m) * 0.7,
                                         drift_e + random.gauss(0, cfg.gps_noise_m) * 0.7)
                lat, lon = offset_meters(lat, lon,
                                         -random.gauss(0, cfg.gps_noise_m * 0.6) * math.sin(bearing),
                                         random.gauss(0, cfg.gps_noise_m * 0.6) * math.cos(bearing))

                try:
                    self.client.set_wgs84(lat, lon)
                    stats.points += 1
                    stats.min_speed = min(stats.min_speed, speed)
                    stats.max_speed = max(stats.max_speed, speed)
                except Exception as exc:  # noqa: BLE001
                    stats.errors += 1
                    if stats.errors <= 3 or stats.errors % 20 == 0:
                        self.log("定位下发失败 #%d: %s" % (stats.errors, exc))

                stats.distance_m = distance
                if feeder is not None:
                    stats.steps = feeder.steps
                    if cfg.cadence == 0:
                        feeder.set_cadence(cadence_for_speed(speed * 3.6))

                if next_kick is not None and now >= next_kick:
                    try:
                        self.client.kick_location()
                    except Exception as exc:  # noqa: BLE001
                        self.log("保底刷新失败: %s" % exc)
                    next_kick = now + cfg.kick_every_s

                if now >= next_log:
                    eta = remaining / max(0.3, speed)
                    self.log("已跑 %.3f/%.2f km | 配速 %.2f min/km | 步数 %d | 定位 %d 次(失败 %d) | ETA %s"
                             % (distance / 1000.0, target_distance / 1000.0, stats.pace_min_per_km,
                                stats.steps, stats.points, stats.errors,
                                time.strftime("%M:%S", time.gmtime(eta))))
                    next_log = now + cfg.log_every_s

                if not cfg.loop_forever and distance >= target_distance:
                    self.log("完成 %.3f km，用时 %s，定位下发 %d 次，模拟步数 %d"
                             % (distance / 1000.0,
                                time.strftime("%H:%M:%S", time.gmtime(time.time() - t0)),
                                stats.points, stats.steps))
                    return stats

                next_tick += dt
                sleep_s = next_tick - time.time()
                if sleep_s > 0:
                    time.sleep(sleep_s)
                else:
                    next_tick = time.time()
        finally:
            if feeder is not None:
                feeder.stop()
            if cfg.hide_window:
                self.client.unpark_window()
                self.log("已把模拟器窗口放回原位")