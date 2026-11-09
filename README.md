# HDEngine Player

海缔力（HDEngine）播放器的 Home Assistant 自定义集成，架构上**同时支持 RS-232 串口与 IP（HTTP）两种控制通道**，与海美迪/亿格瑞集成同一套双通道设计。

> **当前状态：脚手架阶段。** 海缔力控制协议文档尚未提供，协议相关的键值表与命令表（[const.py](custom_components/hdengine_player/const.py) 中标记 `TODO(协议)` 的部分）为空占位。拿到协议后填充即可，传输层、配置流程、实体与降级逻辑已完整可用。

## 架构（已实现）

- 双通道控制：可只配串口、只配 IP，或两者同时配置（优先走 IP，失败自动降级到串口，60 秒后自动重试 IP）
- HTTP 传输：裸 socket 实现，按 `Content-Length` 精确读取（兼容 keep-alive 不断连的设备），请求串行化
- 串口：115200 8N1（波特率可配），命令失败自动关闭重连
- 配置流程：自动识别串口 / 手动配置（串口、波特率、IP 至少填一个）
- 实体：`media_player`（开关机、播放控制、音量、点播）+ `select` 遥控命令选择器
- CI：hassfest + HACS 校验工作流，HACS 品牌资源

## 待协议确认的部分

| 位置 | 内容 |
| --- | --- |
| `const.py` `KEY_MAP` | 遥控键值表（电源、方向、音量、数字等） |
| `const.py` `NAMED_COMMANDS` | 复合命令（播放/暂停/停止/开关机等）的串口命令与 HTTP 路径 |
| `const.py` `SERIAL_EVENT_STATE` | 设备主动上报事件映射 |
| `const.py` `HTTP_*` | HTTP 接口路径与默认端口 |
| `__init__.py` / `config_flow.py` | 串口探测命令（当前占位 `#getDeviceInfo#`）与设备信息 JSON 字段名 |

## 安装

### HACS

1. HACS → 集成 → 右上角菜单 → 自定义存储库
2. 添加 `https://github.com/liujun1223/hdengine_player`，类别选择「集成」
3. 搜索 “HDEngine Player” 安装，重启 Home Assistant

### 手动安装

把 `custom_components/hdengine_player` 复制到 Home Assistant 的 `config/custom_components/` 目录，重启。

## 许可

MIT
