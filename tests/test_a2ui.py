import json
import sys

import pytest

from dingtalk_channel_sdk import Config, DingTalkChannel, DwsA2UIClient, serialize_a2ui_messages

MESSAGE = {"version": "v1.0", "updateDataModel": {"surfaceId": "sdk-card", "path": "/text", "value": '中文、引号"与 $(echo test) `test`'}}


def fixture(tmp_path, response=None, mode="ok", timeout_s=5):
    if response is None:
        response = {"ok": True, "outcome": "success", "data": {"success": True, "result": {"bizId": "server-biz"}}}
    script = tmp_path / "模拟 dws.py"
    trace = tmp_path / "trace.jsonl"
    script.write_text('''import json, sys, time
trace, response, mode, *args = sys.argv[1:]
with open(trace, "a", encoding="utf-8") as output:
    output.write(json.dumps(args, ensure_ascii=False) + "\\n")
if mode == "hang": time.sleep(10)
if mode == "fail":
    sys.stderr.write("不应暴露的凭据占位符")
    sys.exit(2)
sys.stdout.write(response)
''', encoding="utf-8")
    client = DwsA2UIClient(command=[sys.executable, str(script), str(trace), response if isinstance(response, str) else json.dumps(response), mode], profile="corp:user", timeout_s=timeout_s)
    return client, lambda: [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]


def test_对象和字符串均保留语义():
    encoded = json.dumps(MESSAGE, ensure_ascii=False)
    strings = json.loads(serialize_a2ui_messages([MESSAGE, encoded]))
    assert [json.loads(value) for value in strings] == [MESSAGE, MESSAGE]
    assert strings[1] == encoded


@pytest.mark.parametrize("messages", [[], {}, ["not-json"], [None], [{"version": "v0.8"}], [{"version": "v1.0"}], [{"version": "v1.0", "deleteSurface": {"surfaceId": ""}}], [{**MESSAGE, "deleteSurface": {"surfaceId": "x"}}]])
def test_无效信封在执行前拒绝(messages):
    with pytest.raises(ValueError):
        serialize_a2ui_messages(messages)


def test_超大参数在执行前拒绝():
    with pytest.raises(ValueError, match="64 KiB"):
        serialize_a2ui_messages([{**MESSAGE, "updateDataModel": {**MESSAGE["updateDataModel"], "value": "中" * 24000}}])


async def test_Channel显式通道发送单聊群聊并完成原卡片(tmp_path):
    client, calls = fixture(tmp_path)
    ch = DingTalkChannel(config=Config("unused", "unused", a2ui_client=client))
    result = await ch.send_a2ui_card({"open_dingtalk_id": "D-user"}, [MESSAGE])
    assert result.biz_id == "server-biz"
    assert result.update_warning is None
    assert result.receipt["data"]["result"]["bizId"] == "server-biz"
    await ch.send_a2ui_card({"conversation_id": "--group-value"}, [MESSAGE])
    await ch.update_a2ui_card(result.biz_id, [MESSAGE], "3")
    argv = calls()
    assert len(argv) == 3
    assert "--open-dingtalk-id=D-user" in argv[0]
    assert "--conversation-id=--group-value" in argv[1]
    assert "--biz-id=server-biz" in argv[2]
    assert "--flow-status=FINISH" in argv[2]
    for args in argv:
        assert "--profile=corp:user" in args and "--format=json" in args and "--yes" in args
        assert not any("client-secret" in arg for arg in args)
        content = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--content="))
        assert [json.loads(value) for value in json.loads(content)] == [MESSAGE]


async def test_未启用时明确拒绝():
    ch = DingTalkChannel(config=Config("unused", "unused"))
    with pytest.raises(RuntimeError, match="a2ui_client"):
        await ch.send_a2ui_card({"conversation_id": "cid"}, [MESSAGE])
    with pytest.raises(RuntimeError, match="a2ui_client"):
        await ch.update_a2ui_card("biz", [MESSAGE], "FINISH")


async def test_目标标识和状态错误不执行子进程():
    with pytest.raises(ValueError, match="一个 DWS Profile"):
        DwsA2UIClient(profile="corp:user,other:user")
    client = DwsA2UIClient(command=["不存在的 dws"])
    for target in [{}, {"user_id": "staff"}, {"conversation_id": "cid", "at_all": True}, {"conversation_id": "cid", "open_dingtalk_id": "D-user"}, {"open_dingtalk_id": ""}]:
        with pytest.raises(ValueError):
            await client.send_card(target, [MESSAGE])
    with pytest.raises(ValueError, match="bizId"):
        await client.update_card("", [MESSAGE], "FINISH")
    with pytest.raises(ValueError, match="flow_status"):
        await client.update_card("biz", [MESSAGE], "unknown")


async def test_请求侧bizCardId不作为更新标识且不重发(tmp_path):
    client, calls = fixture(tmp_path, {"success": True, "result": {"bizCardId": "request-only", "openTaskId": "task"}})
    result = await client.send_card({"conversation_id": "cid"}, [MESSAGE])
    assert result.biz_id is None and "不要自动重发" in result.update_warning
    assert result.receipt["result"]["bizCardId"] == "request-only"
    assert len(calls()) == 1


@pytest.mark.parametrize("response,mode", [({"success": False}, "ok"), ({"success": True, "result": {"success": False}}, "ok"), ("not-json", "ok"), ({}, "ok"), ({"result": {"bizId": "unconfirmed"}}, "ok"), ({"ok": True, "outcome": "success", "dry_run": True}, "ok"), ({}, "fail")])
async def test_失败回执和进程失败不泄露stderr(tmp_path, response, mode):
    client, calls = fixture(tmp_path, response, mode)
    with pytest.raises(RuntimeError) as error:
        await client.send_card({"conversation_id": "cid"}, [MESSAGE])
    assert "不应暴露" not in str(error.value)
    assert len(calls()) == 1


async def test_超时明确报告未知结果(tmp_path):
    client, _ = fixture(tmp_path, {}, "hang", timeout_s=0.2)
    with pytest.raises(RuntimeError, match="结果可能未知"):
        await client.send_card({"conversation_id": "cid"}, [MESSAGE])
