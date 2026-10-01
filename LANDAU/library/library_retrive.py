"""Library retriever for PhysMaster.

Wraps the ArxivRetriever to provide `search` / `parse` / `format_for_llm`
methods compatible with the tool interface used by Supervisor, Critic, and
Theoretician.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .arxiv_retriever import ArxivRetriever


class LibraryRetriever:
    """arXiv-based library retriever.

    Provides the same public interface (search, parse, format_for_llm,
    format_parsed_for_llm) as the original MCP-based retriever so the
    rest of the codebase needs no changes.
    """

    def __init__(self) -> None:
        self._arxiv = ArxivRetriever()

    # ── Public API ───────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        sources: List[str] | None = None,
    ) -> List[Dict[str, Any]]:
        return self.search(query=query, top_k=top_k)

    def search(
        self,
        query: str,
        top_k: int = 5,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Search arXiv for papers matching the query.

        Returns a list of dicts with keys: title, link, snippet, text,
        plus extra arXiv fields (authors, arxiv_id, pdf_url, abstract).
        """
        results = self._arxiv.search(query=query, top_k=top_k)

        # Normalize to the format expected by the rest of the codebase
        normalized: List[Dict[str, Any]] = []
        for paper in results:
            if "error" in paper:
                continue
            normalized.append({
                "title": paper.get("title", ""),
                "link": paper.get("pdf_url", ""),
                "snippet": paper.get("abstract", "")[:300],
                "text": paper.get("abstract", ""),
                "authors": paper.get("authors", []),
                "arxiv_id": paper.get("arxiv_id", ""),
                "published": paper.get("published", ""),
            })
        return normalized

    # ── Formatting helpers ───────────────────────────────────────

    def format_for_llm(self, results: List[Dict[str, Any]]) -> str:
        """Format search results as readable text for the LLM."""
        if not results:
            return "[arXiv search returned no results]"
        lines = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            link = r.get("link", "")
            text = r.get("text", "") or r.get("snippet", "")
            authors = r.get("authors", [])
            authors_str = ", ".join(authors[:3])
            if len(authors) > 3:
                authors_str += " et al."

            lines.append(f"{i}. {title}")
            if authors_str:
                lines.append(f"   Authors: {authors_str}")
            lines.append(f"   URL: {link}")
            lines.append(f"   {text[:300]}")
            lines.append("")
        return "\n".join(lines)


if __name__ == "__main__":
    lib = LibraryRetriever()

    print("=" * 50)
    print("Test: arXiv search")
    print("=" * 50)
    try:
        results = lib.search(query="quantum error correction", top_k=3)
        print(f"Found {len(results)} results:\n")
        for i, r in enumerate(results, 1):
            print(f"  {i}. {r.get('title', '')}")
            print(f"     {r.get('link', '')}")
            print(f"     {r.get('snippet', '')[:100]}...")
        print("\nFormatted:")
        print(lib.format_for_llm(results)[:500])
    except Exception as e:
        print(f"Search failed: {e}")

    print("\n" + "=" * 50)
    print("Done")
    print("=" * 50)
