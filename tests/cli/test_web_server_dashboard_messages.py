from hermes_cli.web_server import _normalize_dashboard_message_roles


def test_normalize_dashboard_message_roles_labels_vision_preamble_as_tool():
    messages = [
        {
            "role": "user",
            "content": (
                "[The user sent an image~ Here's what I can see:\n"
                "A cat on a chair.]\n"
                "[If you need a closer look, use vision_analyze with image_url: /tmp/cat.png ~]"
            ),
            "tool_name": None,
        }
    ]

    normalized = _normalize_dashboard_message_roles(messages)

    assert normalized[0]["role"] == "tool"
    assert normalized[0]["tool_name"] == "vision_analyze"
    # The dashboard normalization must not mutate SessionDB's original rows.
    assert messages[0]["role"] == "user"
    assert messages[0]["tool_name"] is None


def test_normalize_dashboard_message_roles_leaves_plain_user_messages_alone():
    messages = [{"role": "user", "content": "please describe this image"}]

    normalized = _normalize_dashboard_message_roles(messages)

    assert normalized == messages
    assert normalized[0] is not messages[0]
