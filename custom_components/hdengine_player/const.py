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
    r"""累积读取串口应答.

    设备常把「命令回显」与「真实应答」分两段发送，例如 ``#QPW\r`` 之后
    才跟 ``@RHT OK\r``。旧逻辑一见 \r/\n 就退出，导致只拿到回显、
    丢掉真正的应答。改为：只有识别到设备应答（见 is_hdengine_reply）
    才收尾退出，否则持续累积到 timeout。
    """
    deadline = time.monotonic() + timeout
    buf = ""
    prev_timeout = ser.timeout
    ser.timeout = 0.05  # 短超时轮询，避免首个 read 阻塞整个窗口
    try:
        while time.monotonic() < deadline:
            chunk = ser.read(ser.in_waiting or 1)
            if not chunk:
                continue
            buf += chunk.decode("utf-8", errors="replace")
            if is_hdengine_reply(buf):
                # 已拿到真实应答，短暂收尾把尾部字节收完
                tail_deadline = time.monotonic() + 0.15
                while time.monotonic() < tail_deadline:
                    tail = ser.read(ser.in_waiting or 1)
                    if tail:
                        buf += tail.decode("utf-8", errors="replace")
                        tail_deadline = time.monotonic() + 0.05
                break
            if len(buf) > 512:
                break
    finally:
        ser.timeout = prev_timeout
    return buf.strip()


def is_hdengine_reply(text: str) -> bool:
    """判断串口回包是否来自海缔力设备.

    实测 H6.0.49 等固件的应答带命令回显前缀，形如 ``#POW @RHT OK``，
    因此不能简单丢弃 "#" 开头的回包；改为优先匹配应答标记
    （@RHT / @QPW / @POW / Status=），再兼容裸 "@" 前缀应答。
    """
    stripped = text.strip()
    if not stripped:
        return False
    if any(marker in stripped for marker in REPLY_MARKERS):
        return True
    return stripped.startswith("@")


CONF_HOST = "host"
CONF_PORT = "port"
CONF_BAUDRATE = "baudrate"

DEFAULT_BAUDRATE = 9600
DEFAULT_TCP_PORT = 9051
DEFAULT_HTTP_PORT = 9529
TCP_CONNECT_TIMEOUT = 4
TCP_FIRST_LINE_TIMEOUT = 1.5  # 建立连接后等待首条推送的时间，超时不影响通道可用
TCP_COMMAND_TIMEOUT = 3
TCP_READ_TIMEOUT = 75  # 长连接读超时；超时视为断线触发重连
HTTP_TIMEOUT = 5
SERIAL_TIMEOUT = 1
# 通道失败后的退避时间（秒），期间降级到下一个通道
CHANNEL_RETRY_INTERVAL = 60
# 所有通道掉线后的恢复探测间隔（秒），设备重新上电后自动恢复
RECOVERY_INTERVAL = 30
# 下发命令时若通道全部掉线，最小重连间隔（秒）
COMMAND_RECONNECT_INTERVAL = 5

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
    "shutdown": "POW",  # 协议只有 POW=待机，没有独立关机码
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
# HTTP sendkey 按键名映射（双版本自动回退）
#
# 新固件（协议文档 2026-08 / RK3588 Android 12）用 KEY.* 全大写前缀
# 老固件（H6.0.49 Android 9 等）用 Key.* 首字母大写前缀
# 调用时两套按 NEW→OLD→内部码顺序试，第一个返回 status=200 的生效
# ---------------------------------------------------------------------------

# 新固件键名（协议 2026-08）
HTTP_KEY_NAMES_NEW: dict[str, str] = {
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
HTTP_KEY_NAMES_NEW.update({f"num_{i}": f"KEY.NUMBER_{i}" for i in range(10)})

# 老固件键名（H6.0.49 Android 9 等，经实测有效）
HTTP_KEY_NAMES_OLD: dict[str, str] = {
    "back": "Key.Back",
    "home": "Key.Home",
    "up": "Key.Up",
    "down": "Key.Down",
    "left": "Key.Left",
    "right": "Key.Right",
    # 注意：Key.Ok（小写 k）能通，Key.OK（全大写 K）不行
    "ok": "Key.Ok",
    "delete": "Key.Delete",
    "play": "Key.MediaPlay",
    "play_pause": "Key.MediaPlayPause",
    "stop": "Key.MediaStop",
    "pause": "Key.MediaPause",
    "next": "Key.MediaNext",
    "previous": "Key.MediaPrevious",
    "fast_forward": "Key.FastForward",
    "rewind": "Key.Rewind",
    "mute": "Key.Mute",
    "volume_up": "Key.VolumeUp",
    "volume_down": "Key.VolumeDown",
    "subtitle": "Key.Subtitle",
    "audio": "Key.Audio",
    "repeat": "Key.Repeat",
    "popup_menu": "Key.Menu",
    "movie": "Key.Movie",
    "music": "Key.Music",
    "file": "Key.File",
    "resolution": "Key.Resolution",
    "red": "Key.UserDefineA",
    "green": "Key.UserDefineB",
    "yellow": "Key.UserDefineC",
    "blue": "Key.UserDefineD",
    # 老固件只有 PowerOn 能通（待机/唤醒切换），关机/重启走串口
    "power_toggle": "Key.PowerOn",
    "reboot": "Key.Reboot",
    "shutdown": "Key.PowerOff",
}
HTTP_KEY_NAMES_OLD.update({f"num_{i}": f"Key.Number_{i}" for i in range(10)})

# 兼容导出（默认新固件映射）
HTTP_KEY_NAMES = HTTP_KEY_NAMES_NEW

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
