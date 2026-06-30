"""Models for the Wang Yangming thought knowledge base.

This corpus is kept entirely separate from the user's journal so the two source
types can never be conflated in an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CitationKind(str, Enum):
    QUOTE = "quote"  # verbatim, traceable to a cited source
    INTERPRETATION = "interpretation"  # paraphrase/explanation, no verbatim source


@dataclass(frozen=True)
class YangmingDocument:
    doc_id: str
    title: str
    source: str  # provenance, e.g. 王守仁《传习录》
    version: str  # edition/version note
    note: str = ""


@dataclass(frozen=True)
class YangmingChunk:
    chunk_id: str
    doc_id: str
    ref: str  # citable reference, e.g. 《传习录·上·徐爱录》
    kind: CitationKind
    text: str


@dataclass(frozen=True)
class CitationHit:
    chunk_id: str
    ref: str
    kind: CitationKind
    text: str
    title: str
    source: str
    version: str
