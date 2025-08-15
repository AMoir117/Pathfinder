from __future__ import annotations
import os
import sys
from pathlib import Path
from typing import Iterable, Iterator, List, Set

# ---------- NEW: type groups ----------
TYPE_GROUPS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".heic", ".svg"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".m4v"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"},
    "doc":   {".pdf", ".doc", ".docx", ".rtf", ".txt", ".md", ".rst"},
    "code":  {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".cs", ".go", ".rb", ".rs", ".php", ".sh", ".ps1"},
    "archive": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar"},
    "data":  {".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".parquet", ".feather",
              ".arrow", ".orc", ".h5", ".hdf5", ".db", ".sqlite", ".sqlite3", ".db3",
              ".dta", ".sav"},
    "sheet": {".xlsx", ".xls", ".ods", ".csv", ".tsv"},
    "notebook": {".ipynb"},
    "pdf": {".pdf"},
}
# --------------------------------------

COMMON_DIR_NAMES = [
    "Desktop","Documents","Downloads","Pictures","Music","Videos","Screenshots",
]

CLOUD_VARIANTS = [
    "OneDrive/Desktop","OneDrive/Documents","OneDrive/Pictures","iCloud Drive/Desktop","iCloud Drive/Documents",
]

TEXT_EXTS = {
    ".txt",".md",".rst",".log",".py",".js",".ts",".tsx",".jsx",".json",".yml",".yaml",".ini",".cfg",".conf",
    ".html",".htm",".css",".xml",".csv",".tsv",".ipynb",".tex",
}

def platform_home() -> Path:
    return Path.home()

def default_search_paths() -> List[Path]:
    home = platform_home()
    paths: List[Path] = []
    for name in COMMON_DIR_NAMES:
        p = home / name
        if p.exists():
            paths.append(p)
    if sys.platform.startswith("win"):
        paths.append(home)
    for variant in CLOUD_VARIANTS:
        p = home / variant
        if p.exists():
            paths.append(p)
    seen = set(); uniq = []
    for p in paths:
        try: rp = p.resolve()
        except Exception: rp = p
        if rp not in seen:
            uniq.append(p); seen.add(rp)
    return uniq

def is_probably_text(path: Path) -> bool:
    ext = path.suffix.lower()
    if ext in TEXT_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            sample = f.read(1024)
        return b"\x00" not in sample
    except Exception:
        return False

def _walk_scandir(root: Path, follow_symlinks: bool, max_depth: int | None, exclude_dirs: Set[str]) -> Iterator[Path]:
    stack = [(root, 0)]
    while stack:
        cur, depth = stack.pop()
        try:
            it = os.scandir(cur)
        except Exception:
            continue
        with it:
            for entry in it:
                try:
                    name = entry.name
                    if entry.is_dir(follow_symlinks=follow_symlinks):
                        if name in exclude_dirs:
                            continue
                        next_depth = depth + 1
                        if max_depth is None or next_depth <= max_depth:
                            stack.append((Path(entry.path), next_depth))
                    else:
                        if entry.is_file(follow_symlinks=follow_symlinks) or (follow_symlinks and entry.is_symlink()):
                            yield Path(entry.path)
                except Exception:
                    continue

def iter_files(paths: Iterable[Path], *, follow_symlinks: bool = False, max_depth: int | None = None, exclude_dirs: Iterable[str] = (),) -> Iterator[Path]:
    excludes = set(exclude_dirs)
    for root in paths:
        if root.exists():
            yield from _walk_scandir(root, follow_symlinks, max_depth, excludes)



def drive_roots() -> List[Path]:
    """
    Return a list of drive/mount roots to scan for a whole-drive search.
    - Windows: all existing drive letters (C:/, D:/, ...)
    - POSIX: filesystem root '/' plus mounted volumes (e.g., /Volumes/*, /mnt/*, /media/*)
    """
    roots: List[Path] = []
    if os.name == "nt":
        from string import ascii_uppercase
        for letter in ascii_uppercase:
            p = Path(f"{letter}:\\")
            if p.exists():
                roots.append(p)
    else:
        roots.append(Path("/"))
        for base in (Path("/Volumes"), Path("/mnt"), Path("/media")):
            if base.exists():
                try:
                    for child in base.iterdir():
                        if child.is_dir():
                            roots.append(child)
                except Exception:
                    pass

    # de-dup by resolved path, preserve order
    seen = set()
    out: List[Path] = []
    for r in roots:
        try:
            rp = r.resolve()
        except Exception:
            rp = r
        if rp not in seen:
            out.append(r); seen.add(rp)
    return out
