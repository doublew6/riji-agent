"""Local voice reply generation.

The default implementation uses macOS ``say`` and keeps synthesis local. It
never sends journal-derived reply text to a cloud TTS provider.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Protocol

from riji_agent.voice.models import VoiceAttachment

_LOG = logging.getLogger("riji_agent.voice")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class VoiceReplyService(Protocol):
    def synthesize_reply(self, *, text: str, request_id: str) -> Optional[VoiceAttachment]:
        """Return a local audio attachment, or ``None`` when unavailable."""


class MacOSSayVoiceReplyService:
    """Generate local voice replies with macOS ``say``.

    When ``ffmpeg`` is available, the intermediate ``.m4a`` output is converted
    to Opus so Feishu can deliver it through its native audio message path.
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        voice: Optional[str] = None,
        max_chars: int = 1200,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._voice = voice.strip() if voice else None
        self._max_chars = max(1, int(max_chars))

    def synthesize_reply(self, *, text: str, request_id: str) -> Optional[VoiceAttachment]:
        say = shutil.which("say")
        if say is None:
            _LOG.warning("voice reply skipped: macOS say command is unavailable")
            return None

        content = text.strip()
        if not content:
            return None
        if len(content) > self._max_chars:
            content = content[: self._max_chars].rstrip() + "..."

        self._output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        safe_request = _SAFE_NAME_RE.sub("-", request_id).strip("-") or "reply"
        m4a_path = (self._output_dir / f"{safe_request}.m4a").resolve()
        opus_path = (self._output_dir / f"{safe_request}.opus").resolve()

        input_path: Optional[Path] = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._output_dir), prefix=f"{safe_request}.", suffix=".txt"
            )
            input_path = Path(tmp_name)
            with open(fd, "w", encoding="utf-8") as handle:
                handle.write(content)

            command = [say, "-o", str(m4a_path), "--file-format=m4af", "-f", str(input_path)]
            if self._voice:
                command[1:1] = ["-v", self._voice]
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            attachment = self._convert_to_opus_if_available(m4a_path=m4a_path, opus_path=opus_path)
        except Exception:
            _LOG.warning("voice reply synthesis failed", exc_info=True)
            return None
        finally:
            if input_path is not None:
                try:
                    input_path.unlink(missing_ok=True)
                except OSError:
                    _LOG.debug("voice reply temp input cleanup failed", exc_info=True)

        if attachment is None:
            return None
        return attachment

    def _convert_to_opus_if_available(
        self, *, m4a_path: Path, opus_path: Path
    ) -> Optional[VoiceAttachment]:
        if not m4a_path.is_file():
            return None

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            return VoiceAttachment(path=str(m4a_path), mime_type="audio/mp4")

        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(m4a_path),
                    "-vn",
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "32k",
                    str(opus_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            _LOG.warning("voice reply opus conversion failed; keeping m4a", exc_info=True)
            return VoiceAttachment(path=str(m4a_path), mime_type="audio/mp4")

        if not opus_path.is_file():
            return VoiceAttachment(path=str(m4a_path), mime_type="audio/mp4")

        try:
            m4a_path.unlink(missing_ok=True)
        except OSError:
            _LOG.debug("voice reply intermediate cleanup failed", exc_info=True)
        return VoiceAttachment(path=str(opus_path), mime_type="audio/ogg")
