from pathlib import Path

from riji_agent.voice.service import MacOSSayVoiceReplyService


def test_macos_say_voice_service_converts_to_feishu_opus(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_which(name: str) -> str | None:
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


def test_macos_say_voice_service_returns_none_when_say_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("riji_agent.voice.service.shutil.which", lambda name: None)

    service = MacOSSayVoiceReplyService(tmp_path / "voice")

    assert service.synthesize_reply(text="hello", request_id="req") is None
