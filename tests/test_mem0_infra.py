from __future__ import annotations

from pathlib import Path
import plistlib
from runpy import run_path

import yaml


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra" / "mem0"


def test_mem0_compose_is_loopback_only_and_pinned() -> None:
    compose = yaml.safe_load((INFRA / "compose.yaml").read_text(encoding="utf-8"))
    services = compose["services"]

    postgres_dockerfile = (INFRA / "Dockerfile.postgres").read_text(encoding="utf-8")
    assert "FROM pgvector/pgvector:0.8.6-pg17-bookworm" in postgres_dockerfile
    assert "COPY init-db.sh /docker-entrypoint-initdb.d/init-db.sh" in postgres_dockerfile
    assert "ports" not in services["postgres"]
    assert services["postgres"]["volumes"] == ["postgres_data:/var/lib/postgresql/data"]
    assert services["mem0"]["ports"] == ["127.0.0.1:38881:8000"]
    assert services["dashboard"]["ports"] == ["127.0.0.1:38880:3000"]
    assert services["mem0"]["environment"]["MEM0_TELEMETRY"] == "false"
    assert services["mem0"]["environment"]["HF_HUB_OFFLINE"] == "1"
    assert services["mem0"]["environment"]["AUTH_DISABLED"] == "false"
    expected_commit = "9a7924befd7026e41e445ba809370009e5e985a6"
    assert services["mem0"]["build"]["args"]["MEM0_COMMIT"] == expected_commit
    dashboard_dockerfile = (INFRA / "Dockerfile.dashboard").read_text(encoding="utf-8")
    assert "FROM riji-mem0-mem0:latest AS source" in dashboard_dockerfile
    assert "COPY --from=source /opt/mem0/server/dashboard/ ./" in dashboard_dockerfile


def test_mem0_api_image_pins_application_dependencies() -> None:
    dockerfile = (INFRA / "Dockerfile.api").read_text(encoding="utf-8")
    patcher = (INFRA / "patch_server.py").read_text(encoding="utf-8")

    assert '"mem0ai==2.0.20"' in dockerfile
    assert '"fastembed==0.8.0"' in dockerfile
    assert '"psycopg[binary]==3.2.10"' in dockerfile
    assert "python /tmp/riji-seed-fastembed.py" in dockerfile
    assert 'CMD ["/usr/local/bin/riji-mem0-start"]' in dockerfile
    seeder = (INFRA / "seed_fastembed.py").read_text(encoding="utf-8")
    assert 'MODEL_NAME = "BAAI/bge-small-zh-v1.5"' in seeder
    assert "replace(model.sources, hf=None)" in seeder
    assert "model_file.is_file()" in seeder
    entrypoint = (INFRA / "start-api.sh").read_text(encoding="utf-8")
    assert "cp -R -n /opt/fastembed-seed/. /models/" in entrypoint
    assert "git checkout --detach" in dockerfile
    assert "python /tmp/riji-mem0-patch-server.py" in dockerfile
    assert '"provider": "deepseek"' in patcher
    assert '"provider": "fastembed"' in patcher
    assert "expected exactly one" in patcher


def test_mem0_server_patcher_rewrites_expected_upstream_blocks(tmp_path: Path) -> None:
    patch_server = run_path(str(INFRA / "patch_server.py"))["patch_server"]
    target = tmp_path / "main.py"
    target.write_text(
        '''BUNDLED_LLM_PROVIDERS = ("openai", "anthropic", "gemini")
BUNDLED_EMBEDDER_PROVIDERS = ("openai", "gemini")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
HISTORY_DB_PATH = os.environ.get("HISTORY_DB_PATH", "/app/history/history.db")
DEFAULT_LLM_MODEL = os.environ.get("MEM0_DEFAULT_LLM_MODEL", "gpt-5-mini")
DEFAULT_EMBEDDER_MODEL = os.environ.get("MEM0_DEFAULT_EMBEDDER_MODEL", "text-embedding-3-small")

            "collection_name": POSTGRES_COLLECTION_NAME,
    "llm": {
        "provider": "openai",
        "config": {"api_key": OPENAI_API_KEY, "temperature": 0.2, "model": DEFAULT_LLM_MODEL},
    },
    "embedder": {"provider": "openai", "config": {"api_key": OPENAI_API_KEY, "model": DEFAULT_EMBEDDER_MODEL}},
''',
        encoding="utf-8",
    )

    patch_server(target)

    patched = target.read_text(encoding="utf-8")
    assert '"provider": "deepseek"' in patched
    assert '"provider": "fastembed"' in patched
    assert "BAAI/bge-small-zh-v1.5" in patched
    assert '"embedding_model_dims": 512' in patched
    assert '"embedding_dims": 512' in patched


def test_pro_air_tunnel_is_persistent_and_loopback_only() -> None:
    plist_path = ROOT / "ops" / "launchd" / "ai.riji-agent.air-tunnel.plist"
    with plist_path.open("rb") as handle:
        config = plistlib.load(handle)

    assert config["Label"] == "ai.riji-agent.air-tunnel"
    assert config["RunAtLoad"] is True
    assert config["KeepAlive"] is True
    arguments = config["ProgramArguments"]
    assert arguments[0:2] == ["/usr/bin/ssh", "-N"]
    assert "BatchMode=yes" in arguments
    assert "ExitOnForwardFailure=yes" in arguments
    assert "127.0.0.1:8765:127.0.0.1:8765" in arguments
    assert arguments[-1] == "air"
