from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "privacy_scan",
    ROOT / "scripts" / "privacy_scan.py",
)
assert SPEC is not None
privacy_scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(privacy_scan)


def test_privacy_scan_flags_personal_path(tmp_path: Path) -> None:
    leaked = tmp_path / "notes.md"
    leaked.write_text(
        "vault=/" + "Users/private-name/Documents/riji\n",
        encoding="utf-8",
    )

    failures = privacy_scan.scan(tmp_path, ["notes.md"])

    assert failures
    assert "notes.md" in failures[0]


def test_privacy_scan_allows_placeholder_path(tmp_path: Path) -> None:
    public_doc = tmp_path / "docs.md"
    public_doc.write_text(
        "vault=/" + "Users/example/Documents/riji\n",
        encoding="utf-8",
    )

    assert privacy_scan.scan(tmp_path, ["docs.md"]) == []
