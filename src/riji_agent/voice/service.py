"""Local voice reply generation.

The default implementation uses macOS ``say`` and keeps synthesis local. It
never sends journal-derived reply text to a cloud TTS provider.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import FrozenSet, Optional, Protocol

from riji_agent.voice.models import VoiceAttachment

_LOG = logging.getLogger("riji_agent.voice")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_FFMPEG_FALLBACKS = (Path("/opt/homebrew/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg"))
_SAY_FALLBACKS = (Path("/usr/bin/say"),)


class VoiceReplyService(Protocol):
    provider_id: str

    def synthesize_reply(
        self, *, text: str, request_id: str, voice: Optional[str] = None
    ) -> Optional[VoiceAttachment]:
        """Return a local audio attachment, or ``None`` when unavailable."""


class MacOSSayVoiceReplyService:
    """Generate local voice replies with macOS ``say``.

    When ``ffmpeg`` is available, the intermediate ``.m4a`` output is converted
    to Opus so Feishu can deliver it through its native audio message path.
    """

    provider_id = "macos_say"

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

    def synthesize_reply(
        self, *, text: str, request_id: str, voice: Optional[str] = None
    ) -> Optional[VoiceAttachment]:
        say = _find_executable("say", _SAY_FALLBACKS)
        if say is None:
            _LOG.warning("voice reply skipped: macOS say command is unavailable")
            return None
        selected_voice = self._select_voice(say, voice)

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
            if selected_voice:
                command[1:1] = ["-v", selected_voice]
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            attachment = _convert_to_opus_if_available(
                source_path=m4a_path,
                opus_path=opus_path,
                fallback_mime_type="audio/mp4",
            )
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

    def _select_voice(self, say: str, voice: Optional[str]) -> Optional[str]:
        available = _available_say_voices(say)
        for candidate in (voice, self._voice):
            if not candidate:
                continue
            candidate = candidate.strip()
            if not available or candidate in available:
                return candidate
            _LOG.warning("voice reply requested unavailable macOS voice: %s", candidate)
        return None

class MeloTTSVoiceReplyService:
    """Generate local voice replies with the optional MeloTTS package."""

    provider_id = "melotts"

    def __init__(
        self,
        output_dir: Path,
        *,
        language: str = "ZH",
        speaker: Optional[str] = None,
        device: str = "auto",
        speed: float = 1.0,
        max_chars: int = 1200,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._language = language.strip().upper() or "ZH"
        self._speaker = speaker.strip() if speaker else None
        self._device = device.strip() or "auto"
        self._speed = max(0.1, float(speed))
        self._max_chars = max(1, int(max_chars))
        self._model = None
        self._speaker_ids = None

    def synthesize_reply(
        self, *, text: str, request_id: str, voice: Optional[str] = None
    ) -> Optional[VoiceAttachment]:
        content = text.strip()
        if not content:
            return None
        if len(content) > self._max_chars:
            content = content[: self._max_chars].rstrip() + "..."

        self._output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        safe_request = _SAFE_NAME_RE.sub("-", request_id).strip("-") or "reply"
        wav_path = (self._output_dir / f"{safe_request}.wav").resolve()
        opus_path = (self._output_dir / f"{safe_request}.opus").resolve()

        try:
            model, speaker_ids = self._load_model()
            speaker_name = self._select_speaker(speaker_ids, voice)
            model.tts_to_file(
                content,
                speaker_ids[speaker_name],
                str(wav_path),
                speed=self._speed,
            )
            return _convert_to_opus_if_available(
                source_path=wav_path,
                opus_path=opus_path,
                fallback_mime_type="audio/wav",
            )
        except ImportError:
            _LOG.warning(
                "voice reply skipped: MeloTTS is not installed; install the tts extra"
            )
            return None
        except Exception:
            _LOG.warning("MeloTTS voice reply synthesis failed", exc_info=True)
            return None

    def _load_model(self):
        if self._model is None or self._speaker_ids is None:
            from melo.api import TTS

            model = TTS(language=self._language, device=self._device)
            self._model = model
            self._speaker_ids = dict(model.hps.data.spk2id)
        return self._model, self._speaker_ids

    def _select_speaker(self, speaker_ids: dict[str, int], voice: Optional[str]) -> str:
        for candidate in (voice, self._speaker, self._language):
            if candidate and candidate in speaker_ids:
                return candidate
        if speaker_ids:
            fallback = next(iter(speaker_ids))
            _LOG.warning("MeloTTS speaker unavailable; using fallback speaker: %s", fallback)
            return fallback
        raise RuntimeError("MeloTTS model has no speakers")


def _find_executable(name: str, fallbacks: tuple[Path, ...] = ()) -> Optional[str]:
    path = shutil.which(name)
    if path:
        return path
    for candidate in fallbacks:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _convert_to_opus_if_available(
    *, source_path: Path, opus_path: Path, fallback_mime_type: str
) -> Optional[VoiceAttachment]:
    if not source_path.is_file():
        return None

    ffmpeg = _find_executable("ffmpeg", _FFMPEG_FALLBACKS)
    if ffmpeg is None:
        return VoiceAttachment(path=str(source_path), mime_type=fallback_mime_type)

    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source_path),
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
        _LOG.warning("voice reply opus conversion failed; keeping source audio", exc_info=True)
        return VoiceAttachment(path=str(source_path), mime_type=fallback_mime_type)

    if not opus_path.is_file():
        return VoiceAttachment(path=str(source_path), mime_type=fallback_mime_type)

    try:
        source_path.unlink(missing_ok=True)
    except OSError:
        _LOG.debug("voice reply intermediate cleanup failed", exc_info=True)
    return VoiceAttachment(path=str(opus_path), mime_type="audio/ogg")


def _available_say_voices(say: str) -> FrozenSet[str]:
    try:
        result = subprocess.run(
            [say, "-v", "?"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        _LOG.debug("macOS say voice list unavailable; accepting configured voice names", exc_info=True)
        return frozenset()

    voices = set()
    for line in result.stdout.splitlines():
        match = re.match(r"^(.+?)\s{2,}[a-z]{2}(?:[_-][A-Z]{2})?\s+#", line)
        if match:
            voices.add(match.group(1).strip())
    return frozenset(voices)
