"""E10：Markdown 归一化规则。"""

from dingtalk_channel_sdk import normalize_for_card


def test_normalize_for_card():
    assert normalize_for_card("a\nb") == "a<br>b"
    assert normalize_for_card("a\n\nb") == "a\n\nb"
    assert normalize_for_card("```\nx\ny\n```") == "```\nx\ny\n```"
    assert normalize_for_card("- a\n- b") == "- a\n- b"
    assert normalize_for_card("# T\nbody") == "# T<br>body"
    assert normalize_for_card("body\n# T") == "body\n# T"
    assert normalize_for_card("a\n> q1\n> q2\nb") == "a<br>> q1<br>q2<br>b"
    assert (
        normalize_for_card("x\n| a | b |\n| -- | -- |\n| 1 | 2 |")
        == "x\n\n| a | b |\n| -- | -- |\n| 1 | 2 |"
    )
