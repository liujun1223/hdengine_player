r"""Constants for the HDEngine Player integration.

协议依据《海缔力智能 232 协议 (RK) 2026-08》与《海缔力 TCP 控制协议》：
- RS-232：9600 8N1，命令帧 ``#<3字符码> \\r``，应答 ``@RHT OK\\r`` / ``@RHT RE\\r``
- TCP：9051 长连接，UTF-8 行分隔（\\n），连接后推送完整状态 JSON，状态变化主动广播
- mDNS：_xiaopai._tcp.，服务属性含 tcp_port / mac
"""

DOMAIN = "hdengine_player"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_BAUDRATE = "baudrate"

DEFAULT_BAUDRATE = 9600
DEFAULT_TCP_PORT = 9051
TCP_CONNECT_TIMEOUT = 4
TCP_COMMAND_TIMEOUT = 3
TCP_READ_TIMEOUT = 75  # 长连接读超时；超时视为断线触发重连
SERIAL_TIMEOUT = 1
# TCP 失败/断线后的退避时间（秒），期间跳过 IP 通道直接走串口
TCP_RETRY_INTERVAL = 60

# mDNS 服务发现
ZEROCONF_TYPE = "_xiaopai._tcp.local."

# 支持 USB 转串口的芯片 VID
# FTDI: 0x0403, Silicon Labs: 0x10C4, Prolific: 0x067B, CH340: 0x1A86
SUPPORTED_USB_VIDS = [0x0403, 0x10C4, 0x067B, 0x1A86]

# ---------------------------------------------------------------------------
# 点播路由（仅 TCP/HTTP 通道）
# ---------------------------------------------------------------------------
TCP_PLAY_MEDIA = "/MediaQuery/playMedia?path={path}"
TCP_PLAY_MUSIC = "/MediaQuery/playMusic?path={path}"

# 音乐文件扩展名（用于 play_media 时选择音乐路由）
MUSIC_EXTENSIONS = (".mp3", ".flac", ".ape", ".wav", ".m4a", ".aac", ".ogg", ".wma", ".cue", ".dff", ".dsf")

# ---------------------------------------------------------------------------
# 指令码表：name -> 3 字符指令码
# TCP 通道直接发送指令码（一行），串口通道包成 #<码> \r
# ---------------------------------------------------------------------------
COMMAND_CODES: dict[str, str] = {
    # 电源
    "power_toggle": "POW",  # 待机/唤醒切换
    "wake": "WAK",
    "shutdown": "OFF",
    "reboot": "RBT",
    # 导航
    "back": "RET",
    "home": "HOM",
    "up": "NUP",
    "down": "NDN",
    "left": "NLT",
    "right": "NRT",
    "ok": "SEL",
    "settings": "SET",
    "delete": "DEL",
    # 音量
    "volume_up": "VUP",
    "volume_down": "VDN",
    "mute": "MUT",
    # 播放控制
    "play": "PLU",
    "pause": "PAU",
    "play_pause": "PLA",
    "stop": "STP",
    "fast_forward": "FWD",
    "rewind": "REV",
    "previous": "PRE",
    "next": "NXT",
    # 信息与菜单
    "info": "INF",
    "repeat": "RPT",
    "audio": "AUD",
    "subtitle": "SUB",
    "popup_menu": "MNU",
    "top_menu": "TOP",
    # 功能入口
    "movie": "MOV",
    "music": "MUS",
    "file": "FIL",
    # 其他
    "resolution": "RES",
    "dot": "DOT",
    # 色彩键（支持快捷绑定）
    "red": "RED",
    "green": "GRE",
    "yellow": "YEL",
    "blue": "BLU",
    # 长按方向键
    "long_left_start": "LPL",
    "long_right_start": "LPR",
    "long_up_start": "LPU",
    "long_down_start": "LPD",
    "long_left_stop": "LRL",
    "long_right_stop": "LRR",
    "long_up_stop": "LRU",
    "long_down_stop": "LRD",
}

# 数字键（支持快捷绑定）
COMMAND_CODES.update({f"num_{i}": f"NU{i}" for i in range(10)})

# 查询指令（不进选择器，内部状态轮询用）
CODE_QUERY_PLAY = "QPS"
CODE_QUERY_POWER = "QPW"

# ---------------------------------------------------------------------------
# 状态反馈文本映射（两通道共用：Status=...;）
# ---------------------------------------------------------------------------
STATUS_PLAYBACK: dict[str, str] = {
    "Play": "playing",
    "Pause": "paused",
    "Stop": "idle",
}
STATUS_POWER: dict[str, str] = {
    "PowOn": "on",
    "PowStandby": "standby",
    "PowOff": "off",
}
