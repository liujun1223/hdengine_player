r"""HDEngine Player 集成（RS-232 + TCP 双通道）.

- TCP：9051 长连接，连接后服务端推送完整状态 JSON，状态变化主动广播；
  断线自动重连（退避 TCP_RETRY_INTERVAL 秒）
- RS-232：9600 8N1，命令帧 #<3字符码> \\r，应答 @RHT OK\\r / @RHT RE\\r，
  状态反馈为 Status=...; 纯文本
- 双通道：优先走 TCP，不可达时降级串口
"""

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

import serial

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    CODE_QUERY_PLAY,
    CODE_QUERY_POWER,
    COMMAND_CODES,
    CONF_BAUDRATE,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_BAUDRATE,
    DEFAULT_TCP_PORT,
    DOMAIN,
    MUSIC_EXTENSIONS,
    SERIAL_TIMEOUT,
    STATUS_PLAYBACK,
    STATUS_POWER,
    TCP_COMMAND_TIMEOUT,
    TCP_CONNECT_TIMEOUT,
    TCP_PLAY_MEDIA,
    TCP_PLAY_MUSIC,
    TCP_READ_TIMEOUT,
    TCP_RETRY_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.MEDIA_PLAYER, Platform.SELECT]

# 状态推送信号
SIGNAL_STATE_UPDATED = f"{DOMAIN}_state_updated"


class HDEnginePlayer:
    """海缔力播放器 API，同时封装 RS-232 串口与 TCP 长连接两种通路."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str | None,
        port: str | None,
        baudrate: int = DEFAULT_BAUDRATE,
        tcp_port: int = DEFAULT_TCP_PORT,
    ) -> None:
        """初始化."""
        self.hass = hass
        self._host = host
        self._tcp_port = tcp_port
        self._port = port
        self._baudrate = baudrate
        self.name = "HDEngine Player"
        self.mac: str | None = None
        self.version: str | None = None
        self._available_serial = False
        self._available_tcp = False
        self._tcp_next_retry = 0.0
        self._serial: serial.Serial | None = None
        self._serial_lock = asyncio.Lock()
        self._tcp_lock = asyncio.Lock()
        self._tcp_reader: asyncio.StreamReader | None = None
        self._tcp_writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False
        # 播放状态（TCP 推送 / 串口查询更新）
        self.play_status: int | None = None  # -1 停止 / 1 播放 / 2 暂停
        self.power_state: str | None = None  # on / standby / off
        self.title: str | None = None
        self.position_ms: int | None = None
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
        return self._available_tcp or self._available_serial

    @property
    def uses_tcp(self) -> bool:
        """TCP 通道当前可用."""
        return self._available_tcp

    # ------------------------------------------------------------------
    # TCP 传输（9051 长连接，行分隔）
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
            self._handle_tcp_line(line.decode("utf-8", errors="replace").strip())
        except (TimeoutError, OSError) as err:
            _LOGGER.debug("TCP 连接失败（%s:%s）：%s", self._host, self._tcp_port, err)
            await self._async_tcp_close()
            return False
        else:
            return True

    async def _async_tcp_close(self) -> None:
        """关闭 TCP 连接."""
        writer, self._tcp_writer = self._tcp_writer, None
        self._tcp_reader = None
        if writer is not None:
            with contextlib.suppress(OSError):
                writer.close()
                await writer.wait_closed()

    @callback
    def _handle_tcp_line(self, line: str) -> None:
        """解析一行 TCP 消息（状态 JSON 推送或 Status= 文本），并通知实体."""
        if not line:
            return
        changed = False
        if line.startswith("Status="):
            value = line.removeprefix("Status=").rstrip(";\r\n")
            if value in STATUS_PLAYBACK:
                self.play_status = {"playing": 1, "paused": 2, "idle": -1}[
                    STATUS_PLAYBACK[value]
                ]
                changed = True
            elif value in STATUS_POWER:
                self.power_state = STATUS_POWER[value]
                changed = True
        elif line.startswith("{"):
            try:
                data: dict[str, Any] = json.loads(line)
            except ValueError:
                return
            if "mac" in data:
                self.mac = data.get("mac")
            if data.get("deviceName"):
                self.name = data["deviceName"]
            if "firmware" in data:
                self.version = data.get("firmware")
            if "playStatus" in data:
                self.play_status = data["playStatus"]
                changed = True
            if "powerState" in data:
                self.power_state = data["powerState"]
                changed = True
            if "currentTitle" in data:
                self.title = data["currentTitle"] or None
                changed = True
            if "positionMs" in data:
                self.position_ms = data["positionMs"]
                changed = True
            if "playType" in data:
                self.play_type = data["playType"]
        if changed:
            async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED, self)

    async def _async_tcp_reader_loop(self) -> None:
        """后台读取任务：处理状态推送，断线时按退避重连."""
        while not self._closed:
            reader = self._tcp_reader
            if reader is None:
                break
            try:
                line = await asyncio.wait_for(reader.readline(), TCP_READ_TIMEOUT)
            except (TimeoutError, OSError) as err:
                _LOGGER.debug("TCP 读取异常：%s", err)
                line = b""
            if not line:
                # 断线
                self._mark_tcp_down("连接断开或读取超时")
                await self._async_tcp_close()
                await asyncio.sleep(TCP_RETRY_INTERVAL)
                if self._closed or not self._host:
                    break
                _LOGGER.debug("尝试重连 TCP（%s:%s）", self._host, self._tcp_port)
                if await self._async_tcp_open():
                    self._available_tcp = True
                    _LOGGER.info("TCP 控制已恢复（%s）", self._host)
                    async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED, self)
                continue
            self._handle_tcp_line(line.decode("utf-8", errors="replace").strip())

    async def _async_tcp_command(self, command: str) -> str | None:
        """发送一行命令并等待一条响应行（推送消息会先处理掉）.

        返回响应行内容；超时/失败返回 None。
        """
        async with self._tcp_lock:
            if self._tcp_writer is None or self._tcp_reader is None:
                return None
            try:
                self._tcp_writer.write(command.encode("utf-8") + b"\n")
                await self._tcp_writer.drain()
                deadline = time.monotonic() + TCP_COMMAND_TIMEOUT
                while time.monotonic() < deadline:
                    line = await asyncio.wait_for(
                        self._tcp_reader.readline(),
                        timeout=max(0.1, deadline - time.monotonic()),
                    )
                    if not line:
                        return None
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    # 推送消息（含多字段状态 JSON）照常更新状态，并作为响应返回
                    self._handle_tcp_line(text)
                    return text
            except (TimeoutError, OSError) as err:
                _LOGGER.debug("TCP 命令失败（%s）：%s", command, err)
                return None
        return None

    def _mark_tcp_down(self, reason: str) -> None:
        """TCP 通道不可用."""
        was_available = self._available_tcp
        self._available_tcp = False
        self._tcp_next_retry = time.monotonic() + TCP_RETRY_INTERVAL
        if was_available:
            _LOGGER.warning(
                "TCP 控制不可达（%s:%s）[%s]，%s 秒内改走串口/重试",
                self._host,
                self._tcp_port,
                reason,
                TCP_RETRY_INTERVAL,
            )
            async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED, self)

    # ------------------------------------------------------------------
    # 串口传输（9600 8N1，#XXX \r 帧）
    # ------------------------------------------------------------------

    def _sync_serial_open(self) -> serial.Serial:
        """打开串口."""
        return serial.Serial(
            self._port, self._baudrate, timeout=SERIAL_TIMEOUT, write_timeout=1
        )

    def _sync_serial_command(self, code: str) -> str | None:
        r"""同步发送 #<码> \r，读取一行响应（@RHT ... / Status=...;）."""
        ser = self._serial
        if ser is None or not ser.is_open:
            return None
        try:
            ser.reset_input_buffer()
            ser.write(f"#{code} \r".encode())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith(("@RHT", "Status=")):
                    return line
        except (serial.SerialException, OSError) as err:
            _LOGGER.debug("串口命令失败（%s）：%s", code, err)
            return None
        else:
            return None

    def _sync_serial_close(self) -> None:
        """关闭串口."""
        ser, self._serial = self._serial, None
        if ser is not None:
            with contextlib.suppress(serial.SerialException, OSError):
                ser.close()

    def _sync_serial_probe(self) -> bool:
        r"""探测串口上是否为海缔力播放器（#QPW \r 应返回 Status=Pow...;）."""
        try:
            ser = self._sync_serial_open()
        except (serial.SerialException, OSError):
            return False
        try:
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write(f"#{CODE_QUERY_POWER} \r".encode())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                line = ser.readline().decode("utf-8", errors="replace").strip()
                if line.startswith(("Status=Pow", "@RHT")):
                    return True
        except (serial.SerialException, OSError):
            return False
        else:
            return False
        finally:
            with contextlib.suppress(serial.SerialException, OSError):
                ser.close()

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    async def async_connect(self) -> bool:
        """连接设备：优先 TCP，失败降级串口."""
        if self._host:
            if await self._async_tcp_open():
                self._available_tcp = True
                _LOGGER.info("TCP 控制已就绪（%s:%s）", self._host, self._tcp_port)
                self._reader_task = self.hass.async_create_task(
                    self._async_tcp_reader_loop()
                )
                return True
            _LOGGER.warning(
                "TCP 端口不可达（%s:%s）（设备可能已关机），尝试串口降级",
                self._host,
                self._tcp_port,
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
            except (serial.SerialException, OSError):
                _LOGGER.debug("串口 %s 打开失败", self._port)
            else:
                self._available_serial = True
                _LOGGER.info("RS-232 控制已就绪（%s，探测无响应）", self._port)
                return True
        return False

    async def async_send_command(self, name: str) -> bool:
        """按名称下发指令码，优先 TCP，失败降级串口."""
        code = COMMAND_CODES.get(name)
        if code is None:
            _LOGGER.debug("未知命令：%s", name)
            return False
        return await self._async_send_code(code)

    async def _async_send_code(self, code: str) -> bool:
        """发送 3 字符指令码."""
        if self._available_tcp:
            resp = await self._async_tcp_command(code)
            if resp is not None:
                if "@RHT RE" in resp or '"status":4' in resp:
                    return False
                return True
            self._mark_tcp_down("命令无响应")
            await self._async_tcp_close()
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
                    if resp.startswith("Status="):
                        self._handle_tcp_line(resp)
                        async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED, self)
                    return "@RHT RE" not in resp
                self._available_serial = False
                await self.hass.async_add_executor_job(self._sync_serial_close)
        return False

    async def async_play_media(self, url: str) -> bool:
        """通过 TCP 路由点播影片/音乐."""
        if not self._available_tcp:
            return False
        lower = url.lower().split("?")[0]
        is_music = lower.endswith(MUSIC_EXTENSIONS)
        route = (TCP_PLAY_MUSIC if is_music else TCP_PLAY_MEDIA).format(path=url)
        resp = await self._async_tcp_command(route)
        if resp is None:
            self._mark_tcp_down("点播无响应")
            await self._async_tcp_close()
            return False
        return '"code":200' in resp or '"status":200' in resp

    async def async_poll_state(self) -> None:
        """状态轮询.

        TCP 可用时依赖长连接推送，仅做空行保活查询；串口通道查询播放/电源状态。
        """
        if self._available_tcp:
            # 空行查询完整状态，同时作为连接保活
            resp = await self._async_tcp_command("")
            if resp is None:
                self._mark_tcp_down("保活查询无响应")
                await self._async_tcp_close()
            return
        if self._port and self._available_serial:
            async with self._serial_lock:
                for code in (CODE_QUERY_POWER, CODE_QUERY_PLAY):
                    resp = await self.hass.async_add_executor_job(
                        self._sync_serial_command, code
                    )
                    if resp and resp.startswith("Status="):
                        self._handle_tcp_line(resp)
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
            model="Media Player",
            sw_version=self.version,
        )


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
            "无法连接海缔力播放器：TCP 与串口均不可用（设备可能已关机，稍后自动重试）"
        )
    entry.runtime_data = device
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: HDEngineConfigEntry) -> None:
    """配置更新时重载."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HDEngineConfigEntry) -> bool:
    """卸载集成."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_close()
    return unload_ok
