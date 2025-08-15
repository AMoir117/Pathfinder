# Pathfinder

A Python console app to search for files by **name** and **(text) content**.  
By default, it searches common user folders like Desktop, Documents, Downloads, Pictures, etc.

## Features
- Search by filename (substring or exact).
- Search inside text-based files (utf-8/latin-1 tolerant).
- Quoted filters:
  - `".pdf"` matches file extension `.pdf`.
  - `"pfp.jpeg"` matches exact filename.
  - `"pfp"` matches exact stem (filename without extension).
- Simple relevance scoring (filename, extension, content hits).
- Cross-platform: macOS / Linux / Windows.

## Install (editable dev mode)
```bash
pip install -e .
