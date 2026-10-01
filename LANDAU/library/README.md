# LANDAU Library Module

arXiv paper search and retrieval for PhysMaster.

## Overview

The library module provides one tool for the Theoretician, Supervisor, and Critic agents:

- **`library_search`** — Search arXiv for physics papers by keyword, author, title, category, or arXiv ID. Returns title, authors, abstract, and PDF link.

## Implementation

- **`arxiv_retriever.py`** — Direct HTTP client for the arXiv API v1 (no external dependencies)
- **`library_retrive.py`** — Wrapper that provides the interface expected by the rest of PhysMaster

Uses only Python stdlib (`urllib`, `xml.etree.ElementTree`) — no extra packages required.

## Usage

Agents can call this tool during problem-solving:

```python
# Search by keyword
library_search(query="quantum error correction surface code", top_k=5)

# Search by field prefix
library_search(query="ti:black hole au:Hawking", top_k=3)

# Look up a specific paper by arXiv ID
library_search(query="2301.08727", top_k=1)
```

## arXiv API

- **Endpoint:** `http://export.arxiv.org/api/query`
- **Rate limit:** 3 seconds between requests (enforced by `time.sleep(3)`)
- **Search syntax:** Supports field prefixes like `ti:` (title), `au:` (author), `abs:` (abstract), `cat:` (category)
- **Documentation:** https://arxiv.org/help/api/

## Configuration

Enable/disable in `config.yaml`:

```yaml
landau:
  library_enabled: true
```

No additional configuration needed — arXiv API is public and requires no authentication. If initialization fails (e.g., network issues), the system prints a warning and automatically disables the tool. The pipeline continues normally.
