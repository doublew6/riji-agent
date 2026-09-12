from __future__ import annotations

from pathlib import Path


SERVER_MAIN = Path("/opt/mem0/server/main.py")


def _replace_once(source: str, old: str, new: str, *, label: str) -> str:
    matches = source.count(old)
    if matches != 1:
        raise RuntimeError(f"expected exactly one {label} block, found {matches}")
    return source.replace(old, new, 1)


def patch_server(path: Path = SERVER_MAIN) -> None:
    source = path.read_text(encoding="utf-8")
    source = _replace_once(
        source,
        '''BUNDLED_LLM_PROVIDERS = ("openai", "anthropic", "gemini")
BUNDLED_EMBEDDER_PROVIDERS = ("openai", "gemini")''',
        '''BUNDLED_LLM_PROVIDERS = ("openai", "anthropic", "gemini", "deepseek")
BUNDLED_EMBEDDER_PROVIDERS = ("openai", "gemini", "fastembed")''',
        label="bundled providers",
    )
    source = _replace_once(
        source,
        '''OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
HISTORY_DB_PATH = os.environ.get("HISTORY_DB_PATH", "/app/history/history.db")
DEFAULT_LLM_MODEL = os.environ.get("MEM0_DEFAULT_LLM_MODEL", "gpt-5-mini")
DEFAULT_EMBEDDER_MODEL = os.environ.get("MEM0_DEFAULT_EMBEDDER_MODEL", "text-embedding-3-small")''',
        '''OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")
HISTORY_DB_PATH = os.environ.get("HISTORY_DB_PATH", "/app/history/history.db")
DEFAULT_LLM_MODEL = os.environ.get("MEM0_DEFAULT_LLM_MODEL", "deepseek-chat")
DEFAULT_EMBEDDER_MODEL = os.environ.get("MEM0_DEFAULT_EMBEDDER_MODEL", "BAAI/bge-small-zh-v1.5")''',
        label="model environment",
    )
    source = _replace_once(
        source,
        '            "collection_name": POSTGRES_COLLECTION_NAME,',
        '            "collection_name": POSTGRES_COLLECTION_NAME,\n'
        '            "embedding_model_dims": 512,',
        label="pgvector embedding dimensions",
    )
    source = _patch_default(source)
    source += "\nfrom riji_routes import install_routes\ninstall_routes(app, get_memory_instance, require_admin)\n"
    path.write_text(source, encoding="utf-8")


def _patch_default(source: str) -> str:
    return _replace_once(
        source,
        '''    "llm": {
        "provider": "openai",
        "config": {"api_key": OPENAI_API_KEY, "temperature": 0.2, "model": DEFAULT_LLM_MODEL},
    },
    "embedder": {"provider": "openai", "config": {"api_key": OPENAI_API_KEY, "model": DEFAULT_EMBEDDER_MODEL}},''',
        '''    "llm": {
        "provider": "deepseek",
        "config": {
            "api_key": DEEPSEEK_API_KEY,
            "deepseek_base_url": DEEPSEEK_API_BASE,
            "temperature": 0.2,
            "model": DEFAULT_LLM_MODEL,
        },
    },
    "embedder": {
        "provider": "fastembed",
        "config": {"model": DEFAULT_EMBEDDER_MODEL, "embedding_dims": 512},
    },''',
        label="default memory configuration",
    )


if __name__ == "__main__":
    patch_server()
