from pathlib import Path

from fastapi.testclient import TestClient

from riji_agent.config import Settings
from riji_agent.main import create_app


def test_health_check_does_not_expose_local_configuration(tmp_path: Path) -> None:
    journal_root = tmp_path / "journal"
    journal_root.mkdir()
    settings = Settings(
        RIJI_JOURNAL_ROOT=str(journal_root),
        RIJI_DATA_DIR=str(tmp_path / "runtime"),
        DEEPSEEK_API_KEY="secret",
        RIJI_ALLOWED_FEISHU_USER_IDS="ou_one",
        HERMES_SHARED_SECRET="another-secret",
    )

    response = TestClient(create_app(settings)).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"service": "riji-agent", "status": "ok"}
    assert str(journal_root) not in response.text
