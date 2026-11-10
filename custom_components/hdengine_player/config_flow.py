"""HDEngine Player 配置流程."""

import asyncio
import http.client
import json
import logging
from pathlib import Path
import time
from typing import Any

import serial
from serial.tools import list_ports
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CODE_QUERY_POWER,
    CONF_BAUDRATE,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_BAUDRATE,
    DEFAULT_HTTP_PORT,
    DEFAULT_TCP_PORT,
    DOMAIN,
    HTTP_MODEL,
    HTTP_TIMEOUT,
    TCP_CONNECT_TIMEOUT,
    ZEROCONF_TYPE,
    is_hdengine_reply,
    read_serial_reply,
)

_LOGGER = logging.getLogger(__name__)

SERIAL_MANUAL = "manual"  # 串口列表中的“手动选择”占位项

# pyserial 在部分 HA OS 环境不会枚举板载 DB9 串口，这里补充常见 ttyS 节点
EXTRA_SERIAL_DEVICES = tuple(f"/dev/ttyS{i}" for i in range(8))


class HDEngineConfigFlow(ConfigFlow, domain=DOMAIN):
    """海缔力播放器配置流程."""

    VERSION = 1

    def __init__(self) -> None:
        """初始化."""
        self._discovered: list[dict[str, Any]] = []
        self._selected: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 串口探测
    # ------------------------------------------------------------------

    def _sync_list_serial_ports(self) -> list[str]:
        """列出本机串口（含 pyserial 未枚举到的板载 DB9 / ttyS 口）."""
        devices = {p.device for p in list_ports.comports()}
        devices.update(
            device for device in EXTRA_SERIAL_DEVICES if Path(device).exists()
        )
        return sorted(devices)

    def _sync_probe_candidates(self) -> list[str]:
        """自动识别候选串口：USB 转串口优先，其次板载 ttyS 口."""
        ports = list_ports.comports()
        _LOGGER.warning(
            "串口枚举: %s（应答含 @QPW/@RHT/Status= 即识别为海缔力）",
            [(p.device, p.description, p.vid, p.pid) for p in ports],
        )
        usb = sorted(p.device for p in ports if p.vid is not None)
        return usb + [
            device for device in self._sync_list_serial_ports() if device not in usb
        ]

    def _sync_probe_port(self, port: str, baudrate: int) -> dict[str, Any] | None:
        r"""发送 #QPW \\r 验证串口上是否为海缔力播放器（应答 @QPW / Status=Pow...;）."""
        received = ""
        try:
            with serial.Serial(port, baudrate, timeout=0.2) as ser:
                for attempt in range(3):
                    time.sleep(0.2 if attempt == 0 else 0.4)
                    ser.reset_input_buffer()
                    ser.write(f"#{CODE_QUERY_POWER} \r".encode())
                    ser.flush()
                    buf = read_serial_reply(ser, 1.2)
                    if buf:
                        received = buf
                    if is_hdengine_reply(buf):
                        _LOGGER.warning("串口 %s 识别为海缔力播放器：%r", port, buf)
                        return {"port": port, "baudrate": baudrate}
        except (serial.SerialException, OSError) as err:
            _LOGGER.warning("串口 %s 不可用，已跳过：%s", port, err)
            return None
        _LOGGER.warning(
            "串口 %s 未返回有效应答，收到：%r",
            port,
            received or "（无数据）",
        )
        return None

    def _sync_serial_openable(self, port: str, baudrate: int) -> bool:
        """兜底校验：串口能打开即可."""
        try:
            with serial.Serial(port, baudrate, timeout=1):
                return True
        except (serial.SerialException, OSError):
            return False

    async def _async_network_probe(self, host: str) -> dict[str, Any] | None:
        """探测网络通道，优先 HTTP getModel（含 MAC/型号），回退 TCP 首条推送."""
        http_res = await self.hass.async_add_executor_job(self._sync_http_model, host)
        if http_res:
            return http_res
        return await self._async_tcp_probe(host)

    def _sync_http_model(self, host: str) -> dict[str, Any] | None:
        """GET /ControlCenter/getModel 获取设备信息."""
        conn = http.client.HTTPConnection(host, DEFAULT_HTTP_PORT, timeout=HTTP_TIMEOUT)
        try:
            conn.request(
                "GET", HTTP_MODEL, headers={"Connection": "close", "Accept": "*/*"}
            )
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            if resp.status == 200 and isinstance(data, dict) and "model" in data:
                return {
                    "mac": data.get("net_mac"),
                    "name": data.get("disModel") or data.get("model"),
                    "firmware": data.get("firmware"),
                }
        except (OSError, ValueError, http.client.HTTPException) as err:
            _LOGGER.debug("HTTP 探测失败（%s）：%s", host, err)
        finally:
            conn.close()
        return None

    async def _async_tcp_probe(self, host: str) -> dict[str, Any] | None:
        """连接 TCP 9051 读取首条状态推送，用于取 MAC/型号."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, DEFAULT_TCP_PORT),
                timeout=TCP_CONNECT_TIMEOUT,
            )
            try:
                line = await asyncio.wait_for(
                    reader.readline(), timeout=TCP_CONNECT_TIMEOUT
                )
                if line:
                    data = json.loads(line.decode("utf-8", errors="replace"))
                    if isinstance(data, dict) and data.get("status") == 200:
                        return {
                            "mac": data.get("mac"),
                            "name": data.get("deviceName"),
                            "firmware": None,
                        }
            finally:
                writer.close()
                await writer.wait_closed()
        except (TimeoutError, OSError, ValueError) as err:
            _LOGGER.debug("TCP 探测失败（%s）：%s", host, err)
        return None

    # ------------------------------------------------------------------
    # 配置步骤
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """入口：自动识别串口或手动配置."""
        if user_input is not None:
            if user_input.get("auto_detect"):
                return await self.async_step_auto_detect()
            return await self.async_step_manual()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional("auto_detect", default=True): bool}),
        )

    async def async_step_auto_detect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """扫描串口，自动识别海缔力播放器."""
        ports = await self.hass.async_add_executor_job(self._sync_probe_candidates)
        if ports:
            _LOGGER.warning("开始扫描串口，候选: %s", ports)
        else:
            _LOGGER.warning(
                "未发现任何串口设备；请确认串口已接入并映射到 Home Assistant"
                "（Docker 需 --device=/dev/ttyS0 或 privileged），或改用 IP 控制"
            )
        for port in ports:
            result = await self.hass.async_add_executor_job(
                self._sync_probe_port, port, DEFAULT_BAUDRATE
            )
            if result:
                _LOGGER.info("在 %s 上发现海缔力播放器", port)
                self._selected = result
                return await self.async_step_host()
        _LOGGER.warning("未在串口上发现海缔力播放器，转为手动配置")
        return await self.async_step_manual()

    async def async_step_host(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """识别到串口设备后，选填 IP."""
        if user_input is not None:
            data: dict[str, Any] = {
                CONF_PORT: self._selected["port"],
                CONF_BAUDRATE: self._selected["baudrate"],
            }
            host = (user_input.get(CONF_HOST) or "").strip()
            if host:
                data[CONF_HOST] = host
            return await self._async_create(data)

        return self.async_show_form(
            step_id="host",
            data_schema=vol.Schema({vol.Optional(CONF_HOST): str}),
            description_placeholders={"name": "海缔力播放器"},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """手动配置：串口与 IP 至少填一个."""
        errors: dict[str, str] = {}
        ports = await self.hass.async_add_executor_job(self._sync_list_serial_ports)

        if user_input is not None:
            port = user_input.get(CONF_PORT) or ""
            if port == SERIAL_MANUAL:
                port = ""
            baudrate = int(user_input.get(CONF_BAUDRATE, DEFAULT_BAUDRATE))
            host = (user_input.get(CONF_HOST) or "").strip()

            if not port and not host:
                errors["base"] = "no_channels"
            else:
                if port:
                    probed = await self.hass.async_add_executor_job(
                        self._sync_probe_port, port, baudrate
                    )
                    if not probed:
                        openable = await self.hass.async_add_executor_job(
                            self._sync_serial_openable, port, baudrate
                        )
                        if not openable:
                            errors[CONF_PORT] = "cannot_connect_serial"
                        else:
                            _LOGGER.warning(
                                "串口 %s 探测无响应，但可打开，允许保存", port
                            )
                if not errors:
                    data: dict[str, Any] = {CONF_BAUDRATE: baudrate}
                    if port:
                        data[CONF_PORT] = port
                    if host:
                        data[CONF_HOST] = host
                    return await self._async_create(data)

        port_options = [
            {"value": SERIAL_MANUAL, "label": "不使用串口（仅 IP 控制）"}
        ] + [{"value": p, "label": p} for p in ports]
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_PORT, default=SERIAL_MANUAL): SelectSelector(
                        SelectSelectorConfig(
                            options=port_options, mode=SelectSelectorMode.DROPDOWN
                        )
                    ),
                    vol.Optional(
                        CONF_BAUDRATE, default=str(DEFAULT_BAUDRATE)
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=["9600", "19200", "38400", "57600", "115200"],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(CONF_HOST, default=""): str,
                }
            ),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: Any) -> ConfigFlowResult:
        """MDNS 自动发现（_xiaopai._tcp.）."""
        host = discovery_info.host
        mac = (discovery_info.properties or {}).get("mac", "")
        name = discovery_info.name.removesuffix(f".{ZEROCONF_TYPE}")
        if mac:
            await self.async_set_unique_id(mac.lower())
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        else:
            self._async_abort_entries_match({CONF_HOST: host})
        self._selected = {CONF_HOST: host, "name": name}
        self.context["title_placeholders"] = {"name": f"{name} ({host})"}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """确认添加发现的设备."""
        if user_input is not None:
            return await self._async_create({CONF_HOST: self._selected[CONF_HOST]})
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._selected["name"]},
        )

    # ------------------------------------------------------------------
    # 创建配置项
    # ------------------------------------------------------------------

    async def _async_create(self, data: dict[str, Any]) -> ConfigFlowResult:
        """取唯一 ID 并创建配置项."""
        host = data.get(CONF_HOST)
        name = "HDEngine Player"
        unique_id: str | None = None
        if host:
            info = await self._async_network_probe(host)
            if info:
                mac = info.get("mac")
                if mac:
                    unique_id = str(mac).lower()
                if info.get("firmware"):
                    _LOGGER.debug("固件版本：%s", info["firmware"])
        if unique_id is None:
            unique_id = data.get(CONF_PORT) or host
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=name, data=data)
