from gateway.platforms.base import SendResult
from gateway.run import _agent_result_get, _messages_from_agent_result


def test_agent_result_get_uses_dict_values_and_defaults_for_delivery_results():
    delivery_result = SendResult(success=True, message_id="telegram-1")

    assert _agent_result_get({"interrupted": True}, "interrupted") is True
    assert _agent_result_get(delivery_result, "interrupted") is None
    assert _agent_result_get(delivery_result, "final_response", "") == ""


def test_messages_from_agent_result_uses_dict_messages():
    fallback = [{"role": "user", "content": "old"}]
    messages = [{"role": "assistant", "content": "new"}]

    assert _messages_from_agent_result({"messages": messages}, fallback) is messages


def test_messages_from_agent_result_falls_back_for_send_result():
    fallback = [{"role": "user", "content": "queued"}]
    delivery_result = SendResult(success=True, message_id="telegram-1")

    assert _messages_from_agent_result(delivery_result, fallback) is fallback


def test_messages_from_agent_result_falls_back_when_messages_missing_or_invalid():
    fallback = [{"role": "user", "content": "queued"}]

    assert _messages_from_agent_result({}, fallback) is fallback
    assert _messages_from_agent_result({"messages": None}, fallback) is fallback
    assert _messages_from_agent_result({"messages": "not-a-list"}, fallback) is fallback
