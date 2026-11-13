"""HDEngine Player 媒体播放器实体."""

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
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

SCAN_INTERVAL = timedelta(seconds=15)

PARALLEL_UPDATES = 1

# 海报墙节点的 media_content_id 约定：根节点用 POSTER_ROOT，
# 单个影片用 "poster|<path>"，便于播放时回查原始海报数据
POSTER_ROOT = "posters"
POSTER_PREFIX = "poster|"


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

    def __init__(self, device: HDEnginePlayer) -> None:
        """初始化."""
        self._device = device
        self._attr_unique_id = device.mac or device.host or device.port
        self._attr_device_info = device.device_info

        features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_MUTE
        )
        if device.host is not None:
            features |= (
                MediaPlayerEntityFeature.PLAY_MEDIA
                | MediaPlayerEntityFeature.BROWSE_MEDIA
            )
        self._attr_supported_features = features

        # 海报墙缓存：media_content_id -> MediaEntityDto
        self._posters: dict[str, dict[str, Any]] = {}

    @property
    def available(self) -> bool:
        """集成已加载即视为可用.

        设备关机/待机时通道无应答，若返回 False 会让 HA 禁用所有按钮，
        用户就无法再点「开机」唤醒设备，因此这里始终可用，
        由 state 显示 off 来表达设备当前状态。
        """
        return True

    @property
    def state(self) -> MediaPlayerState | None:
        """映射设备状态到 HA 状态."""
        if not self._device.is_available:
            return MediaPlayerState.OFF
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
                    "且设备 TCP 9051 / HTTP 9529 均无响应；请确认 IP 或补充串口"
                )
            if self._device.port and not self._device.host:
                raise HomeAssistantError(
                    f"发送命令 {name} 失败：串口 {self._device.port} 无响应"
                )
            raise HomeAssistantError(f"发送命令 {name} 失败：TCP / HTTP / 串口均无响应")

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

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """浏览设备上的海报墙（仅 IP 通道可用）."""
        if self._device.host is None:
            raise HomeAssistantError("浏览海报墙需要配置设备 IP 地址")

        if media_content_id == POSTER_ROOT:
            return await self._async_browse_posters()

        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id="root",
            media_content_type=MediaType.VIDEO,
            title=self.name or "HDEngine",
            can_play=False,
            can_expand=True,
            children=[
                BrowseMedia(
                    media_class=MediaClass.DIRECTORY,
                    media_content_id=POSTER_ROOT,
                    media_content_type=MediaType.VIDEO,
                    title="海报墙",
                    can_play=False,
                    can_expand=True,
                )
            ],
        )

    async def _async_browse_posters(self) -> BrowseMedia:
        """读取 queryMediaList 并转换成可播放的媒体条目."""
        # 先查电影，不够再查电视剧
        items = await self._device.async_get_poster_list(
            media_type="movie", page_size=50
        )
        self._posters = {}
        children: list[BrowseMedia] = []
        for index, item in enumerate(items):
            path = str(item.get("path") or f"item_{index}")
            media_id = f"{POSTER_PREFIX}{path}"
            if media_id in self._posters:
                media_id = f"{media_id}#{index}"
            self._posters[media_id] = item

            # 标题：优先中文名 title，其次剧名 name，再从 path 截最后一段
            title = str(
                item.get("title")
                or item.get("name")
                or path.rsplit("/", 1)[-1]
            ).strip()

            # 封面：优先 TMDB 海报 → 豆瓣海报；本地路径 HA 无法访问
            thumbnail = None
            for key in ("posterPath", "posterDouban"):
                url = item.get(key)
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    thumbnail = url
                    break

            # 标注电视剧
            media_class = MediaClass.EPISODE

            children.append(
                BrowseMedia(
                    media_class=media_class,
                    media_content_id=media_id,
                    media_content_type=MediaType.VIDEO,
                    title=title,
                    can_play=True,
                    can_expand=False,
                    thumbnail=thumbnail,
                )
            )

        if not items:
            return BrowseMedia(
                media_class=MediaClass.DIRECTORY,
                media_content_id=POSTER_ROOT,
                media_content_type=MediaType.VIDEO,
                title="海报墙（未开启刮削）",
                can_play=False,
                can_expand=True,
                children=[],
            )

        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id=POSTER_ROOT,
            media_content_type=MediaType.VIDEO,
            title=f"海报墙（{len(children)} 部）",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _async_play_poster(self, media_id: str) -> None:
        """播放海报墙里的影片."""
        item = self._posters.get(media_id)
        if item is None:
            await self._async_browse_posters()
            item = self._posters.get(media_id)
        if item is None:
            raise HomeAssistantError(f"海报墙里找不到该影片: {media_id}")
        path = str(item.get("path") or "")
        media_type = str(item.get("mediaType", "movie"))
        if not path:
            raise HomeAssistantError(f"海报条目缺少 path 字段: {media_id}")
        if not await self._device.async_play_media(path, media_type=media_type):
            raise HomeAssistantError(
                f"播放失败: {item.get('title') or item.get('name') or media_id}"
            )
        self._attr_state = MediaPlayerState.PLAYING
        self._attr_media_title = str(item.get("title") or item.get("name") or "")
        self._attr_media_content_id = media_id
        self._attr_media_content_type = MediaType.VIDEO
        self.async_write_ha_state()

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """点播影片/音乐（TCP 9051 或 HTTP 9529）."""
        if not media_id:
            raise HomeAssistantError("play_media 需要提供媒体地址（media_id）")
        if self._device.host is None:
            raise HomeAssistantError("串口通道不支持点播，请在配置中填写设备 IP")

        if media_id.startswith(POSTER_PREFIX):
            await self._async_play_poster(media_id)
            return

        if not await self._device.async_play_media(media_id):
            raise HomeAssistantError(
                "点播失败：设备 TCP/HTTP 通道不可达，或媒体路径不存在"
                "（路径需为设备可访问的 local:/// 等路径）"
            )
