"""Installer for the Hermes-side Feishu -> riji-agent bridge hook.

Hermes does not currently expose a stable plugin point for replacing the
Feishu message responder. Until that exists upstream, this installer makes the
required gateway hook reproducible and idempotent instead of relying on a
manual edit to ``~/.hermes/hermes-agent/gateway/run.py``.

The installed hook is deliberately small: it only runs when
``RIJI_AGENT_URL`` and ``HERMES_SHARED_SECRET`` are present, forwards the
Hermes/Feishu message contract to riji-agent over loopback HTTP, and returns
the riji-agent reply to Hermes for delivery.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

DEFAULT_GATEWAY_RUN_PATH = Path.home() / ".hermes" / "hermes-agent" / "gateway" / "run.py"

BEGIN_MARKER = "        # BEGIN riji-agent Hermes Feishu bridge"
END_MARKER = "        # END riji-agent Hermes Feishu bridge"
LEGACY_START = "        # riji-agent local privacy bridge."
ANCHOR = "        # Intercept messages that are responses to a pending /update prompt."


class HermesBridgeInstallError(RuntimeError):
    """Raised when the Hermes gateway file cannot be safely patched."""


@dataclass(frozen=True)
class HermesBridgeStatus:
    gateway_run: Path
    state: str

    @property
    def installed(self) -> bool:
        return self.state == "installed"


def status(gateway_run: Path = DEFAULT_GATEWAY_RUN_PATH) -> HermesBridgeStatus:
    """Return whether the riji-agent hook is installed in Hermes."""
    path = Path(gateway_run).expanduser()
    if not path.exists():
        return HermesBridgeStatus(path, "missing_file")
    text = path.read_text(encoding="utf-8")
    if BEGIN_MARKER in text and END_MARKER in text:
        return HermesBridgeStatus(path, "installed")
    if LEGACY_START in text:
        return HermesBridgeStatus(path, "legacy_patch")
    return HermesBridgeStatus(path, "not_installed")


def install(gateway_run: Path = DEFAULT_GATEWAY_RUN_PATH, *, backup: bool = True) -> HermesBridgeStatus:
    """Install or update the bridge hook in Hermes' gateway runner."""
    path = Path(gateway_run).expanduser()
    text = _read_gateway(path)
    updated = _install_text(text)
    if updated == text:
        return HermesBridgeStatus(path, "installed")
    if backup:
        _backup(path)
    path.write_text(updated, encoding="utf-8")
    return HermesBridgeStatus(path, "installed")


def uninstall(gateway_run: Path = DEFAULT_GATEWAY_RUN_PATH, *, backup: bool = True) -> HermesBridgeStatus:
    """Remove the managed hook. Legacy manual patches are left untouched."""
    path = Path(gateway_run).expanduser()
    text = _read_gateway(path)
    if BEGIN_MARKER not in text and END_MARKER not in text:
        return status(path)
    updated = _remove_marked_block(text)
    if backup:
        _backup(path)
    path.write_text(updated, encoding="utf-8")
    return status(path)


def _read_gateway(path: Path) -> str:
    if not path.exists():
        raise HermesBridgeInstallError(f"Hermes gateway file not found: {path}")
    if not path.is_file():
        raise HermesBridgeInstallError(f"Hermes gateway path is not a file: {path}")
    return path.read_text(encoding="utf-8")


def _install_text(text: str) -> str:
    block = _bridge_block()
    if BEGIN_MARKER in text or END_MARKER in text:
        return _replace_marked_block(text, block)

    anchor_idx = text.find(ANCHOR)
    if anchor_idx < 0:
        raise HermesBridgeInstallError(
            "Could not find Hermes gateway insertion point. "
            "Expected the update-prompt interception anchor."
        )

    legacy_idx = text.find(LEGACY_START)
    if 0 <= legacy_idx < anchor_idx:
        text = text[:legacy_idx] + text[anchor_idx:]
        anchor_idx = text.find(ANCHOR)

    return text[:anchor_idx] + block + "\n" + text[anchor_idx:]


def _replace_marked_block(text: str, block: str) -> str:
    start = text.find(BEGIN_MARKER)
    end = text.find(END_MARKER)
    if start < 0 or end < 0 or end < start:
        raise HermesBridgeInstallError("Existing bridge markers are malformed.")
    end = text.find("\n", end)
    if end < 0:
        end = len(text)
    else:
        end += 1
    replacement = (block + "\n") if block else ""
    return text[:start] + replacement + text[end:]


def _remove_marked_block(text: str) -> str:
    return _replace_marked_block(text, "")


def _backup(path: Path) -> Path:
    candidate = path.with_name(path.name + ".riji-agent.bak")
    if candidate.exists():
        for idx in range(1, 1000):
            numbered = path.with_name(f"{path.name}.riji-agent.bak.{idx}")
            if not numbered.exists():
                candidate = numbered
                break
    shutil.copy2(path, candidate)
    return candidate


def _bridge_block() -> str:
    return f"""{BEGIN_MARKER}
        # This hook is managed by `riji-agent hermes-bridge install`.
        # It keeps Feishu as transport only: diary-aware answering stays inside
        # the loopback-only riji-agent service.
        if (
            not is_internal
            and source.platform == Platform.FEISHU
            and os.getenv("RIJI_AGENT_URL")
            and os.getenv("HERMES_SHARED_SECRET")
        ):
            try:
                import httpx as _riji_httpx

                riji_chat_type = "p2p" if source.chat_type == "dm" else source.chat_type
                riji_event_id = (
                    event.message_id
                    or getattr(source, "message_id", None)
                    or getattr(event, "platform_update_id", None)
                    or f"hermes-feishu-{{uuid.uuid4().hex}}"
                )
                payload = {{
                    "event_id": str(riji_event_id),
                    "feishu_user_id": str(source.user_id or ""),
                    "chat_id": str(source.chat_id or ""),
                    "chat_type": str(riji_chat_type or ""),
                    "text": event.text or "",
                }}
                _riji_timeout = float(os.getenv("RIJI_AGENT_TIMEOUT_SECONDS", "240"))
                async with _riji_httpx.AsyncClient(
                    timeout=_riji_timeout,
                    trust_env=False,
                ) as _riji_client:
                    _riji_response = await _riji_client.post(
                        os.environ["RIJI_AGENT_URL"],
                        headers={{"X-Hermes-Secret": os.environ["HERMES_SHARED_SECRET"]}},
                        json=payload,
                    )
                if 200 <= _riji_response.status_code < 300:
                    _riji_data = _riji_response.json()
                    return str(_riji_data.get("reply") or "")
                if _riji_response.status_code in {{401, 403}}:
                    return "这条消息没有通过 riji-agent 的本地授权校验。"
                logger.warning(
                    "riji-agent bridge returned HTTP %s for Feishu message",
                    _riji_response.status_code,
                )
                return "riji-agent 暂时没有成功处理这条消息，请稍后再试。"
            except _riji_httpx.TimeoutException:
                logger.warning("riji-agent bridge timed out after %.1fs", _riji_timeout)
                return "riji-agent 正在处理这条较复杂的问题，但本次等待超时了。请稍后重试，或把问题拆短一点。"
            except Exception as _riji_bridge_exc:
                logger.warning("riji-agent bridge failed: %s", _riji_bridge_exc)
                return "riji-agent 暂时没有成功处理这条消息，请稍后再试。"
{END_MARKER}"""
