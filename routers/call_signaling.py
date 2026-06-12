"""Validation helpers for chat call signaling messages."""

CALL_SIGNAL_TYPES = {
    "call_request",
    "call_response",
    "call_signal",
    "call_ended",
    "call_state",
}


def is_call_signal_type(message_type: str | None) -> bool:
    return message_type in CALL_SIGNAL_TYPES