"""Hermes-side group transport hook; does not load journal or model code."""

from __future__ import annotations

GROUP_BEGIN_MARKER = "        # BEGIN riji-agent Hermes group bridge"
GROUP_END_MARKER = "        # END riji-agent Hermes group bridge"


def group_bridge_block() -> str:
    """Keep group transport separate from the existing private-chat bridge."""
    return '''        # BEGIN riji-agent Hermes group bridge
        # Preserve the original SDK identity and let the backend own delivery.
        if (
            not is_internal
            and source.platform == Platform.FEISHU
            and source.chat_type not in {"dm", "p2p"}
            and (os.getenv("RIJI_AGENT_URL") or os.getenv("HERMES_SHARED_SECRET"))
        ):
            try:
                if not os.getenv("RIJI_AGENT_URL") or not os.getenv("HERMES_SHARED_SECRET"):
                    return None
                import json as _riji_group_json
                import httpx as _riji_group_httpx
                from urllib.parse import urlsplit as _riji_group_urlsplit

                _riji_group_url = _riji_group_urlsplit(os.environ["RIJI_AGENT_URL"])
                if (
                    _riji_group_url.scheme != "http"
                    or _riji_group_url.hostname != "127.0.0.1"
                    or _riji_group_url.port != 8765
                    or _riji_group_url.path != "/hermes/messages"
                    or _riji_group_url.username is not None
                    or _riji_group_url.password is not None
                    or _riji_group_url.query
                    or _riji_group_url.fragment
                ):
                    return None
                _riji_group_raw = getattr(event, "raw_message", None)
                if not isinstance(_riji_group_raw, dict):
                    from lark_oapi import JSON as _riji_group_codec
                    _riji_group_raw = _riji_group_json.loads(
                        _riji_group_codec.marshal(_riji_group_raw)
                    )
                if (
                    not isinstance(_riji_group_raw, dict)
                    or not isinstance(_riji_group_raw.get("header"), dict)
                    or not isinstance(_riji_group_raw.get("event"), dict)
                    or len(_riji_group_json.dumps(_riji_group_raw).encode()) > 100000
                ):
                    return None
                async with _riji_group_httpx.AsyncClient(
                    timeout=20.0, trust_env=False, follow_redirects=False,
                ) as _riji_group_client:
                    _riji_group_response = await _riji_group_client.post(
                        "http://127.0.0.1:8765/api/mentors/v1/host-events",
                        headers={"X-Hermes-Secret": os.environ["HERMES_SHARED_SECRET"]},
                        json={"raw_event": _riji_group_raw},
                    )
                if not 200 <= _riji_group_response.status_code < 300:
                    logger.warning("riji-agent group ingress rejected the event")
            except Exception:
                # Group failures never expose request data or enter the old model.
                logger.warning("riji-agent group ingress unavailable")
            return None
        # END riji-agent Hermes group bridge'''


def remove_group_block(text: str) -> str:
    """Remove only our exact marked block, retaining private-route extensions."""
    from riji_agent.integrations.hermes_installer import HermesBridgeInstallError

    if GROUP_BEGIN_MARKER not in text and GROUP_END_MARKER not in text:
        return text
    if text.count(GROUP_BEGIN_MARKER) != 1 or text.count(GROUP_END_MARKER) != 1:
        raise HermesBridgeInstallError("Existing group bridge markers are malformed.")
    start, end = text.index(GROUP_BEGIN_MARKER), text.index(GROUP_END_MARKER)
    if end < start:
        raise HermesBridgeInstallError("Existing group bridge markers are malformed.")
    end = text.find("\n", end)
    return text[:start] + text[len(text) if end < 0 else end + 1:]
