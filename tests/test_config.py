"""Config.transport 预留字段的行为（默认 stream、webhook 预留未实现、未知值报错）。"""

import pytest

from dingtalk_channel_sdk.config import (
    TRANSPORT_STREAM,
    TRANSPORT_HTTP,
    Config,
)


def _base(**kw):
    return Config(client_id="id", client_secret="sec", **kw)


def test_transport_defaults_to_stream():
    cfg = _base()
    assert cfg.transport == TRANSPORT_STREAM


def test_transport_webhook_accepted():
    cfg = _base(transport=TRANSPORT_HTTP)
    assert cfg.transport == TRANSPORT_HTTP
    assert cfg.http_timestamp_tolerance_s == 3600.0


def test_transport_unknown_rejected():
    with pytest.raises(ValueError, match="unknown transport"):
        _base(transport="grpc")
