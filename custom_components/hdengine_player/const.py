r"""Constants for the HDEngine Player integration.

协议依据：
- 《海缔力智能 232 协议 (RK) 2026-08》RS-232：9600 8N1，命令帧 ``#<3字符码> \\r``，
  应答 ``@RHT OK\\r`` / ``@RHT RE\\r``；状态查询与播放控制反馈 ``Status=...;``
- 《海缔力 TCP 控制协议》TCP：9051 长连接，UTF-8 行分隔，连接后推送状态，状态变化主动广播
- 《海缔力 HTTP 控制协议》HTTP：9529，GET/POST，JSON 或 text/plain
- mDNS：_xiaopai._tcp.（HTTP 端口 9529，属性 tcp_port=9051、mac）
"""

import time
from typing import Any

DOMAIN = "hdengine_player"

# 海缔力串口应答特征（"@" 开头为设备应答，"#" 开头是主机命令回显，不算应答）
REPLY_MARKERS = ("Status=", "@RHT", "@QPW", "@POW", "@PLA", "@MUT")


def read_serial_reply(ser: Any, timeout: float) -> str:
    r"""累积读取串口应答；海缔力以 \r 结尾，不能依赖 readline 等 \n."""
    deadline = time.monotonic() + timeout
    buf = ""
    prev_timeout = ser.timeout
    ser.timeout = 0.05  # 短超时轮询，避免首个 read 阻塞整个窗口
    try:
        while time.monotonic() < deadline:
            chunk = ser.read(ser.in_waiting or 1)
            if chunk:
                buf += chunk.decode("utf-8", errors="replace")
                if "\r" in buf or "\n" in buf:
                    break
    finally:
        ser.timeout = prev_timeout
    return buf.strip()


def is_hdengine_reply(text: str) -> bool:
    """判断串口回包是否来自海缔力设备（排除自身命令回显）."""
    return any(marker in text for marker in REPLY_MARKERS)


CONF_HOST = "host"
CONF_PORT = "port"
CONF_BAUDRATE = "baudrate"

DEFAULT_BAUDRATE = 9600
DEFAULT_TCP_PORT = 9051
DEFAULT_HTTP_PORT = 9529
TCP_CONNECT_TIMEOUT = 4
TCP_COMMAND_TIMEOUT = 3
TCP_READ_TIMEOUT = 75  # 长连接读超时；超时视为断线触发重连
HTTP_TIMEOUT = 5
SERIAL_TIMEOUT = 1
# 通道失败后的退避时间（秒），期间降级到下一个通道
CHANNEL_RETRY_INTERVAL = 60
# 所有通道掉线后的恢复探测间隔（秒），设备重新上电后自动恢复
RECOVERY_INTERVAL = 30

# mDNS 服务发现
ZEROCONF_TYPE = "_xiaopai._tcp.local."

# 支持 USB 转串口的芯片 VID
# FTDI: 0x0403, Silicon Labs: 0x10C4, Prolific: 0x067B, CH340: 0x1A86
SUPPORTED_USB_VIDS = [0x0403, 0x10C4, 0x067B, 0x1A86]

# ---------------------------------------------------------------------------
# HTTP 接口（端口 9529）
# ---------------------------------------------------------------------------
HTTP_MODEL = "/ControlCenter/getModel"
HTTP_SEND_KEY = "/ControlCenter/RemoteControl/sendkey?key={key}"
HTTP_LONG_PRESS = "/ControlCenter/RemoteControl/longPress?key={key}&action={action}"
HTTP_QUERY_PLAY = "/Player/queryPlayStatus"
HTTP_QUERY_POWER = "/Player/queryPowerStatus"
HTTP_VIDEO_STATUS = "/VideoPlay/getPlayStatus"
HTTP_VIDEO_CHANGE_STATUS = "/VideoPlay/changeStatus?status={status}"
HTTP_VIDEO_SEEK = "/VideoPlay/seekTo?position={position}"
HTTP_SUBTITLE_LIST = "/VideoPlay/getSubtitleList"
HTTP_SUBTITLE_SET = "/VideoPlay/setSubtitle?index={index}"
HTTP_AUDIO_LIST = "/VideoPlay/getAudioList"
HTTP_AUDIO_SET = "/VideoPlay/setAudio?index={index}"
HTTP_MUSIC_STATE = "/MusicControl/v2/getState"
HTTP_MUSIC_PLAY_PAUSE = "/MusicControl/v2/playOrPause"
HTTP_MUSIC_NEXT = "/MusicControl/v2/playNext"
HTTP_MUSIC_LAST = "/MusicControl/v2/playLast"
HTTP_MUSIC_SEEK = "/MusicControl/v2/seekTo?position={position}"
HTTP_MUSIC_LOOP = "/MusicControl/v2/setLoopMode?mode={mode}"
HTTP_SET_POWER = "/MusicControl/v2/setPowerOption?tag={tag}"
HTTP_PLAY_MEDIA = "/MediaQuery/playMedia?path={path}"
HTTP_PLAY_MUSIC = "/MediaQuery/playMusic?path={path}"

# 电源操作 tag（HTTP）
POWER_TAG_REBOOT = "REBOOT"
POWER_TAG_OFF = "OFF"
POWER_TAG_STANDBY = "STANDBY"
POWER_TAG_WAKEUP = "WAKEUP"

# 音乐文件扩展名（用于 play_media 选择音乐路由）
MUSIC_EXTENSIONS = (
    ".mp3",
    ".flac",
    ".ape",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".wma",
    ".cue",
    ".dff",
    ".dsf",
)

# 监听器最大数量（字幕/音轨选择器）
MAX_OPTIONS = 30

# ---------------------------------------------------------------------------
# 指令码表：name -> 3 字符内部码
# 三通道共用：TCP 直接发码，串口包成 #<码> \r，HTTP 转成 KEY.* 名称（见 HTTP_KEY_NAMES）
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

# ---------------------------------------------------------------------------
# HTTP sendkey 按键名映射（协议第 13 节）；未列出者直接回退内部码
# ---------------------------------------------------------------------------
HTTP_KEY_NAMES: dict[str, str] = {
    "back": "KEY.BACK",
    "home": "KEY.HOME",
    "up": "KEY.UP",
    "down": "KEY.DOWN",
    "left": "KEY.LEFT",
    "right": "KEY.RIGHT",
    "ok": "KEY.OK",
    "delete": "KEY.DASHDEL",
    "play": "KEY.MEDIAPLAY",
    "play_pause": "KEY.MEDIAPLAY.PAUSE",
    "stop": "KEY.MEDIASTOP",
    "pause": "KEY.MEDIAPAUSE",
    "next": "KEY.MEDIANEXT",
    "previous": "KEY.MEDIAPREV",
    "rewind": "KEY.MEDIABACKWARD",
    "fast_forward": "KEY.MEDIAFORWARD",
    "mute": "KEY.MUTE",
    "volume_up": "KEY.VOLUMEUP",
    "volume_down": "KEY.VOLUMEDOWN",
    "subtitle": "KEY.SUBTITLE",
    "audio": "KEY.AUDIO",
    "repeat": "KEY.REPEAT",
    "popup_menu": "KEY.POPMENU",
    "movie": "KEY.MOVIE",
    "music": "KEY.MUSIC",
    "file": "KEY.FILE",
    "resolution": "KEY.RESOLUTION",
    "red": "KEY.USERDEFINE_A",
    "green": "KEY.USERDEFINE_B",
    "yellow": "KEY.USERDEFINE_C",
    "blue": "KEY.USERDEFINE_D",
    "power_toggle": "KEY.POWERON.STANDBY",
    "reboot": "KEY.POWERON.REBOOT",
    "shutdown": "KEY.POWERON.POWEROFF",
    "long_left_start": "KEY.LONG.LEFT",
    "long_right_start": "KEY.LONG.RIGHT",
    "long_up_start": "KEY.LONG.UP",
    "long_down_start": "KEY.LONG.DOWN",
    "long_left_stop": "KEY.LONG.LEFT.RELEASE",
    "long_right_stop": "KEY.LONG.RIGHT.RELEASE",
    "long_up_stop": "KEY.LONG.UP.RELEASE",
    "long_down_stop": "KEY.LONG.DOWN.RELEASE",
}
HTTP_KEY_NAMES.update({f"num_{i}": f"KEY.NUMBER_{i}" for i in range(10)})

# 查询指令（内部状态轮询用）
CODE_QUERY_PLAY = "QPS"
CODE_QUERY_POWER = "QPW"

# ---------------------------------------------------------------------------
# 状态反馈文本映射（三通道共用：Status=...;）
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

# /VideoPlay/getPlayStatus 中 video.status 的含义
VIDEO_STATUS_PLAY = 1
VIDEO_STATUS_PAUSE = 2
VIDEO_STATUS_STOP = 3
