# HDEngine Player

海缔力（HDEngine）播放器的 Home Assistant 自定义集成，**同时支持 RS-232 串口与 IP（TCP）两种控制通道**，与海美迪/亿格瑞集成同一套双通道设计。

协议依据：《海缔力智能 232 协议 (RK) 2026-08》《海缔力 TCP 控制协议》。

## 控制通道

| 通道 | 参数 | 说明 |
| --- | --- | --- |
| TCP | 9051 长连接（UTF-8，行分隔） | 连接即推送完整状态 JSON，状态变化主动广播，无需轮询；支持点播 |
| RS-232 | 9600 8N1，命令帧 `#<3字符码> \r` | 应答 `@RHT OK\r` / `@RHT RE\r`，状态反馈 `Status=...;` |
| mDNS | `_xiaopai._tcp.` | 局域网自动发现设备 |

双通道可只配其一或同时配置：优先走 TCP，断线/不可达自动降级串口，60 秒后自动重试 TCP。

## 功能

- **自动发现**：mDNS 发现局域网设备；也可自动识别串口（发送 `#QPW` 探测）
- 开关机（待机/唤醒/关机/重启）、播放/暂停/停止、上一曲/下一曲、音量/静音
- 状态实时同步：电源状态、播放状态、片名、播放进度（TCP 推送）
- `select` 遥控命令选择器：方向/确认/数字键/色彩键/长按/字幕/音轨/循环等 50+ 指令
- `play_media` 点播影片与音乐（TCP 路由 `/MediaQuery/playMedia`、`playMusic`）
- TCP 断线自动重连，设备关机走串口，开机后自动恢复

## 安装

### HACS

1. HACS → 集成 → 右上角菜单 → 自定义存储库
2. 添加 `https://github.com/liujun1223/hdengine_player`，类别选择「集成」
3. 搜索 “HDEngine Player” 安装，重启 Home Assistant

### 手动安装

把 `custom_components/hdengine_player` 复制到 Home Assistant 的 `config/custom_components/` 目录，重启。

## 配置

添加集成后三选一：

- **mDNS 自动发现**：设备在线时 HA 会直接弹出发现通知，确认即可
- **自动识别串口**：扫描本机串口，识别到设备后可选填 IP
- **手动配置**：串口 / 波特率 / IP 至少填一项

## 许可

MIT
