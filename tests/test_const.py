"""协议常量测试."""

from custom_components.hdengine_player.const import (
    COMMAND_CODES,
    STATUS_PLAYBACK,
    STATUS_POWER,
)


def test_command_codes_format() -> None:
    """所有指令码为 3 字符大写字母数字组合."""
    for name, code in COMMAND_CODES.items():
        assert name
        assert len(code) == 3, f"{name} 的指令码 {code} 应为 3 字符"
        assert code.isalnum() and code.upper() == code


def test_command_names_unique() -> None:
    """指令名不重复（dict 天然去重，校验数量合理）."""
    assert len(COMMAND_CODES) >= 40


def test_core_commands_present() -> None:
    """媒体播放器核心命令必须存在."""
    for name in (
        "wake",
        "power_toggle",
        "play",
        "pause",
        "stop",
        "next",
        "previous",
        "volume_up",
        "volume_down",
        "mute",
    ):
        assert name in COMMAND_CODES


def test_status_maps() -> None:
    """状态反馈映射完整."""
    assert STATUS_PLAYBACK == {"Play": "playing", "Pause": "paused", "Stop": "idle"}
    assert STATUS_POWER == {"PowOn": "on", "PowStandby": "standby", "PowOff": "off"}
