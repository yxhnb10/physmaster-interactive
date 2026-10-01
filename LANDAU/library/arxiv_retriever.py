"""arXiv search and retrieval for PhysMaster library module.

Uses the arXiv API v1 (http://export.arxiv.org/api/query) to search
for physics papers and return title, abstract, authors, and PDF links.
"""

from __future__ import annotations

import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List


class ArxivRetriever:
    """Search arXiv for physics papers."""

    BASE_URL = "http://export.arxiv.org/api/query"
    NAMESPACES = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    def __init__(self) -> None:
        pass

    def search(
        self,
        query: str,
        top_k: int = 5,
        sort_by: str = "relevance",
        sort_order: str = "descending",
    ) -> List[Dict[str, Any]]:
        """Search arXiv and return top_k results.

        Parameters
        ----------
        query : str
            Search query (e.g., "quantum field theory", "ti:black hole")
            Supports field prefixes: ti (title), au (author), abs (abstract), cat (category)
        top_k : int
            Number of results to return
        sort_by : str
            "relevance", "lastUpdatedDate", or "submittedDate"
        sort_order : str
            "ascending" or "descending"

        Returns
        -------
        List[Dict]
            Each dict has: title, authors, abstract, pdf_url, arxiv_id, published, updated
        """
        params = {
            "search_query": query,
            "start": 0,
            "max_results": top_k,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
        url = f"{self.BASE_URL}?{urllib.parse.urlencode(params)}"

        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                xml_data = response.read()
        except Exception as e:
            return [{"error": f"arXiv API request failed: {e}"}]

        # Rate limit: arXiv asks for 3 seconds between requests
        time.sleep(3)

        return self._parse_feed(xml_data)

    def _parse_feed(self, xml_data: bytes) -> List[Dict[str, Any]]:
        """Parse arXiv Atom feed XML into a list of paper dicts."""
        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError as e:
            return [{"error": f"XML parse error: {e}"}]

        entries = root.findall("atom:entry", self.NAMESPACES)
        results = []

        for entry in entries:
            title_elem = entry.find("atom:title", self.NAMESPACES)
            summary_elem = entry.find("atom:summary", self.NAMESPACES)
            published_elem = entry.find("atom:published", self.NAMESPACES)
            updated_elem = entry.find("atom:updated", self.NAMESPACES)
            id_elem = entry.find("atom:id", self.NAMESPACES)

            # Authors
            author_elems = entry.findall("atom:author", self.NAMESPACES)
            authors = []
            for author in author_elems:
                name_elem = author.find("atom:name", self.NAMESPACES)
                if name_elem is not None and name_elem.text:
                    authors.append(name_elem.text.strip())

            # PDF link
            pdf_url = None
            for link in entry.findall("atom:link", self.NAMESPACES):
                if link.get("title") == "pdf":
                    pdf_url = link.get("href")
                    break

            # arXiv ID from the entry ID (e.g., http://arxiv.org/abs/2301.12345v1)
            arxiv_id = ""
            if id_elem is not None and id_elem.text:
                arxiv_id = id_elem.text.split("/abs/")[-1]

            results.append({
                "title": title_elem.text.strip() if title_elem is not None and title_elem.text else "",
                "authors": authors,
                "abstract": summary_elem.text.strip() if summary_elem is not None and summary_elem.text else "",
                "pdf_url": pdf_url or "",
                "arxiv_id": arxiv_id,
                "published": published_elem.text.strip() if published_elem is not None and published_elem.text else "",
                "updated": updated_elem.text.strip() if updated_elem is not None and updated_elem.text else "",
            })

        return results

    def format_for_llm(self, results: List[Dict[str, Any]]) -> str:
        """Format search results as a readable string for the LLM."""
        if not results:
            return "[arXiv search returned no results]"

        if len(results) == 1 and "error" in results[0]:
            return f"[arXiv search error: {results[0]['error']}]"

        lines = []
        for i, paper in enumerate(results, 1):
            authors_str = ", ".join(paper.get("authors", [])[:3])
            if len(paper.get("authors", [])) > 3:
                authors_str += " et al."

            lines.append(f"[{i}] {paper.get('title', 'No title')}")
            lines.append(f"    Authors: {authors_str}")
            lines.append(f"    arXiv ID: {paper.get('arxiv_id', 'N/A')}")
            lines.append(f"    Published: {paper.get('published', 'N/A')[:10]}")
            lines.append(f"    Abstract: {paper.get('abstract', '')[:300]}...")
            lines.append(f"    PDF: {paper.get('pdf_url', 'N/A')}")
            lines.append("")

        return "\n".join(lines)
