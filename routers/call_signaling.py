"""Validation helpers for one-to-one WebRTC call signaling messages."""

from __future__ import annotations

from typing import Any

CALL_SIGNAL_TYPES = frozenset(
    {
        "call_request",
        "call_response",
        "call_signal",
        "call_ended",
        "call_state",
    }
)

CALL_SIGNAL_SUBTYPES = frozenset(
    {
        "offer",
        "answer",
        "candidate",
        "restart_request",
    }
)

MAX_SDP_LENGTH = 256_000
MAX_CANDIDATE_LENGTH = 8_192
MAX_REASON_LENGTH = 300


def is_call_signal_type(message_type: str | None) -> bool:
    return message_type in CALL_SIGNAL_TYPES


def validate_call_payload(message_type: str, payload: Any) -> tuple[bool, str | None]:
    """Validate untrusted call signaling payloads before forwarding them."""
    if message_type not in CALL_SIGNAL_TYPES:
        return False, "Unsupported call message type"
    if not isinstance(payload, dict):
        return False, "Call payload must be an object"

    target_id = payload.get("to")
    if not isinstance(target_id, str) or not target_id or len(target_id) > 80:
        return False, "Invalid call target"

    if message_type == "call_response" and not isinstance(payload.get("accepted"), bool):
        return False, "Call response must contain accepted=true/false"

    if message_type == "call_signal":
        signal_type = payload.get("signal_type")
        if signal_type not in CALL_SIGNAL_SUBTYPES:
            return False, "Unsupported call signal subtype"

        if signal_type in {"offer", "answer"}:
            sdp = payload.get("sdp")
            if not isinstance(sdp, dict):
                return False, "SDP must be an object"
            if sdp.get("type") != signal_type:
                return False, "SDP type does not match signal type"
            sdp_text = sdp.get("sdp")
            if not isinstance(sdp_text, str) or not sdp_text:
                return False, "SDP body is missing"
            if len(sdp_text) > MAX_SDP_LENGTH:
                return False, "SDP is too large"

        if signal_type == "candidate":
            candidate = payload.get("candidate")
            if not isinstance(candidate, dict):
                return False, "ICE candidate must be an object"
            candidate_text = candidate.get("candidate", "")
            if not isinstance(candidate_text, str):
                return False, "ICE candidate text is invalid"
            if len(candidate_text) > MAX_CANDIDATE_LENGTH:
                return False, "ICE candidate is too large"

    if message_type == "call_ended":
        reason = payload.get("reason")
        if reason is not None and (
            not isinstance(reason, str) or len(reason) > MAX_REASON_LENGTH
        ):
            return False, "Invalid call end reason"

    if message_type == "call_state":
        for key in ("speaking", "micEnabled", "cameraEnabled", "screenEnabled"):
            value = payload.get(key)
            if value is not None and not isinstance(value, bool):
                return False, f"{key} must be boolean"

    return True, None
