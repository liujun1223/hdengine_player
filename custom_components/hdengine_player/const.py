"""Constants for the HDEngine Player integration.

TODO(协议): 海缔力控制协议文档尚未提供。
KEY_MAP / NAMED_COMMANDS / SERIAL_EVENT_STATE 目前为空占位，
HTTP 路径常量沿用海美迪的结构仅作占位，拿到协议后统一替换。
"""

DOMAIN = "hdengine_player"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_BAUDRATE = "baudrate"

DEFAULT_BAUDRATE = 115200  # TODO(协议): 待确认
DEFAULT_HTTP_PORT = 8899  # TODO(协议): 待确认
HTTP_TIMEOUT = 6
HTTP_CONNECT_TIMEOUT = 4
SERIAL_TIMEOUT = 1
# HTTP 失败后的退避时间（秒），期间跳过 IP 通道直接走串口
HTTP_RETRY_INTERVAL = 60

# 支持 USB 转串口的芯片 VID
# FTDI: 0x0403, Silicon Labs: 0x10C4, Prolific: 0x067B, CH340: 0x1A86
SUPPORTED_USB_VIDS = [0x0403, 0x10C4, 0x067B, 0x1A86]

# ---------------------------------------------------------------------------
# HTTP 接口路径（TODO(协议): 占位，待确认）
# ---------------------------------------------------------------------------
HTTP_DEVICE_INFO = "/get"
HTTP_PLAYER_INFO = "/getPlayerInfo"
HTTP_AUDIO_PLAYER_INFO = "/getAudioPlayerInfo"
HTTP_PLAY_POSTER = "/playPoster"
HTTP_BROADCAST_MUSIC = "/broadCastMusic"
HTTP_SEND_KEY = "/send?key={key}"

# ---------------------------------------------------------------------------
# 遥控键值表（TODO(协议): 待填充）
# name -> 键值；IP 通道用 HTTP_SEND_KEY 下发，串口通道包成 #<key>#
# ---------------------------------------------------------------------------
KEY_MAP: dict[str, int] = {}

# ---------------------------------------------------------------------------
# 复合命令（TODO(协议): 待填充）
# name -> (RS-232 命令, HTTP 路径)；某一端不支持时对应位置为 None
# ---------------------------------------------------------------------------
NAMED_COMMANDS: dict[str, tuple[str | None, str | None]] = {}

# 设备主动上报（仅 RS-232）（TODO(协议): 待填充）
SERIAL_EVENT_STATE: dict[str, str] = {}
