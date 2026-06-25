"""A small, clearly-marked starter corpus for the Wang Yangming KB.

These passages are widely attributed to 《传习录》, but maintainers should verify
and extend them against an authoritative published edition before relying on the
citations. ``version`` flags that verification is still needed. Interpretation
chunks carry no verbatim source and are labelled as such.
"""

from __future__ import annotations

from riji_agent.yangming.models import CitationKind, YangmingChunk, YangmingDocument
from riji_agent.yangming.store import YangmingKB

_CHUANXILU = YangmingDocument(
    doc_id="chuanxilu",
    title="传习录",
    source="王守仁《传习录》（门人辑录之语录与论学书信）",
    version="公有领域古籍；引用前请以权威点校本（如中华书局点校本）核对",
    note="《传习录》为王阳明语录及论学书信集，分上、中、下三卷。",
)

_QUOTES = [
    ("cxl-1", "《传习录·上·徐爱录》", "知是行的主意，行是知的工夫；知是行之始，行是知之成。"),
    ("cxl-2", "《传习录·上·徐爱录》", "心即理也。天下又有心外之事、心外之理乎？"),
    ("cxl-3", "《传习录·中·答顾东桥书》", "知之真切笃实处即是行，行之明觉精察处即是知。"),
]

_INTERPRETATIONS = [
    (
        "interp-liangzhi",
        "概括性阐释（无逐字出处）",
        "王阳明心学以『致良知』为核心：主张在具体事务上磨练，使内在的良知在行动中得到落实，而非空谈。",
    ),
]


def load_seed(kb: YangmingKB) -> None:
    """Populate the KB with the starter corpus (idempotent)."""
    kb.add_document(_CHUANXILU)
    for chunk_id, ref, text in _QUOTES:
        kb.add_chunk(YangmingChunk(chunk_id, _CHUANXILU.doc_id, ref, CitationKind.QUOTE, text))
    for chunk_id, ref, text in _INTERPRETATIONS:
        kb.add_chunk(
            YangmingChunk(chunk_id, _CHUANXILU.doc_id, ref, CitationKind.INTERPRETATION, text)
        )
