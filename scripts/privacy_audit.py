"""Read-only publication inventories; findings never contain matched values."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
from pathlib import Path
from typing import Any, Callable

from privacy_guard_core import (
    Finding, GuardError, MAX_BYTES, safe_source, scan_bytes, scan_path_name, scan_text,
)

MAX_COMMITS = 2000
MAX_BLOBS = 50000
MAX_TOTAL_BYTES = 100 * 1024 * 1024
GitReader = Callable[..., bytes]


def _image(raw: bytes) -> bool:
    return raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")) or (
        raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"
    )


class PublicationAudit:
    """Inventory current candidate files or one frozen Git history, without writes."""

    def __init__(self, repo: Path, config: dict[str, Any], read_git: GitReader):
        self.repo = repo
        self.config = config
        self.read_git = read_git
        self.findings: list[Finding] = []
        self.manual: list[dict[str, Any]] = []
        self.counts: Counter[str] = Counter()
        self.paths: dict[str, str] = {}
        self.blobs: dict[str, str] = {}
        self.identities: dict[str, dict[str, Any]] = {}
        self.head: str | None = None
        self.used_bytes = 0

    def git(self, *args: str) -> bytes:
        return self.read_git(self.repo, *args)

    def source(self, name: str, ordinal: str) -> str:
        # Even credential-shaped filenames must not echo a matched value.
        if scan_text(name, ordinal, self.config):
            return ordinal
        return safe_source(name, self.config)

    def inspect_path(self, name: str) -> str:
        if name not in self.paths:
            self.counts["paths"] += 1
            source = self.source(name, f"path#{self.counts['paths']}")
            self.paths[name] = source
            self.findings.extend(scan_path_name(name, source, self.config))
        return self.paths[name]

    def review(self, category: str, source: str, **details: Any) -> None:
        self.manual.append({"category": category, "source": source, **details})

    def allow_size(self, size: int, source: str) -> bool:
        if size > MAX_BYTES:
            self.review("oversized_input", source, size_bytes=size)
            return False
        if self.used_bytes + size > MAX_TOTAL_BYTES:
            self.review("total_size_limit", source, size_bytes=size)
            return False
        return True

    def inspect_bytes(self, raw: bytes, source: str) -> None:
        if not self.allow_size(len(raw), source):
            return
        self.used_bytes += len(raw)
        self.counts["content_bytes"] += len(raw)
        if _image(raw):
            self.counts["images"] += 1
            self.review("image_pixels_and_metadata", source, size_bytes=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest())
            return
        findings = scan_bytes(raw, source, self.config)
        binary = [item for item in findings if item.category == "binary_review_required"]
        if binary:
            self.counts["binary_files"] += 1
            self.review("unknown_binary", source, size_bytes=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest())
        else:
            self.counts["text_inputs"] += 1
            self.findings.extend(findings)
            if source.lower().endswith(".svg") or raw.lstrip().startswith(b"<svg"):
                self.counts["images"] += 1
                self.review("image_vector_and_metadata", source, size_bytes=len(raw),
                            sha256=hashlib.sha256(raw).hexdigest())

    def working_files(self) -> None:
        raw = self.git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        names = sorted({name.decode("utf-8") for name in raw.split(b"\0") if name})
        for name in names:
            source = self.inspect_path(name)
            path = self.repo / name
            if self.has_link_parent(path):
                self.review("symbolic_link", source)
            elif not path.exists():
                self.counts["deleted_working_files"] += 1
            elif not path.is_file():
                self.review("non_regular_file", source)
            elif self.allow_size(path.stat().st_size, source):
                with path.open("rb") as stream:
                    self.inspect_bytes(stream.read(MAX_BYTES + 1), source)

    def has_link_parent(self, path: Path) -> bool:
        current = path
        while current != self.repo:
            if current.is_symlink() or self.repo not in current.parents:
                return True
            current = current.parent
        return False

    def resolve(self, revision: str) -> str:
        if not revision or revision.startswith("-") or "\0" in revision:
            raise GuardError("invalid_revision")
        return self.git("rev-parse", "--verify", "--end-of-options", revision + "^{commit}").decode().strip()

    def inspect_identity(self, name: bytes, email: bytes, role: str) -> None:
        identity = name + b"\0" + email
        fingerprint = hashlib.sha256(identity).hexdigest()[:16]
        if fingerprint not in self.identities:
            raw = (name + b"\n" + email).decode("utf-8")
            categories = sorted({f.category for f in scan_text(raw, "identity", self.config)})
            self.identities[fingerprint] = {"fingerprint": fingerprint, "roles": [], "categories": categories}
        roles = self.identities[fingerprint]["roles"]
        if role not in roles:
            roles.append(role)

    def inspect_commit(self, commit: str, ordinal: int) -> None:
        raw = self.git("show", "-s", "--format=format:%B%x00%an%x00%ae%x00%cn%x00%ce", commit)
        parts = raw.split(b"\0")
        if len(parts) != 5:
            raise GuardError("commit_metadata_invalid")
        self.inspect_bytes(parts[0], f"commit#{ordinal}:{commit[:12]}:message")
        self.inspect_identity(parts[1], parts[2], "author")
        self.inspect_identity(parts[3], parts[4], "committer")
        self.counts["commits"] += 1

    def inspect_tree(self, commit: str) -> None:
        raw = self.git("ls-tree", "-r", "-z", "--full-tree", commit)
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            meta, path = entry.split(b"\t", 1)
            mode, kind, oid = (part.decode("ascii") for part in meta.split(b" "))
            name = path.decode("utf-8")
            source = self.inspect_path(name)
            if mode == "160000" or kind != "blob":
                self.review("submodule_or_unknown_object", source, commit=commit, object=oid)
                continue
            if mode == "120000":
                self.review("symbolic_link", source, commit=commit, object=oid)
            self.inspect_blob(oid, source)

    def inspect_blob(self, oid: str, source: str) -> None:
        if oid in self.blobs:
            return
        if len(self.blobs) >= MAX_BLOBS:
            raise GuardError("history_blob_limit")
        self.blobs[oid] = source
        self.counts["unique_blobs"] += 1
        label = f"blob:{oid[:12]}:{source}"
        size = int(self.git("cat-file", "-s", oid))
        if self.allow_size(size, label):
            self.inspect_bytes(self.git("cat-file", "blob", oid), label)

    def history(self, revision: str, *, all_ancestors: bool) -> None:
        self.head = self.resolve(revision)
        commits = [self.head]
        if all_ancestors:
            commits = self.git("rev-list", "--reverse", self.head).decode().splitlines()
        if len(commits) > MAX_COMMITS:
            raise GuardError("history_commit_limit")
        for ordinal, commit in enumerate(commits, 1):
            self.inspect_commit(commit, ordinal)
            self.inspect_tree(commit)

    def result(self, mode: str) -> dict[str, Any]:
        identities = list(self.identities.values())
        return {
            "version": 1, "mode": mode, "head": self.head,
            "ok": not self.findings and not self.manual and not identities,
            "text_scan_ok": not self.findings,
            "automated_scan_complete": not self.manual,
            "review_required": bool(self.manual or identities),
            "counts": dict(self.counts),
            "findings": [asdict(item) for item in self.findings],
            "manual_review": self.manual,
            "identity_metadata": {"review_required": bool(identities), "identities": identities},
            "coverage": {
                "ignored_working_files": "excluded", "image_ocr_and_metadata": "manual",
                "submodule_contents": "manual", "identity_values": "not_emitted",
                "network_requests": "none", "repository_mutations": "none",
            },
        }


def audit_sources(repo: Path, mode: str, config: dict[str, Any],
                  read_git: GitReader, revision: str | None = None) -> dict[str, Any]:
    audit = PublicationAudit(repo, config, read_git)
    if mode == "all-files":
        audit.working_files()
    else:
        if revision is None:
            raise GuardError("explicit_revision_required")
        audit.history(revision, all_ancestors=mode == "history")
    return audit.result(mode)
