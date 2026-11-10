r"""HDEngine Player 集成（TCP / HTTP / RS-232 三通道）.

- TCP：9051 长连接，UTF-8 行分隔；连接后推送完整状态 JSON，状态变化主动广播，
  空行可查询完整状态；与 HTTP 共用路由
- HTTP：9529，GET 查询/控制，JSON 或 text/plain 响应
- RS-232：9600 8N1，命令帧 #<3字符码> \\r，应答 @RHT OK\\r / @RHT RE\\r，
  状态反馈 Status=...;
- 三通道优先 TCP，其次 HTTP，最后串口；失败后按退避时间自动重试
"""

import asyncio
import contextlib
import http.client
import json
import logging
import time
from typing import Any
import urllib.parse

import serial

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    CHANNEL_RETRY_INTERVAL,
    CODE_QUERY_PLAY,
    CODE_QUERY_POWER,
    COMMAND_CODES,
    CONF_BAUDRATE,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_BAUDRATE,
    DEFAULT_HTTP_PORT,
    DEFAULT_TCP_PORT,
    DOMAIN,
    HTTP_KEY_NAMES,
    HTTP_MODEL,
    HTTP_MUSIC_STATE,
    HTTP_PLAY_MEDIA,
    HTTP_PLAY_MUSIC,
    HTTP_QUERY_PLAY,
    HTTP_QUERY_POWER,
    HTTP_SEND_KEY,
    HTTP_TIMEOUT,
    HTTP_VIDEO_STATUS,
    MUSIC_EXTENSIONS,
    SERIAL_TIMEOUT,
    STATUS_PLAYBACK,
    STATUS_POWER,
    TCP_COMMAND_TIMEOUT,
    TCP_CONNECT_TIMEOUT,
    TCP_READ_TIMEOUT,
    VIDEO_STATUS_PAUSE,
    VIDEO_STATUS_PLAY,
    VIDEO_STATUS_STOP,
    is_hdengine_reply,
    read_serial_reply,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.SELECT]

# 状态推送信号
SIGNAL_STATE_UPDATED = f"{DOMAIN}_state_updated"


class HDEnginePlayer:
    """海缔力播放器 API，封装 TCP 长连接、HTTP 与 RS-232 串口三种通路."""

    def __init__(  # noqa: PLR0917
        self,
        hass: HomeAssistant,
        host: str | None,
        port: str | None,
        baudrate: int = DEFAULT_BAUDRATE,
        http_port: int = DEFAULT_HTTP_PORT,
        tcp_port: int = DEFAULT_TCP_PORT,
    ) -> None:
        """初始化."""
        self.hass = hass
        self._host = host
        self._http_port = http_port
        self._tcp_port = tcp_port
        self._port = port
        self._baudrate = baudrate
        self.name = "HDEngine Player"
        self.model: str | None = None
        self.mac: str | None = None
        self.version: str | None = None
        self._available_tcp = False
        self._available_http = False
        self._available_serial = False
        self._serial: serial.Serial | None = None
        self._serial_lock = asyncio.Lock()
        self._tcp_lock = asyncio.Lock()
        self._http_lock = asyncio.Lock()
        self._tcp_reader: asyncio.StreamReader | None = None
        self._tcp_writer: asyncio.StreamWriter | None = None
        self._tcp_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False
        # 播放状态（三通道统一）
        self.play_status: int | None = None  # 1 播放 / 2 暂停 / -1 停止
        self.power_state: str | None = None  # on / standby / off
        self.title: str | None = None
        self.position_ms: int | None = None
        self.duration_ms: int | None = None
        self.play_type: int | None = None  # 1 视频 / 2 音乐

    @property
    def host(self) -> str | None:
        """返回 IP."""
        return self._host

    @property
    def port(self) -> str | None:
        """返回串口设备路径."""
        return self._port

    @property
    def is_available(self) -> bool:
        """任一通道可用."""
        return self._available_tcp or self._available_http or self._available_serial

    # ------------------------------------------------------------------
    # TCP 传输（9051 长连接）
    # ------------------------------------------------------------------

    async def _async_tcp_open(self) -> bool:
        """建立 TCP 连接并读取首条状态推送."""
        try:
            self._tcp_reader, self._tcp_writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._tcp_port),
                timeout=TCP_CONNECT_TIMEOUT,
            )
            line = await asyncio.wait_for(
                self._tcp_reader.readline(), timeout=TCP_CONNECT_TIMEOUT
            )
            if not line:
                raise ConnectionError("连接后立即断开")
        except (TimeoutError, OSError) as err:
            _LOGGER.warning(
                "TCP 连接失败（%s:%s）：%s", self._host, self._tcp_port, err
            )
            await self._async_tcp_close()
            return False
        self._handle_line(line.decode("utf-8", errors="replace").strip())
        return True

    async def _async_tcp_close(self) -> None:
        """关闭 TCP 连接."""
        writer, self._tcp_writer = self._tcp_writer, None
        self._tcp_reader = None
        if writer is not None:
            with contextlib.suppress(OSError):
                writer.close()
                await writer.wait_closed()

    async def _async_tcp_reader_loop(self) -> None:
        """后台守护任务：处理状态推送，断线后按退避重连."""
        while not self._closed:
            if self._tcp_reader is None:
                if not self._host:
                    break
                if not await self._async_tcp_open():
                    self._available_tcp = False
                    await asyncio.sleep(CHANNEL_RETRY_INTERVAL)
                    continue
                self._available_tcp = True
                _LOGGER.info("TCP 控制已恢复（%s:%s）", self._host, self._tcp_port)
                async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED, self)
            try:
                line = await asyncio.wait_for(
                    self._tcp_reader.readline(), TCP_READ_TIMEOUT
                )
            except TimeoutError:
                # 长时间无推送属正常空闲，发空行心跳确认连接仍活着，不当作断线
                if self._tcp_writer is not None:
                    with contextlib.suppress(OSError):
                        async with self._tcp_lock:
                            self._tcp_writer.write(b"\n")
                            await self._tcp_writer.drain()
                continue
            except OSError as err:
                _LOGGER.warning(
                    "TCP 读取异常（%s:%s）：%s", self._host, self._tcp_port, err
                )
                line = b""
            if line:
                text = line.decode("utf-8", errors="replace").strip()
                self._handle_line(text)
                if self._tcp_queue.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        self._tcp_queue.get_nowait()
                self._tcp_queue.put_nowait(text)
                continue
            was_available = self._available_tcp
            self._available_tcp = False
            await self._async_tcp_close()
            if was_available:
                _LOGGER.warning(
                    "TCP 控制断开（%s:%s），降级到 HTTP/串口，%s 秒后重连",
                    self._host,
                    self._tcp_port,
                    CHANNEL_RETRY_INTERVAL,
                )
                async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED, self)
            await asyncio.sleep(CHANNEL_RETRY_INTERVAL)

    async def _async_tcp_send(self, command: str) -> bool:
        """只发送不等待响应（响应由守护任务处理），返回是否写入成功."""
        async with self._tcp_lock:
            writer = self._tcp_writer
            if writer is None:
                _LOGGER.warning("TCP 连接未建立（%s:%s）", self._host, self._tcp_port)
                return False
            try:
                writer.write(command.encode("utf-8") + b"\n")
                await writer.drain()
            except OSError as err:
                _LOGGER.warning(
                    "TCP 写入失败（%s:%s）：%s", self._host, self._tcp_port, err
                )
                return False
        return True

    async def _async_tcp_command(self, command: str) -> str | None:
        """发送一行命令并等待响应.

        返回响应文本；返回 "" 表示已发出但设备未回显（通道仍可用）；
        返回 None 表示写入失败或连接已断开。
        """
        async with self._tcp_lock:
            if self._tcp_writer is None or self._tcp_reader is None:
                _LOGGER.warning("TCP 连接未建立（%s:%s）", self._host, self._tcp_port)
                return None
            try:
                self._tcp_writer.write(command.encode("utf-8") + b"\n")
                await self._tcp_writer.drain()
            except OSError as err:
                _LOGGER.warning(
                    "TCP 写入失败（%s:%s）：%s", self._host, self._tcp_port, err
                )
                return None
            deadline = time.monotonic() + TCP_COMMAND_TIMEOUT
            while time.monotonic() < deadline:
                try:
                    text = await asyncio.wait_for(
                        self._tcp_queue.get(),
                        timeout=max(0.1, deadline - time.monotonic()),
                    )
                except TimeoutError:
                    break
                if not text:
                    continue
                if not command:
                    # 空行查询：首条即完整状态
                    return text
                data = _parse_json(text)
                if isinstance(data, dict) and not (
                    {"deviceName", "playStatus", "powerState", "event"} & data.keys()
                ):
                    return text
            _LOGGER.warning(
                "TCP 命令 %r 已发出但 %s 秒内无响应（通道保持可用）",
                command,
                TCP_COMMAND_TIMEOUT,
            )
        return ""

    # ------------------------------------------------------------------
    # HTTP 传输（9529）
    # ------------------------------------------------------------------

    def _sync_http_get(self, path: str) -> tuple[int, str] | None:
        """同步 GET 请求，返回 (状态码, 响应体)."""
        conn = http.client.HTTPConnection(
            self._host, self._http_port, timeout=HTTP_TIMEOUT
        )
        try:
            conn.request("GET", path, headers={"Connection": "close", "Accept": "*/*"})
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException) as err:
            _LOGGER.debug("HTTP 请求失败（%s）：%s", path, err)
            return None
        else:
            return resp.status, body
        finally:
            conn.close()

    async def async_http_get(self, path: str) -> tuple[int, str] | None:
        """异步 GET 请求（串行化，避免设备并发处理异常）."""
        if not self._host:
            return None
        async with self._http_lock:
            return await self.hass.async_add_executor_job(self._sync_http_get, path)

    async def _async_http_probe(self) -> bool:
        """探测 HTTP 通道并读取设备信息（型号/MAC/固件）."""
        res = await self.async_http_get(HTTP_MODEL)
        if res is None:
            return False
        status, body = res
        if status != 200:
            _LOGGER.warning(
                "HTTP %s 返回 %s：%r", HTTP_MODEL, status, body.strip()[:120]
            )
            return False
        data = _parse_json(body)
        if not isinstance(data, dict):
            _LOGGER.warning(
                "HTTP 可达但 %s 响应不是 JSON：%r", HTTP_MODEL, body.strip()[:120]
            )
            return True
        if not {"model", "disModel", "net_mac"} & data.keys():
            _LOGGER.warning("HTTP 可达但响应缺少设备字段：%r", body.strip()[:120])
        if data.get("net_mac"):
            self.mac = str(data["net_mac"]).lower()
        model = data.get("disModel") or data.get("model")
        if model:
            self.model = str(model)
        if data.get("firmware"):
            self.version = str(data["firmware"])
        return True

    async def _async_http_send(self, name: str, code: str) -> tuple[bool, bool]:
        """通过 sendkey 下发按键，返回 (是否成功, 通道是否可达)."""
        key = HTTP_KEY_NAMES.get(name, code)
        res = await self.async_http_get(
            HTTP_SEND_KEY.format(key=urllib.parse.quote(key, safe="."))
        )
        if res is None:
            return False, False
        status, body = res
        if status != 200:
            _LOGGER.warning("HTTP 按键 %s 返回 %s：%r", key, status, body.strip()[:120])
            return False, True
        data = _parse_json(body)
        if isinstance(data, dict):
            if data.get("status") == 200 or data.get("code") == 200:
                return True, True
            _LOGGER.warning("HTTP 按键 %s 被拒绝：%r", key, body.strip()[:120])
            return False, True
        if body.strip().startswith("Status="):
            return True, True
        _LOGGER.warning("HTTP 按键 %s 响应无法识别：%r", key, body.strip()[:120])
        return False, True

    async def _async_http_poll(self) -> None:
        """HTTP 通道状态轮询：电源 + 播放状态，并补全标题/进度."""
        changed = False
        res = await self.async_http_get(HTTP_QUERY_POWER)
        if res is not None and self._apply_status_text(res[1]):
            changed = True
        res = await self.async_http_get(HTTP_QUERY_PLAY)
        if res is not None and self._apply_status_text(res[1]):
            changed = True
        res = await self.async_http_get(HTTP_VIDEO_STATUS)
        if res is not None:
            data = _parse_json(res[1])
            video = (data or {}).get("video")
            if isinstance(video, dict) and video.get("status") in (
                VIDEO_STATUS_PLAY,
                VIDEO_STATUS_PAUSE,
                VIDEO_STATUS_STOP,
            ):
                self._apply_video(video)
                changed = True
            else:
                # 未在播放视频时，尝试音乐状态
                music = await self.async_http_get(HTTP_MUSIC_STATE)
                if music is not None:
                    mdata = _parse_json(music[1])
                    if mdata is not None and self._apply_music(mdata):
                        changed = True
        if changed:
            async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED, self)

    # ------------------------------------------------------------------
    # 状态解析（三通道共用）
    # ------------------------------------------------------------------

    def _apply_status_text(self, text: str) -> bool:
        """解析 Status=Play; 等纯文本反馈（应答可能带 @QPW OK 前缀）."""
        changed = False
        for segment in text.replace("\r", "\n").split("\n"):
            segment = segment.strip().rstrip(";")
            if not segment.startswith("Status="):
                continue
            value = segment.removeprefix("Status=").rstrip(";\r\n")
            if value in STATUS_PLAYBACK:
                self.play_status = {"playing": 1, "paused": 2, "idle": -1}[
                    STATUS_PLAYBACK[value]
                ]
                changed = True
            elif value in STATUS_POWER:
                self.power_state = STATUS_POWER[value]
                changed = True
        return changed

    def _apply_video(self, video: dict[str, Any]) -> None:
        """应用 /VideoPlay/getPlayStatus 的 video 分组."""
        status = video.get("status")
        if status == VIDEO_STATUS_PLAY:
            self.play_status = 1
        elif status == VIDEO_STATUS_PAUSE:
            self.play_status = 2
        elif status == VIDEO_STATUS_STOP:
            self.play_status = -1
        if video.get("title"):
            self.title = str(video["title"])
        if video.get("currentPosition") is not None:
            self.position_ms = int(video["currentPosition"])
        if video.get("duration") is not None:
            self.duration_ms = int(video["duration"])
        self.play_type = 1

    def _apply_music(self, data: dict[str, Any]) -> bool:
        """应用 /MusicControl/v2/getState 响应（字段做多键容错）."""
        state = data.get("state")
        if not isinstance(state, dict):
            state = data
        changed = False
        status = state.get("playStatus")
        if isinstance(status, int) and status in (1, 2, -1):
            self.play_status = status
            changed = True
        title = state.get("currentTitle") or state.get("title")
        if title:
            self.title = str(title)
            changed = True
        position = state.get("positionMs")
        if position is None:
            position = state.get("currentPosition", state.get("position"))
        if isinstance(position, int):
            self.position_ms = position
            changed = True
        duration = state.get("durationMs")
        if duration is None:
            duration = state.get("duration", state.get("totalDuration"))
        if isinstance(duration, int):
            self.duration_ms = duration
            changed = True
        if changed:
            self.play_type = 2
        return changed

    @callback
    def _handle_line(self, line: str) -> None:
        """解析一行文本（TCP 状态 JSON / 文本状态），并通知实体."""
        if not line:
            return
        changed = False
        if line.startswith("{"):
            data = _parse_json(line)
            if data is None:
                return
            changed = self._apply_state_json(data)
        elif self._apply_status_text(line):
            changed = True
        if changed:
            async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED, self)

    def _apply_state_json(self, data: dict[str, Any]) -> bool:
        """应用 TCP 推送的完整状态 JSON（仅事件推送返回 False）."""
        changed = False
        if data.get("mac"):
            self.mac = str(data["mac"]).lower()
        if data.get("deviceName"):
            self.name = f"HDEngine {data['deviceName']}"
        if data.get("powerState"):
            self.power_state = str(data["powerState"])
            changed = True
        if isinstance(data.get("playStatus"), int):
            status = data["playStatus"]
            self.play_status = 1 if status == 1 else 2 if status == 2 else -1
            changed = True
        if "currentTitle" in data:
            self.title = data["currentTitle"] or None
            changed = True
        if isinstance(data.get("positionMs"), int):
            self.position_ms = data["positionMs"]
            changed = True
        if isinstance(data.get("durationMs"), int):
            self.duration_ms = data["durationMs"]
            changed = True
        if isinstance(data.get("playType"), int) and data["playType"]:
            self.play_type = data["playType"]
            changed = True
        return changed

    # ------------------------------------------------------------------
    # 串口传输（9600 8N1）
    # ------------------------------------------------------------------

    def _sync_serial_open(self) -> serial.Serial:
        """打开串口."""
        return serial.Serial(
            self._port, self._baudrate, timeout=SERIAL_TIMEOUT, write_timeout=1
        )

    def _sync_serial_command(self, code: str) -> str | None:
        r"""同步发送 #<码> \r，读取应答（@RHT ... / Status=...;）."""
        ser = self._serial
        if ser is None or not ser.is_open:
            return None
        try:
            ser.reset_input_buffer()
            ser.write(f"#{code} \r".encode())
            ser.flush()
            buf = read_serial_reply(ser, 0.6)
        except (serial.SerialException, OSError) as err:
            _LOGGER.debug("串口命令失败（%s）：%s", code, err)
            return None
        return buf or None

    def _sync_serial_close(self) -> None:
        """关闭串口."""
        ser, self._serial = self._serial, None
        if ser is not None:
            with contextlib.suppress(serial.SerialException, OSError):
                ser.close()

    def _sync_serial_probe(self) -> bool:
        r"""探测串口上是否为海缔力播放器（#QPW \r 应答 @QPW / Status=Pow...;）."""
        try:
            ser = self._sync_serial_open()
        except serial.SerialException, OSError:
            return False
        try:
            time.sleep(0.2)
            for _ in range(3):
                ser.reset_input_buffer()
                ser.write(f"#{CODE_QUERY_POWER} \r".encode())
                ser.flush()
                if is_hdengine_reply(read_serial_reply(ser, 1.2)):
                    return True
                time.sleep(0.3)
        except serial.SerialException, OSError:
            return False
        finally:
            with contextlib.suppress(serial.SerialException, OSError):
                ser.close()
        return False

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    async def async_connect(self) -> bool:
        """连接设备：TCP → HTTP → 串口."""
        if self._host:
            if await self._async_http_probe():
                self._available_http = True
                _LOGGER.info(
                    "HTTP 控制已就绪（%s:%s，型号 %s）",
                    self._host,
                    self._http_port,
                    self.model,
                )
            if await self._async_tcp_open():
                self._available_tcp = True
                _LOGGER.info("TCP 控制已就绪（%s:%s）", self._host, self._tcp_port)
                self._reader_task = self.hass.async_create_task(
                    self._async_tcp_reader_loop()
                )
        if self._available_tcp or self._available_http:
            return True
        if self._host:
            _LOGGER.warning(
                "网络通道均不可达（%s，TCP %s / HTTP %s），尝试串口降级",
                self._host,
                self._tcp_port,
                self._http_port,
            )
        if self._port:
            probed = await self.hass.async_add_executor_job(self._sync_serial_probe)
            if probed:
                self._serial = await self.hass.async_add_executor_job(
                    self._sync_serial_open
                )
                self._available_serial = True
                _LOGGER.info("RS-232 控制已就绪（%s）", self._port)
                return True
            # 兜底：串口能打开即视为可用
            try:
                self._serial = await self.hass.async_add_executor_job(
                    self._sync_serial_open
                )
            except serial.SerialException, OSError:
                _LOGGER.debug("串口 %s 打开失败", self._port)
            else:
                self._available_serial = True
                _LOGGER.info("RS-232 控制已就绪（%s，探测无响应）", self._port)
                return True
        return False

    async def async_send_command(self, name: str) -> bool:
        """按名称下发命令：TCP → HTTP → 串口."""
        code = COMMAND_CODES.get(name)
        if code is None:
            _LOGGER.debug("未知命令：%s", name)
            return False
        if self._available_tcp:
            # TCP 用 3 字符内部指令码最稳（KEY.* 键名依赖固件映射）
            if await self._async_tcp_send(code):
                return True
            self._available_tcp = False
            await self._async_tcp_close()
            _LOGGER.warning("TCP 通道断开，降级到 HTTP/串口")
        if self._available_http:
            ok, reachable = await self._async_http_send(name, code)
            if ok:
                return True
            if not reachable:
                self._available_http = False
                _LOGGER.warning("HTTP 通道不可达，降级到串口")
        if self._port:
            async with self._serial_lock:
                if self._serial is None or not self._serial.is_open:
                    with contextlib.suppress(serial.SerialException, OSError):
                        self._serial = await self.hass.async_add_executor_job(
                            self._sync_serial_open
                        )
                resp = await self.hass.async_add_executor_job(
                    self._sync_serial_command, code
                )
                if resp is not None:
                    if self._apply_status_text(resp):
                        async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED, self)
                    return "@RHT RE" not in resp
                self._available_serial = False
                await self.hass.async_add_executor_job(self._sync_serial_close)
        return False

    async def async_play_media(self, url: str) -> bool:
        """点播影片/音乐（TCP 或 HTTP 路由，二者共用 /MediaQuery/*）."""
        if not url:
            return False
        is_music = url.lower().split("?")[0].endswith(MUSIC_EXTENSIONS)
        route = (HTTP_PLAY_MUSIC if is_music else HTTP_PLAY_MEDIA).format(
            path=urllib.parse.quote(url, safe="/:.")
        )
        if self._available_tcp:
            if await self._async_tcp_send(route):
                return True
            self._available_tcp = False
            await self._async_tcp_close()
            _LOGGER.warning("TCP 通道断开，点播降级到 HTTP")
        if self._available_http:
            res = await self.async_http_get(route)
            if res is not None:
                _, body = res
                data = _parse_json(body)
                if data is not None:
                    return data.get("code") == 200 or data.get("status") == 200
        return False

    async def async_poll_state(self) -> None:
        """状态轮询：TCP 走空行查询；HTTP 查电源与播放状态；串口查询 QPW/QPS."""
        if self._available_tcp:
            resp = await self._async_tcp_command("")
            if resp is None:
                self._available_tcp = False
                await self._async_tcp_close()
            return
        if self._available_http:
            await self._async_http_poll()
            return
        if self._port and self._available_serial:
            changed = False
            async with self._serial_lock:
                for code in (CODE_QUERY_POWER, CODE_QUERY_PLAY):
                    resp = await self.hass.async_add_executor_job(
                        self._sync_serial_command, code
                    )
                    if resp and self._apply_status_text(resp):
                        changed = True
            if changed:
                async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED, self)

    async def async_close(self) -> None:
        """关闭全部通道."""
        self._closed = True
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._async_tcp_close()
        await self.hass.async_add_executor_job(self._sync_serial_close)

    @property
    def device_info(self) -> DeviceInfo:
        """设备信息."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.mac or self._host or self._port or "unknown")},
            name=self.name,
            manufacturer="HDEngine",
            model=self.model or "Media Player",
            sw_version=self.version,
        )


def _parse_json(body: str) -> dict[str, Any] | None:
    """容错解析 JSON 对象."""
    text = body.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


type HDEngineConfigEntry = ConfigEntry[HDEnginePlayer]


async def async_setup_entry(hass: HomeAssistant, entry: HDEngineConfigEntry) -> bool:
    """设置集成."""
    device = HDEnginePlayer(
        hass,
        entry.data.get(CONF_HOST),
        entry.data.get(CONF_PORT),
        entry.data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE),
    )
    if not await device.async_connect():
        await device.async_close()
        raise ConfigEntryNotReady(
            "无法连接海缔力播放器：TCP / HTTP / 串口均不可用（设备可能已关机，稍后自动重试）"
        )
    entry.runtime_data = device
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: HDEngineConfigEntry
) -> None:
    """配置更新时重载."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HDEngineConfigEntry) -> bool:
    """卸载集成."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_close()
    return unload_ok
