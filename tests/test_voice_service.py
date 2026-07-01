from pathlib import Path

from riji_agent.voice.service import MacOSSayVoiceReplyService


def test_macos_say_voice_service_writes_temp_input_not_command_text(tmp_path: Path, monkeypatch) -> None:
    calls = []

    monkeypatch.setattr("riji_agent.voice.service.shutil.which", lambda name: "/usr/bin/say")

    def fake_run(command, check, stdout, stderr):
        calls.append(command)
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"audio")

    monkeypatch.setattr("riji_agent.voice.service.subprocess.run", fake_run)

    service = MacOSSayVoiceReplyService(tmp_path / "voice", max_chars=8)
    attachment = service.synthesize_reply(text="这是一段比较长的回复文本", request_id="req/1")

    assert attachment is not None
    assert attachment.path.endswith(".m4a")
    assert Path(attachment.path).read_bytes() == b"audio"
    command = calls[0]
    assert "-f" in command
    assert "这是一段比较长的回复文本" not in command
    assert not list((tmp_path / "voice").glob("*.txt"))


def test_macos_say_voice_service_returns_none_when_say_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("riji_agent.voice.service.shutil.which", lambda name: None)

    service = MacOSSayVoiceReplyService(tmp_path / "voice")

    assert service.synthesize_reply(text="hello", request_id="req") is None
