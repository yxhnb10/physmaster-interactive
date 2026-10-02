# Prior Knowledge System (RAG)

## Overview

`LANDAU/prior/` implements a retrieval-augmented generation pipeline that provides textbook knowledge, paper excerpts, and cross-task wisdom to the agents during solving.

## Architecture

```
Source documents (PDF/MD/TXT)
        │
        ▼
┌─────────────────┐
│   prior_store.py │  Ingestion: convert → chunk → embed → FAISS index
└────────┬────────┘
         │
         ▼
┌─────────────────────┐
│  FAISS IndexFlatIP   │  Dense vector index (bge-small-en-v1.5)
│  + BM25 sparse index │  Built from child chunks
└────────┬────────────┘
         │
         ▼
┌──────────────────────┐
│  prior_retrieve.py    │  Hybrid retrieval: dense + BM25 → RRF → rerank
└────────┬─────────────┘
         │
         ▼
  Agent prompt context
```

## Parent-Child Chunking

Source documents are split into two tiers:

- **Parent chunks** (max 4000 chars, 400 char overlap): Broad context windows
- **Child chunks** (max 1200 chars, 240 char overlap): Embedded and indexed

Child chunks are what gets embedded and retrieved. When a child is matched, the parent chunk provides broader context. This two-tier design balances retrieval precision with context completeness.

## Retrieval Pipeline

When `prior_search` is called:

1. **Query expansion**: LLM rewrites the query into 3 variants (if `rewrite_query: true`)
2. **Dense search**: Each variant is embedded and searched against the FAISS index
3. **Sparse search**: BM25-style scoring against term frequency statistics
4. **Fusion**: Reciprocal Rank Fusion merges dense and sparse results
5. **Metadata filtering**: Apply source_id, chapter, section, keyword filters
6. **Reranking**: Weighted score from dense (0.55), sparse (0.20), overlap (0.10), keyword (0.10), phrase (0.05)
7. **Deduplication**: Remove duplicate parent chunks, enforce source diversity
8. **Context expansion**: Optionally include prev/next chunks

## Ingesting Documents

Place source files in `LANDAU/prior/source/`:

```bash
# Ingest all files in source/
python LANDAU/prior/prior_store.py

# Ingest a single file
python LANDAU/prior/prior_store.py --target path/to/file.pdf

# Full rebuild (delete existing index)
python LANDAU/prior/prior_store.py --reset

# Skip FAISS index build (chunking only)
python LANDAU/prior/prior_store.py --no-index

# List supported source files
python LANDAU/prior/prior_store.py --list-sources
```

Supported formats: `.pdf` (via MinerU conversion), `.md`, `.txt`

Incremental updates: New files are automatically detected and added without re-encoding existing chunks.

## Index Files

After ingestion, the following files are created:

```
LANDAU/prior/
├── source/                        # Place source files here
├── knowledge/
│   ├── chunks.jsonl               # Child chunks (embedded, indexed)
│   └── parent_chunks.jsonl        # Parent chunks (context expansion)
└── index/
    ├── index.faiss                # FAISS IndexFlatIP dense index
    ├── id_map.jsonl               # chunk_id ↔ FAISS index_id mapping
    └── index_meta.json            # Index metadata (model, vector count)
```

## Cross-Task Wisdom

When `wisdom_save_enabled: true`, after each task completes:

1. The LLM reads the best trajectory and distills key insights
2. The wisdom text is embedded with the same model
3. A new chunk is appended to `chunks.jsonl`
4. The embedding is added to the FAISS index
5. Future tasks retrieve this wisdom alongside textbook knowledge

This creates a feedback loop: solved tasks improve future solving.

## Graceful Degradation

| Situation | Behavior |
|---|---|
| `faiss-cpu` not installed | Warning printed, prior search returns "not available" |
| Index files don't exist yet | Warning printed, prior search returns empty |
| Embedding model fails to load | Warning printed, wisdom storage skipped entirely |
| `sentence-transformers` missing | Warning printed, both retrieval and wisdom disabled |

The pipeline always continues — prior knowledge is a nice-to-have, not a hard requirement.
