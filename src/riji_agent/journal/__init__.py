"""Read-only Obsidian journal parsing and incremental indexing.

This package never writes to the journal vault. It parses Markdown notes into
structured records and maintains a local SQLite FTS5 index that downstream
retrieval and permission layers can query.
"""

from riji_agent.journal.models import NoteKind, ParsedNote
from riji_agent.journal.parser import JournalParseError, iter_note_files, parse_note
from riji_agent.journal.index import IndexStats, JournalIndex

__all__ = [
    "NoteKind",
    "ParsedNote",
    "JournalParseError",
    "iter_note_files",
    "parse_note",
    "IndexStats",
    "JournalIndex",
]
