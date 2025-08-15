from __future__ import annotations
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from .query import ParsedQuery
from .utils import iter_files, is_probably_text

@dataclass
class MatchResult:
    path: Path

@dataclass
class SearchStats:
    emitted: int
    wall_time_sec: float
    idle_time_sec: float       # final idle gap that triggered stop (0 if not idle-stop)
    stopped_reason: str        # "idle_timeout" | "limit" | "complete"

def _read_text(path: Path, max_bytes: int) -> str:
    size = path.stat().st_size
    to_read = min(size, max_bytes)
    with open(path, "rb") as f:
        data = f.read(to_read)
    try:
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return data.decode("latin-1", errors="ignore")

def _name_matches(filename: str, stem: str, ext: str, q: ParsedQuery) -> bool:
    fn = filename if q.case_sensitive else filename.lower()
    st = stem     if q.case_sensitive else stem.lower()
    ex = ext      if q.case_sensitive else ext.lower()
    if q.and_extensions and ex not in q.and_extensions:
        return False
    if q.exact_filenames and fn in q.exact_filenames:
        return True
    if q.exact_stems and st in q.exact_stems:
        return True
    for tok in q.tokens:
        if tok and tok in fn:
            return True
    return False

def _content_matches(text: str, q: ParsedQuery) -> bool:
    if not q.tokens:
        return False
    hay = text if q.case_sensitive else text.lower()
    for tok in q.tokens:
        if tok and tok in hay:
            return True
    return False

def _file_id(path: Path) -> Tuple[int, int] | Tuple[str]:
    try:
        st = path.stat()
        return (int(st.st_dev), int(st.st_ino))
    except Exception:
        try:
            return (str(path.resolve()).casefold(),)
        except Exception:
            return (str(path),)

def _evaluate_path(
    path: Path,
    q: ParsedQuery,
    *,
    scan_content: bool,
    content_only: bool,
    max_bytes: int,
    ext_match_or: bool,
) -> Optional[MatchResult]:
    try:
        filename = path.name
        stem = path.stem
        ext = path.suffix.lower()
    except Exception:
        return None

    if ext_match_or and q.and_extensions and ext in q.and_extensions:
        return MatchResult(path)

    if not content_only:
        if _name_matches(filename, stem, ext, q):
            return MatchResult(path)

    if scan_content:
        try:
            if is_probably_text(path) and path.stat().st_size <= max_bytes:
                text = _read_text(path, max_bytes=max_bytes)
                if _content_matches(text, q):
                    if not q.and_extensions or ext in q.and_extensions:
                        return MatchResult(path)
        except Exception:
            pass
    return None

def search(
    roots: Iterable[Path],
    query: ParsedQuery,
    *,
    scan_content: bool = True,
    content_only: bool = False,
    follow_symlinks: bool = False,
    max_content_size_mb: int = 10,
    workers: int = 8,
    limit: int = 200,
    max_depth: int | None = None,
    exclude_dirs: Iterable[str] = (),
    stream_callback: Callable[[Path], None] | None = None,
    ext_match_or: bool = False,
    idle_timeout_sec: float | None = None,
) -> Tuple[List[MatchResult], SearchStats]:
    """
    Boolean matching (no scores). De-dupes matches and stops when:
      - `limit` results emitted, OR
      - `idle_timeout_sec` elapses without a new match (idle timeout), OR
      - traversal completes.
    Returns (results, stats). If streaming, results may be empty (since they're printed live).
    """
    start = time.monotonic()
    results: List[MatchResult] = []
    max_bytes = max_content_size_mb * 1024 * 1024

    stop_event = threading.Event()
    seen_ids: set = set()
    emitted_count = 0
    stopped_reason = "complete"

    # Idle-timeout bookkeeping
    idle_deadline = (start + idle_timeout_sec) if (idle_timeout_sec and idle_timeout_sec > 0) else None
    last_emit_time = None  # monotonic timestamp of last emit

    def timed_out() -> bool:
        return idle_deadline is not None and time.monotonic() >= idle_deadline

    def reset_idle_timer():
        nonlocal idle_deadline, last_emit_time
        last_emit_time = time.monotonic()
        if idle_timeout_sec and idle_timeout_sec > 0:
            idle_deadline = last_emit_time + idle_timeout_sec

    def maybe_emit(path: Path) -> bool:
        nonlocal emitted_count, stopped_reason
        fid = _file_id(path)
        if fid in seen_ids:
            return True
        seen_ids.add(fid)

        if stream_callback:
            stream_callback(path)
        else:
            results.append(MatchResult(path))
        emitted_count += 1

        reset_idle_timer()

        if emitted_count >= max(1, limit):
            stopped_reason = "limit"
            stop_event.set()
            return False
        return True

    # Fast path (no content scan)
    if not scan_content:
        for p in iter_files(roots, follow_symlinks=follow_symlinks, max_depth=max_depth, exclude_dirs=exclude_dirs):
            if stop_event.is_set() or timed_out():
                if timed_out():
                    stopped_reason = "idle_timeout"
                break
            r = _evaluate_path(p, query, scan_content=False, content_only=content_only, max_bytes=max_bytes, ext_match_or=ext_match_or)
            if r:
                if not maybe_emit(r.path):
                    break
        end = time.monotonic()
        idle_gap = max(0.0, end - (last_emit_time or start)) if stopped_reason == "idle_timeout" else 0.0
        stats = SearchStats(
            emitted=emitted_count,
            wall_time_sec=end - start,
            idle_time_sec=idle_gap,
            stopped_reason=stopped_reason,
        )
        return results, stats

    # Content scan with threads
    futures = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for p in iter_files(roots, follow_symlinks=follow_symlinks, max_depth=max_depth, exclude_dirs=exclude_dirs):
            if stop_event.is_set() or timed_out():
                if timed_out():
                    stopped_reason = "idle_timeout"
                break
            futures.append(ex.submit(_evaluate_path, p, query,
                                     scan_content=True, content_only=content_only,
                                     max_bytes=max_bytes, ext_match_or=ext_match_or))
            if len(futures) >= 2048 or timed_out():
                if timed_out():
                    stopped_reason = "idle_timeout"
                for fut in as_completed(futures):
                    if stop_event.is_set() or stopped_reason == "idle_timeout":
                        break
                    r = fut.result()
                    if r and not maybe_emit(r.path):
                        break
                futures.clear()
                if stop_event.is_set() or stopped_reason == "idle_timeout":
                    break

        for fut in as_completed(futures):
            if stop_event.is_set():
                break
            r = fut.result()
            if r and not maybe_emit(r.path):
                break

    end = time.monotonic()
    idle_gap = max(0.0, end - (last_emit_time or start)) if stopped_reason == "idle_timeout" else 0.0
    stats = SearchStats(
        emitted=emitted_count,
        wall_time_sec=end - start,
        idle_time_sec=idle_gap,
        stopped_reason=stopped_reason,
    )
    return results, stats
