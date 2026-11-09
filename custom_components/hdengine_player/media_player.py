"""HDEngine Player 媒体播放器实体."""

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SIGNAL_STATE_UPDATED, HDEngineConfigEntry, HDEnginePlayer

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HDEngineConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """设置媒体播放器."""
    device = entry.runtime_data
    async_add_entities([HDEngineMediaPlayer(device)])


class HDEngineMediaPlayer(MediaPlayerEntity):
    """海缔力媒体播放器."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:theater"
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.PLAY_MEDIA
    )

    def __init__(self, device: HDEnginePlayer) -> None:
        """初始化."""
        self._device = device
        self._attr_unique_id = device.mac or device.host or device.port
        self._attr_device_info = device.device_info

    @property
    def available(self) -> bool:
        """任一通道在线即视为可用."""
        return self._device.is_available

    @property
    def state(self) -> MediaPlayerState | None:
        """映射设备状态到 HA 状态."""
        if not self._device.is_available:
            return None
        power = self._device.power_state
        if power in ("off", "standby"):
            return MediaPlayerState.OFF
        play = self._device.play_status
        if play == 1:
            return MediaPlayerState.PLAYING
        if play == 2:
            return MediaPlayerState.PAUSED
        if play == -1:
            return MediaPlayerState.IDLE
        return MediaPlayerState.ON

    @property
    def media_content_type(self) -> MediaType | None:
        """当前媒体类型."""
        if self._device.play_type == 2:
            return MediaType.MUSIC
        if self._device.play_type == 1:
            return MediaType.VIDEO
        return None

    @property
    def media_title(self) -> str | None:
        """当前播放标题."""
        return self._device.title

    @property
    def media_position(self) -> int | None:
        """当前播放进度（秒）."""
        if self._device.position_ms is None:
            return None
        return self._device.position_ms // 1000

    async def async_added_to_hass(self) -> None:
        """订阅设备状态推送."""

        @callback
        def _state_updated(device: HDEnginePlayer) -> None:
            if device is self._device:
                self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_STATE_UPDATED, _state_updated)
        )

    async def async_update(self) -> None:
        """轮询：TCP 走保活查询，串口走状态查询."""
        await self._device.async_poll_state()

    async def _send(self, name: str) -> None:
        """下发命令，失败时抛出可读错误."""
        if not await self._device.async_send_command(name):
            if self._device.host and not self._device.port:
                raise HomeAssistantError(
                    f"发送命令 {name} 失败：当前仅配置了 IP，"
                    "且设备 TCP 服务不可达；请确认 IP 或补充串口"
                )
            if self._device.port and not self._device.host:
                raise HomeAssistantError(
                    f"发送命令 {name} 失败：串口 {self._device.port} 无响应"
                )
            raise HomeAssistantError(f"发送命令 {name} 失败：TCP 与串口均无响应")

    async def async_turn_on(self) -> None:
        """唤醒."""
        await self._send("wake")

    async def async_turn_off(self) -> None:
        """待机."""
        await self._send("power_toggle")

    async def async_media_play(self) -> None:
        """播放."""
        await self._send("play")

    async def async_media_pause(self) -> None:
        """暂停."""
        await self._send("pause")

    async def async_media_stop(self) -> None:
        """停止."""
        await self._send("stop")

    async def async_media_next_track(self) -> None:
        """下一曲."""
        await self._send("next")

    async def async_media_previous_track(self) -> None:
        """上一曲."""
        await self._send("previous")

    async def async_volume_up(self) -> None:
        """音量+."""
        await self._send("volume_up")

    async def async_volume_down(self) -> None:
        """音量-."""
        await self._send("volume_down")

    async def async_mute_volume(self, mute: bool) -> None:
        """静音切换."""
        await self._send("mute")

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """点播影片/音乐（仅 TCP 通道支持）."""
        if not media_id:
            raise HomeAssistantError("play_media 需要提供媒体地址（media_id）")
        if not self._device.host:
            raise HomeAssistantError("串口通道不支持点播，请在配置中填写设备 IP")
        if not await self._device.async_play_media(media_id):
            raise HomeAssistantError(
                "点播失败：设备 TCP 通道不可达，或媒体路径不存在"
                "（路径需为设备可访问的 local:/// 等路径）"
            )
