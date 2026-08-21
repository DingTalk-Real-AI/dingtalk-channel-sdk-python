"""按消息类型拆分的内容转换器。

每个 converter 输入归一化后的 content dict，输出 (text, resources, mentions)。
新增消息类型时：新增 converter 模块并在 normalize.message.parse_content 分发。
"""

from .card import convert_action_card, convert_interactive_card
from .media import convert_audio, convert_file, convert_picture, convert_video
from .reply import convert_reply
from .richtext import convert_rich_text
from .text import convert_markdown, convert_text

__all__ = [
    "convert_text",
    "convert_markdown",
    "convert_rich_text",
    "convert_picture",
    "convert_file",
    "convert_audio",
    "convert_video",
    "convert_action_card",
    "convert_interactive_card",
    "convert_reply",
]
