"""协议常量结构测试.

TODO(协议): 协议表填充后，补充具体键值/命令的断言（参照海美迪 test_const.py）.
"""

from custom_components.hdengine_player.const import (
    KEY_MAP,
    NAMED_COMMANDS,
    SERIAL_EVENT_STATE,
)


def test_command_tables_exist() -> None:
    """协议表必须是 dict 结构（当前允许为空占位）."""
    assert isinstance(KEY_MAP, dict)
    assert isinstance(NAMED_COMMANDS, dict)
    assert isinstance(SERIAL_EVENT_STATE, dict)


def test_command_names_do_not_collide() -> None:
    """键值命令与复合命令不能重名，否则选择器会重复."""
    assert not set(KEY_MAP) & set(NAMED_COMMANDS)


def test_named_command_shape() -> None:
    """复合命令为 (串口命令 | None, HTTP 路径 | None) 的二元组."""
    for name, pair in NAMED_COMMANDS.items():
        assert name
        serial_cmd, http_path = pair
        assert serial_cmd is None or isinstance(serial_cmd, str)
        assert http_path is None or http_path.startswith("/")
