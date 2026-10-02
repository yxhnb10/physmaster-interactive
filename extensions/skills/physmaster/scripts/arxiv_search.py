#!/usr/bin/env python3
"""Standalone arXiv paper search tool.

Usage:
    python arxiv_search.py --query "quantum error correction" --top_k 5
    python arxiv_search.py --query "ti:black hole au:Hawking" --top_k 3
    python arxiv_search.py --query "2301.08727" --top_k 1
"""

import argparse
import sys
from pathlib import Path


def _find_project_root():
    """Find PHY_Master project root by looking for marker files."""
    p = Path(__file__).resolve().parent
    for _ in range(10):
        if (p / "core").is_dir() and (p / "LANDAU").is_dir():
            return p
        p = p.parent
    raise RuntimeError(
        "Cannot find PHY_Master project root. "
        "Expected to find 'core/' and 'LANDAU/' directories."
    )


_PROJECT_ROOT = _find_project_root()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from LANDAU.library import LibraryRetriever


def main():
    parser = argparse.ArgumentParser(description="Search arXiv for physics papers")
    parser.add_argument("--query", "-q", type=str, required=True, help="Search query or arXiv ID")
    parser.add_argument("--top_k", "-k", type=int, default=5, help="Number of results")
    args = parser.parse_args()

    lib = LibraryRetriever()
    results = lib.search(query=args.query, top_k=args.top_k)

    if not results:
        print("No results found.")
        return

    print(f"Found {len(results)} results for: {args.query}\n")
    print(lib.format_for_llm(results))


if __name__ == "__main__":
    main()
