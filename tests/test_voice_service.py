from pathlib import Path

from riji_agent.voice.service import MacOSSayVoiceReplyService, MeloTTSVoiceReplyService


class FakeMeloModel:
    def __init__(self) -> None:
        self.calls = []

    def tts_to_file(self, text, speaker_id, output_path, speed):
        self.calls.append(
            {"text": text, "speaker_id": speaker_id, "output_path": output_path, "speed": speed}
        )
        Path(output_path).write_bytes(b"wav")


def test_macos_say_voice_service_converts_to_feishu_opus(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_which(name: str):
        return {"say": "/usr/bin/say", "ffmpeg": "/opt/homebrew/bin/ffmpeg"}.get(name)

    monkeypatch.setattr("riji_agent.voice.service.shutil.which", fake_which)

    def fake_run(command, check, stdout, stderr):
        calls.append(command)
        if command[0].endswith("say"):
            output = Path(command[command.index("-o") + 1])
            output.write_bytes(b"m4a")
        else:
            output = Path(command[-1])
            output.write_bytes(b"opus")

    monkeypatch.setattr("riji_agent.voice.service.subprocess.run", fake_run)

    service = MacOSSayVoiceReplyService(tmp_path / "voice", max_chars=8)
    attachment = service.synthesize_reply(text="这是一段比较长的回复文本", request_id="req/1")

    assert attachment is not None
    assert attachment.path.endswith(".opus")
    assert attachment.mime_type == "audio/ogg"
    assert Path(attachment.path).read_bytes() == b"opus"
    say_command = calls[0]
    ffmpeg_command = calls[1]
    assert "-f" in say_command
    assert "-i" in ffmpeg_command
    assert "libopus" in ffmpeg_command
    assert "这是一段比较长的回复文本" not in say_command
    assert not list((tmp_path / "voice").glob("*.m4a"))
    assert not list((tmp_path / "voice").glob("*.txt"))


def test_macos_say_voice_service_uses_per_reply_voice(tmp_path: Path, monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(
        "riji_agent.voice.service.shutil.which",
        lambda name: "/usr/bin/say" if name == "say" else None,
    )
    monkeypatch.setattr("riji_agent.voice.service._available_say_voices", lambda _say: {"Flo"})

    def fake_run(command, check, stdout, stderr):
        calls.append(command)
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"m4a")

    monkeypatch.setattr("riji_agent.voice.service.subprocess.run", fake_run)

    service = MacOSSayVoiceReplyService(tmp_path / "voice", voice="Tingting")
    attachment = service.synthesize_reply(text="hello", request_id="req", voice="Flo")

    assert attachment is not None
    assert calls[0][1:3] == ["-v", "Flo"]


def test_macos_say_voice_service_falls_back_when_persona_voice_missing(
    tmp_path: Path, monkeypatch
) -> None:
    calls = []

    monkeypatch.setattr(
        "riji_agent.voice.service.shutil.which",
        lambda name: "/usr/bin/say" if name == "say" else None,
    )
    monkeypatch.setattr("riji_agent.voice.service._available_say_voices", lambda _say: {"Tingting"})

    def fake_run(command, check, stdout, stderr):
        calls.append(command)
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"m4a")

    monkeypatch.setattr("riji_agent.voice.service.subprocess.run", fake_run)

    service = MacOSSayVoiceReplyService(tmp_path / "voice", voice="Tingting")
    attachment = service.synthesize_reply(text="hello", request_id="req", voice="Missing")

    assert attachment is not None
    assert calls[0][1:3] == ["-v", "Tingting"]


def test_macos_say_voice_service_falls_back_to_m4a_without_ffmpeg(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "riji_agent.voice.service.shutil.which",
        lambda name: "/usr/bin/say" if name == "say" else None,
    )

    def fake_run(command, check, stdout, stderr):
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"m4a")

    monkeypatch.setattr("riji_agent.voice.service.subprocess.run", fake_run)

    service = MacOSSayVoiceReplyService(tmp_path / "voice")
    attachment = service.synthesize_reply(text="hello", request_id="req")

    assert attachment is not None
    assert attachment.path.endswith(".m4a")
    assert attachment.mime_type == "audio/mp4"


def test_macos_say_voice_service_uses_ffmpeg_fallback_path(tmp_path: Path, monkeypatch) -> None:
    fallback_ffmpeg = tmp_path / "homebrew" / "ffmpeg"
    fallback_ffmpeg.parent.mkdir()
    fallback_ffmpeg.write_text("#!/bin/sh\n")
    fallback_ffmpeg.chmod(0o755)

    monkeypatch.setattr(
        "riji_agent.voice.service.shutil.which",
        lambda name: "/usr/bin/say" if name == "say" else None,
    )
    monkeypatch.setattr("riji_agent.voice.service._FFMPEG_FALLBACKS", (fallback_ffmpeg,))

    def fake_run(command, check, stdout, stderr):
        if command[0].endswith("say"):
            output = Path(command[command.index("-o") + 1])
            output.write_bytes(b"m4a")
        else:
            assert command[0] == str(fallback_ffmpeg)
            output = Path(command[-1])
            output.write_bytes(b"opus")

    monkeypatch.setattr("riji_agent.voice.service.subprocess.run", fake_run)

    service = MacOSSayVoiceReplyService(tmp_path / "voice")
    attachment = service.synthesize_reply(text="hello", request_id="req")

    assert attachment is not None
    assert attachment.path.endswith(".opus")
    assert attachment.mime_type == "audio/ogg"


def test_macos_say_voice_service_returns_none_when_say_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("riji_agent.voice.service.shutil.which", lambda name: None)
    monkeypatch.setattr("riji_agent.voice.service._SAY_FALLBACKS", ())

    service = MacOSSayVoiceReplyService(tmp_path / "voice")

    assert service.synthesize_reply(text="hello", request_id="req") is None


def test_melotts_voice_service_converts_to_feishu_opus(tmp_path: Path, monkeypatch) -> None:
    model = FakeMeloModel()
    calls = []

    service = MeloTTSVoiceReplyService(
        tmp_path / "voice",
        language="ZH",
        speaker="ZH",
        device="cpu",
        speed=1.15,
        max_chars=8,
    )
    monkeypatch.setattr(service, "_load_model", lambda: (model, {"ZH": 7}))
    monkeypatch.setattr(
        "riji_agent.voice.service.shutil.which",
        lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None,
    )

    def fake_run(command, check, stdout, stderr):
        calls.append(command)
        output = Path(command[-1])
        output.write_bytes(b"opus")

    monkeypatch.setattr("riji_agent.voice.service.subprocess.run", fake_run)

    attachment = service.synthesize_reply(text="这是一段比较长的回复文本", request_id="req/1")

    assert attachment is not None
    assert attachment.path.endswith(".opus")
    assert attachment.mime_type == "audio/ogg"
    assert Path(attachment.path).read_bytes() == b"opus"
    assert model.calls == [
        {
            "text": "这是一段比较长的回复文本"[:8] + "...",
            "speaker_id": 7,
            "output_path": str(tmp_path / "voice" / "req-1.wav"),
            "speed": 1.15,
        }
    ]
    assert "libopus" in calls[0]
    assert not list((tmp_path / "voice").glob("*.wav"))


def test_melotts_voice_service_uses_per_reply_speaker(tmp_path: Path, monkeypatch) -> None:
    model = FakeMeloModel()
    service = MeloTTSVoiceReplyService(tmp_path / "voice", language="ZH", speaker="ZH")
    monkeypatch.setattr(service, "_load_model", lambda: (model, {"ZH": 1, "mentor": 2}))
    monkeypatch.setattr("riji_agent.voice.service.shutil.which", lambda name: None)

    attachment = service.synthesize_reply(text="hello", request_id="req", voice="mentor")

    assert attachment is not None
    assert attachment.path.endswith(".wav")
    assert attachment.mime_type == "audio/wav"
    assert model.calls[0]["speaker_id"] == 2


def test_melotts_voice_service_returns_none_when_package_missing(
    tmp_path: Path, monkeypatch
) -> None:
    service = MeloTTSVoiceReplyService(tmp_path / "voice")
    monkeypatch.setattr(service, "_load_model", lambda: (_ for _ in ()).throw(ImportError()))

    assert service.synthesize_reply(text="hello", request_id="req") is None
