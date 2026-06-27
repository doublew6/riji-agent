"""Demo quickstart over a fictional sample vault."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from riji_agent.journal.index import JournalIndex


def sample_vault_root() -> Path:
    return Path(__file__).resolve().parents[2] / "examples" / "sample-vault" / "riji"


def copy_sample_vault(target: Path, *, force: bool = False) -> Path:
    source = sample_vault_root()
    destination = target.expanduser().resolve()
    if destination.exists():
        if not force:
            raise FileExistsError(str(destination))
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return destination


def run_demo_chat(question: str, *, data_dir: Optional[Path] = None) -> str:
    data_root = (data_dir or Path(".riji-demo")).expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    index = JournalIndex(data_root / "demo-index.sqlite3", sample_vault_root())
    try:
        index.build_index(rebuild=True)
        hits = index.search(question, limit=3, include_private=False)
    finally:
        index.close()

    if not hits:
        return "No demo evidence found. Sources: none"

    lines = [
        "Demo answer:",
        "The fictional sample vault contains matching evidence. This local demo uses the same index path but a stub answer, so it does not read .env or call a model provider.",
        "",
        "Sources:",
    ]
    for hit in hits:
        lines.append(f"- [[{hit.source_id}]] {hit.snippet}")
    return "\n".join(lines)
