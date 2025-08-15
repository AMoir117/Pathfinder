from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from .query import parse_query
from .search import search
from .utils import default_search_paths

DEFAULT_EXCLUDES = [".git", "node_modules", "__pycache__", ".venv", ".mypy_cache", ".gradle", "target", "build"]

def _fmt_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pathfinder",
        description=(
            "Search files by name and/or text content across common locations "
            "such as Desktop, Documents, Downloads, Pictures, etc.\n\n"
            "Search supports:\n"
            "  • ext:<extension> — Match by extension (e.g., ext:.jpg)\n"
            "  • type:<category> — Match by type category (image, video, audio, doc, code, data)\n"
            "  • \"name.ext\"     — Exact filename match (quoted)\n"
            "  • tokens          — Fuzzy text match for filenames or file contents\n\n"
            "By default, matches are fuzzy and case-insensitive unless otherwise specified."
        ),
        formatter_class=argparse.RawTextHelpFormatter
    )

    # Search terms
    p.add_argument(
        "terms", nargs="+",
        help="Search terms. Combine ext:/type: filters with text or exact matches."
    )

    # Paths & traversal
    p.add_argument(
        "--paths", nargs="+", type=str,
        help="Override search roots (default: common locations in your home folder)."
    )
    p.add_argument(
        "--follow-symlinks", action="store_true",
        help="Follow symbolic links when traversing directories."
    )
    p.add_argument(
        "--max-depth", type=int, default=None,
        help="Limit directory traversal depth from each root (e.g., 2 for only subfolders)."
    )
    p.add_argument(
        "--exclude", nargs="*", default=DEFAULT_EXCLUDES,
        help=f"Directory names to skip. Default: {DEFAULT_EXCLUDES}"
    )

    # Matching behavior
    p.add_argument(
        "--no-content", action="store_true",
        help="Disable content scanning (only search filenames)."
    )
    p.add_argument(
        "--content-only", action="store_true",
        help="Search only within file content (ignore filename matches)."
    )
    p.add_argument(
        "--filename-only", action="store_true",
        help="Alias for --no-content (filename/stem/ext search only)."
    )
    p.add_argument(
        "--case-sensitive", action="store_true",
        help="Enable case-sensitive matching for filenames and content."
    )
    p.add_argument(
        "--max-size-mb", type=int, default=10,
        help="Maximum file size for content scan in MB (default: 10)."
    )

    # Output & performance
    p.add_argument(
        "--limit", type=int, default=20,
        help="Stop after finding this many results (default: 20)."
    )
    p.add_argument(
        "--json", action="store_true",
        help="Output each result as a JSON object (path, and optionally file info)."
    )
    p.add_argument(
        "--no-stream", action="store_true",
        help="Disable streaming — only print results after search completes."
    )
    p.add_argument(
        "--workers", type=int, default=8,
        help="Number of worker threads for content scanning (default: 8)."
    )

    # Extension logic
    p.add_argument(
        "--ext-match-or", action="store_true",
        help="Extensions from ext:/type: filters act as OR instead of AND.\n"
             "Example: ext:.jpg pfp → Matches any .jpg OR name containing 'pfp'."
    )

    # Idle timeout
    p.add_argument(
        "--idle-timeout", type=float, default=None,
        help="Stop searching after N seconds with no new matches (resets on each match).\n"
             "Default: 5 seconds."
    )

    # Extra file info
    p.add_argument(
        "--f-info", action="store_true",
        help="Show extra file information (modified/created dates) in output."
    )

    # Expanded search control
    p.add_argument(
        "-xs", "--expanded-search", action="store_true",
        help="If no results in common dirs/home (or provided --paths), expand to full drive(s) and search again."
    )


    return p


def main(argv: List[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.filename_only:
        args.no_content = True

    # ----- Resolve first-pass roots (common dirs + ensure home if using defaults) -----
    if args.paths:
        first_pass_roots = []
        for s in args.paths:
            p = Path(s).expanduser()
            if not p.exists():
                print(f"[warn] path does not exist: {p}", file=sys.stderr)
                continue
            first_pass_roots.append(p)
        if not first_pass_roots:
            print("[error] no valid paths provided.", file=sys.stderr)
            return 1
    else:
        first_pass_roots = default_search_paths()
        home = Path.home()
        try:
            r_first = {p.resolve() for p in first_pass_roots}
            if home.resolve() not in r_first:
                first_pass_roots.append(home)
        except Exception:
            if home not in first_pass_roots:
                first_pass_roots.append(home)

    q = parse_query(args.terms, case_sensitive=args.case_sensitive)

    # Streaming callback
    def emit(path: Path) -> None:
        try:
            st = path.stat()
            created = getattr(st, "st_ctime", None)
            modified = getattr(st, "st_mtime", None)
        except Exception:
            created = None
            modified = None

        if args.json:
            obj = {"path": str(path)}
            if args.f_info:
                if modified is not None:
                    obj["modified"] = _fmt_ts(modified)
                if created is not None:
                    obj["created"] = _fmt_ts(created)
            print(json.dumps(obj, ensure_ascii=False), flush=True)
        else:
            if args.f_info:
                extra = []
                if modified is not None:
                    extra.append(f"modified={_fmt_ts(modified)}")
                if created is not None:
                    extra.append(f"created={_fmt_ts(created)}")
                suffix = ("  [" + ", ".join(extra) + "]") if extra else ""
                print(f"{path}{suffix}", flush=True)
            else:
                print(str(path), flush=True)

    # ANSI colors
    RED = "\033[91m"; GREEN = "\033[92m"; PURPLE = "\033[95m"; RESET = "\033[0m"

    # ===== PASS 1: common+home (or user-provided --paths) =====
    results1, stats1 = search(
        roots=first_pass_roots,
        query=q,
        scan_content=not args.no_content,
        content_only=args.content_only,
        follow_symlinks=args.follow_symlinks,
        max_content_size_mb=args.max_size_mb,
        workers=args.workers,
        limit=args.limit,
        max_depth=args.max_depth,
        exclude_dirs=args.exclude or [],
        stream_callback=None if args.no_stream else emit,
        ext_match_or=args.ext_match_or,
        idle_timeout_sec=args.idle_timeout,
    )

    if args.no_stream:
        for r in results1:
            emit(r.path)

    # Status after PASS 1
    active1 = max(0.0, stats1.wall_time_sec - stats1.idle_time_sec)
    color1 = GREEN if stats1.emitted > 0 else RED
    print(
        f"\n{color1}[status] (pass 1: common dirs) Files found: {stats1.emitted}{RESET}\n"
        f"{color1}[status] (pass 1) Time: {active1:.2f}s + {stats1.idle_time_sec:.2f}s idle{RESET} "
        f"{PURPLE}(stop={stats1.stopped_reason}){RESET}",
        file=sys.stderr
    )

    # If we found anything, end here unless expanded search
    if stats1.emitted > 0 and not args.expanded_search:
        return 0

    # If no results and expanded-search not requested, hint and stop
    if not args.expanded_search:
        print(f"{PURPLE}[status] No matches in pass 1. Rerun with -xs/--expanded-search to scan full drive(s).{RESET}",
              file=sys.stderr)
        return 0

    # ===== PASS 2: expand to full drive(s) =====
    from .utils import drive_roots
    drive_roots_list = drive_roots()
    print(f"{PURPLE}[status] Expanding to full drive(s)...{RESET}", file=sys.stderr)

    results2, stats2 = search(
        roots=drive_roots_list,
        query=q,
        scan_content=not args.no_content,
        content_only=args.content_only,
        follow_symlinks=args.follow_symlinks,
        max_content_size_mb=args.max_size_mb,
        workers=args.workers,
        limit=args.limit,
        max_depth=args.max_depth,
        exclude_dirs=args.exclude or [],
        stream_callback=None if args.no_stream else emit,
        ext_match_or=args.ext_match_or,
        idle_timeout_sec=args.idle_timeout,
    )

    if args.no_stream:
        for r in results2:
            emit(r.path)

    # Final status for PASS 2
    active2 = max(0.0, stats2.wall_time_sec - stats2.idle_time_sec)
    color2 = GREEN if stats2.emitted > 0 else RED
    print(
        f"\n{color2}[status] (pass 2: full drives) Files found: {stats2.emitted}{RESET}\n"
        f"{color2}[status] (pass 2) Time: {active2:.2f}s + {stats2.idle_time_sec:.2f}s idle{RESET} "
        f"{PURPLE}(stop={stats2.stopped_reason}){RESET}",
        file=sys.stderr
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
