# 步道乐跑 · MuMu 后台代跑助手

Windows 桌面工具：在 MuMu 模拟器里驱动虚拟定位与加速度计，
让「步道乐跑」记录一段合理的跑步轨迹。**纯后台运行，不占用鼠标键盘。**

单文件免安装 EXE 见 [`dist/步道乐跑助手.exe`](dist/步道乐跑助手.exe)（约 11.6 MB）。

---

## 功能

- **虚拟定位**：任意 N 个顶点连成闭合跑道，按目标配速沿跑道推进（拐角自动抹圆）
- **步数模拟**：按目标步频注入加速度计脉冲，满足 App 的步数/运动状态判据
- **真实化扰动**：一阶惯性配速、起步加速/收尾减速、随机路口减速、每圈跑线漂移、双向 GPS 抖动、下发节奏抖动
- **后台运行**：把模拟器窗口移到屏幕外，不挡桌面、不抢焦点，跑完自动还原
- **随时查看**：一键「显示模拟器」看进度，再一键「隐藏」回后台
- **配置持久化**：坐标、坐标系、速度等全部自动保存，重装/换目录不丢
- **坐标系自适应**：支持高德/腾讯（GCJ-02）、百度（BD-09）、GPS/谷歌（WGS-84）三种输入

---

## 快速开始

### 1. 环境

- Windows 10/11
- [MuMu 模拟器](https://mumu.163.com/) 12（装在任何盘都行，程序会自动探测）
- 模拟器里装好「步道乐跑」，登录，进入首页

> 不需要管理员权限，**不需要开 MuMu 的 ROOT 开关**。

### 2. 跑起来

1. 打开 MuMu，进入「步道乐跑」，点**开始乐跑**，让它进入跑步记录界面
2. 打开 `步道乐跑助手.exe`
3. 填 6 个跑道顶点的经纬度（顺序连线，顺/逆时针都行）
4. 选坐标系 —— 从哪个地图复制的就选哪个：
   - 高德 / 腾讯地图 → `GCJ-02`
   - 百度地图 → `BD-09`
   - 谷歌地球 / 手机 GPS → `WGS-84`
5. 填跑步速度（建议 **6~11 km/h**）
6. 点 **▶ 开始代跑**

之后模拟器窗口会自动移出屏幕，你正常用电脑即可。
想看进度点「显示模拟器（看进度）」，看完点「隐藏模拟器（后台）」。

### 3. 取跑道顶点

在任意地图上按住跑道边界的几个角，把经纬度按顺序抄进来。
6 个点足够画一个椭圆跑道，想更贴合就多填几个（界面固定 6 行，需要更多点可以改 `config.json`）。

---

## 从源码运行

```powershell
pip install -r requirements.txt   # 实际只需要标准库，见文件注释
python gui.py                     # 图形界面
python run.py --check             # 自检：坐标换算 / 跑道 / MuMu 通道
python run.py --preview           # 生成 track_preview.html 预览跑道形状
python verify.py                  # 端到端验证：目标点 vs App 视角拿到的定位
```

调试参数：

```
步道乐跑助手.exe --selftest out.txt     # 无界面自检，验证环境探测与下发
步道乐跑助手.exe --configpath out.txt   # 显示配置文件存放路径
步道乐跑助手.exe --dumpconfig out.txt   # 显示当前实际载入的配置值
```

---

## 构建 EXE

```powershell
.\build_exe.ps1
```

脚本会调用 PyInstaller 打包为单文件：

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name LePaoRunner --paths src --distpath dist --workpath build --specpath build gui.py
```

---

## 工作原理

### MuMu 虚拟定位通道

MuMu 把 guest 的虚拟定位以 **GPS provider** 的形式交给 App，`Location.isMock()` 为 `false`。

```
POST http://127.0.0.1:<gateway_port>/player/window
{"id": "<uuid>", "action": "location",
 "params": {"address": "", "latitude": <bd_lat>, "longitude": <bd_lon>}}
```

- 网关端口从 `MuMuPlayer-12.0-*/configs/vm_config.json` 的 `nat.port_forward.gateway.host_port` 探测（默认 24576）
- 单次往返 < 20 ms，适合 1 Hz 连续喂点
- 回退通道：`MuMuManager.exe control -v <idx> tool location --longitude <> --latitude <>`

### 坐标换算

MuMu 内部固定做 `BD09 → GCJ02 → WGS84`：

```
[LocationPresenter::convertDB09ToWGS84_] GCJ02 point: {113.403, 23.047}
[LocationPresenter::convertDB09ToWGS84_] WGS84 point: {113.397, 23.0495}
```

所以**直接填手机 GPS 坐标会偏约 1 km**。本工具内部把 WGS-84 预补偿成 BD-09，
实测最终落到目标点偏差 **0.6 m**。

### 步数模拟

App 的运动数据来自加速度计（脱壳后分析得到）：

```java
// 取加速度计幅值
float v = sqrt(x*x + y*y + z*z);
// 相邻采样"先升后降"，且 (连续下降>=2 或 峰值>=18) 视为一个峰
// 再要求 距上一步 >=250ms 且 (峰-谷) >= 动态阈值(1.3~4.3)  →  记 1 步
//
// 另用 180 个幅值样本的 MAD 判断运动状态：
//   MAD>5 → 跑步 ; (2,5] → 走路 ; <1.5 → 静止 ; [1.5,2] → 异常
```

MuMu 默认加速度计恒为 `9.81`（重力方向），幅值恒定 → **0 步 + 静止**。
MuMu 的「摇一摇」会在加速度计上产生完整峰谷（`9.8 → 31 → 48 → 21 → 38 → 11`，约 500 ms），
正好被判定为 1 步，同时把 MAD 拉进"跑步"区间。

```
POST .../player/window {"action": "tool_click", "params": {"type": "shake"}}
```

**关键限制（实测）**：MuMu 固定 576 ms 动画，且**每 ~655 ms 才消化一次请求**。
发得比这快只会让请求排队 → 窗口会一直抖下去 → 宿主渲染出现白屏。
因此本工具把步频硬限制在 **84 步/分**（间隔 ≥ 0.70 s），保证队列不堆积。

### 后台运行

- `shake` 本质是**摇窗口**动画，没有纯传感器版本；
  `gravity`（重力感应）只改方向、幅值恒定，产生不了峰值 —— 这两条都实测确认过。
- 而且 `shake` 会把被 `hide_window` 隐藏的窗口**重新显示并置顶**。
- 因此本工具改用 `SetWindowPos(-32000, -32000)` 把窗口挪到屏幕外：
  实测**屏幕外 shake 依然产生完整波形**，用户完全看不到，跑完自动还原。

---

## 常见问题

**Q：轨迹没动 / App 不记录？**
先确认 App 已经点了「开始乐跑」。若仍不动，勾上「保底刷新」——
每 20 秒关/开一次定位总开关强制 GNSS 引擎重取当前位置。

**Q：模拟器窗口一直挡在最上层？**
点「隐藏模拟器（后台）」。程序启动时也会自动取消置顶。

**Q：App 白屏？**
不要同时开两份程序（已有单实例锁保护），也不要把步频调到 84/分以上。

**Q：坐标每换一台电脑就没了？**
配置存在 `%APPDATA%\LePaoRunner\config.json`；拷这个文件即可迁移。
exe 同目录若有 `config.json` 会作为便携回退。

**Q：提示「必要权限未开启」进不去跑步页？**
```
adb shell pm grant com.lptiyu.tanke android.permission.ACCESS_FINE_LOCATION
adb shell pm grant com.lptiyu.tanke android.permission.ACCESS_COARSE_LOCATION
adb shell pm grant com.lptiyu.tanke android.permission.ACCESS_BACKGROUND_LOCATION
adb shell cmd appops set com.lptiyu.tanke SYSTEM_ALERT_WINDOW allow
adb shell dumpsys deviceidle whitelist +com.lptiyu.tanke
```
adb 在 `<MuMu安装目录>\nx_device\12.0\shell\adb.exe`。

---

## 目录结构

```
lepao-runner/
├── gui.py                 图形界面（tkinter）
├── run.py                 命令行入口
├── verify.py              端到端验证脚本
├── build_exe.ps1          打包脚本
├── src/lepao/
│   ├── geo.py             WGS84 / GCJ02 / BD09 互转 + haversine
│   ├── mumu.py            MuMu 环境探测、网关直连、窗口后台化
│   ├── sensors.py         步数脉冲发生器（限流保护）
│   ├── track.py           顶点 → 闭合跑道 → 按里程插值
│   └── sim.py             跑步主循环（配速模型 / 漂移 / 日志）
├── tools/probe/           设备端探针源码（Java，用于验证注入是否生效）
└── dist/                  打包好的 EXE
```

---

## 致谢 / 参考

- 最初的思路来自 [17Y9E81/happy-run](https://github.com/17Y9E81/happy-run)（鼠标点坐标框 + 粘贴），
  本项目把它的思路换成了 MuMu 的接口调用，因此不再占用键鼠。