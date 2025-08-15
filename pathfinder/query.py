from __future__ import annotations
import re
from dataclasses import dataclass
from typing import List, Set
from .utils import TYPE_GROUPS

QUOTED_RE = re.compile(r'"([^"]+)"')            # "something"
TYPE_RE   = re.compile(r'^type:([A-Za-z0-9_]+)$', re.IGNORECASE)
EXT_RE    = re.compile(r'^ext:(\.[A-Za-z0-9]+)$', re.IGNORECASE)

@dataclass
class ParsedQuery:
    exact_filenames: List[str]
    exact_stems: List[str]
    and_extensions: List[str]   # from ext:... and type:..., acts as AND filter
    tokens: List[str]           # fuzzy tokens for filename/content
    case_sensitive: bool = False

def _norm(s: str, case_sensitive: bool) -> str:
    return s if case_sensitive else s.lower()

def _normalize_ext(e: str) -> str:
    e = e.strip().lower()
    if not e.startswith("."):
        e = "." + e
    return e

def _expand_type_to_exts(type_name: str) -> Set[str]:
    return TYPE_GROUPS.get(type_name.lower(), set())

def parse_query(argv_terms: List[str], case_sensitive: bool = False) -> ParsedQuery:
    """
    Supports:
      - ext:.jpg  -> ANDed extension filter
      - type:image -> ANDed group -> extensions (png/jpg/…)
      - "name.ext" -> exact filename
      - "name"     -> exact stem
      - everything else -> fuzzy tokens for filename/content
    Note: A quoted ".ext" is treated as a literal token now; use ext:.ext for filtering.
    """
    joined = " ".join(argv_terms)
    quoted = QUOTED_RE.findall(joined)
    unquoted_str = QUOTED_RE.sub(" ", joined)
    unquoted = [t for t in re.split(r"\s+", unquoted_str.strip()) if t]

    exact_filenames: List[str] = []
    exact_stems: List[str] = []
    and_extensions: Set[str] = set()
    tokens: List[str] = []

    # Quoted items (exacts or tokens; also allow ext:/type: inside quotes)
    for raw in quoted:
        s = raw.strip()
        n = _norm(s, case_sensitive)

        m_ext = EXT_RE.match(s) or EXT_RE.match(n)
        if m_ext:
            and_extensions.add(_normalize_ext(m_ext.group(1)))
            continue

        m_type = TYPE_RE.match(s) or TYPE_RE.match(n)
        if m_type:
            and_extensions |= {_normalize_ext(e) for e in _expand_type_to_exts(m_type.group(1))}
            continue

        # Path-ish quoted -> take final component as exact filename
        if "/" in s or "\\" in s:
            exact_filenames.append(_norm(s.split("/")[-1].split("\\")[-1], case_sensitive))
        elif "." in s and not s.startswith("."):
            # looks like "name.ext"
            exact_filenames.append(_norm(s, case_sensitive))
        else:
            # plain exact stem
            exact_stems.append(_norm(s, case_sensitive))

    # Unquoted items (tokens + ext:/type:)
    for raw in unquoted:
        s = raw.strip()
        n = _norm(s, case_sensitive)

        m_ext = EXT_RE.match(s) or EXT_RE.match(n)
        if m_ext:
            and_extensions.add(_normalize_ext(m_ext.group(1)))
            continue

        m_type = TYPE_RE.match(s) or TYPE_RE.match(n)
        if m_type:
            and_extensions |= {_normalize_ext(e) for e in _expand_type_to_exts(m_type.group(1))}
            continue

        tokens.append(n)

    # Dedup keep order for exacts/tokens
    def dedup(seq: List[str]) -> List[str]:
        seen = set(); out = []
        for x in seq:
            if x not in seen:
                out.append(x); seen.add(x)
        return out

    return ParsedQuery(
        exact_filenames=dedup(exact_filenames),
        exact_stems=dedup(exact_stems),
        and_extensions=sorted(and_extensions),
        tokens=dedup(tokens),
        case_sensitive=case_sensitive,
    )
