"""L3 Wisdom Store — distill, store, and retrieve cross-task wisdom.

Wisdom chunks are stored in the same FAISS index as textbook chunks so that
future tasks automatically retrieve relevant historical wisdom via the existing
PriorRetriever pipeline.
"""

from __future__ import annotations

import json
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

try:
    import faiss
except ImportError:
    faiss = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

from utils.llm_client import call_model_without_tools


class WisdomStore:
    """L3 memory layer. After a task finishes, distills cross-subtask
    wisdom via LLM and appends it to the shared FAISS prior index so
    future tasks can retrieve it through the normal PriorRetriever."""

    def __init__(self, prior_store_dir: str | Path, config_path: str | Path | None = None):
        self.prior_dir = Path(prior_store_dir)
        self.knowledge_dir = self.prior_dir / "knowledge"
        self.index_dir = self.prior_dir / "index"
        self.knowledge_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        self.chunks_path = self.knowledge_dir / "chunks.jsonl"
        self.faiss_path = self.index_dir / "index.faiss"
        self.id_map_path = self.index_dir / "id_map.jsonl"
        self.index_meta_path = self.index_dir / "index_meta.json"

        self.config_path = config_path

        prompts_dir = Path(__file__).resolve().parents[2] / "prompts"
        self._sys_prompt = (prompts_dir / "wisdom_system_prompt.txt").read_text(encoding="utf-8")
        self._user_prompt_tpl = (prompts_dir / "wisdom_prompt.txt").read_text(encoding="utf-8")

        self._emb_model: Optional[SentenceTransformer] = None


    def _get_emb_model(self) -> SentenceTransformer | None:
        """Lazy-load the sentence transformer model on first use.
        Returns None if unavailable."""
        if self._emb_model is None:
            if SentenceTransformer is None:
                print("[Wisdom] Warning: sentence-transformers not installed. Wisdom will be saved to chunks.jsonl but not indexed.")
                return None
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                self._emb_model = SentenceTransformer("BAAI/bge-small-en-v1.5", device=device)
            except Exception as e:
                print(f"[Wisdom] Warning: Failed to load embedding model: {e}. Wisdom will be saved to chunks.jsonl but not indexed.")
                return None
        return self._emb_model

    def extract_wisdom(
        self,
        structured_problem: Dict[str, Any],
        trajectory: List[Dict[str, Any]],
        completed_subtasks: List[Any],
    ) -> str:
        """Collect L2 knowledge from trajectory nodes and ask the LLM
        to distill them into a single wisdom paragraph."""
        task_description = json.dumps(structured_problem, ensure_ascii=False, indent=2)

        knowledge_parts: List[str] = []
        for node in trajectory:
            k = node.get("memory") or node.get("l2_knowledge") or node.get("knowledge", "")
            if k:
                knowledge_parts.append(k)
        trajectory_knowledge = "\n---\n".join(knowledge_parts) if knowledge_parts else "(no knowledge distilled)"

        subtask_strs: List[str] = []
        for st in (completed_subtasks or []):
            if isinstance(st, dict):
                subtask_strs.append(json.dumps(st, ensure_ascii=False, indent=2))
            else:
                subtask_strs.append(str(st))
        completed_str = "\n".join(subtask_strs) if subtask_strs else "(none)"

        user_prompt = self._user_prompt_tpl.format(
            task_description=task_description,
            trajectory_knowledge=trajectory_knowledge,
            completed_subtasks=completed_str,
        )

        wisdom_text = call_model_without_tools(
            system_prompt=self._sys_prompt,
            user_prompt=user_prompt,
            config_path=self.config_path,
        )
        return wisdom_text

    def store_wisdom(self, task_description: str, wisdom_text: str, task_name: str):
        """Append a wisdom chunk to chunks.jsonl, encode it, and
        incrementally add the embedding to the FAISS index."""
        if not wisdom_text.strip():
            print("[Wisdom] Empty wisdom text, skipping store.")
            return

        # Check if embedding model is available before doing anything
        emb_model = self._get_emb_model()
        if emb_model is None:
            print("[Wisdom] Embedding model unavailable. Skipping wisdom storage (no index means no retrieval).")
            return

        if faiss is None:
            print("[Wisdom] faiss not available. Skipping wisdom storage (no index means no retrieval).")
            return

        year = datetime.datetime.now().year
        chunk_id = f"wisdom:{task_name}:0001"
        context_prefix = f"[Task Wisdom | {task_name}] "

        chunk = {
            "chunk_id": chunk_id,
            "parent_chunk_id": None,
            "text": wisdom_text,
            "context_prefix": context_prefix,
            "source": {
                "source_id": f"wisdom:{task_name}",
                "title": task_name,
                "authors": ["system"],
                "year": year,
                "edition": "wisdom",
            },
            "locator": {
                "chapter": "0",
                "section": "0",
                "chapter_title": "",
                "section_title": "",
                "page_start": 0,
                "page_end": 0,
            },
            "citation": f"Task Wisdom: {task_name}",
            "keywords": self._extract_keywords(wisdom_text),
            "prev_chunk_id": None,
            "next_chunk_id": None,
        }

        with self.chunks_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        print(f"[Wisdom] Chunk appended to {self.chunks_path}")

        embed_text = context_prefix + wisdom_text
        embedding = emb_model.encode(
            [embed_text],
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        embedding = np.asarray(embedding, dtype="float32")

        if self.faiss_path.exists():
            index = faiss.read_index(str(self.faiss_path))
            start_id = index.ntotal
            index.add(embedding)
            faiss.write_index(index, str(self.faiss_path))
        else:
            dim = embedding.shape[1]
            index = faiss.IndexFlatIP(dim)
            start_id = 0
            index.add(embedding)
            faiss.write_index(index, str(self.faiss_path))

        with self.id_map_path.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps({"chunk_id": chunk_id, "index_id": start_id}, ensure_ascii=False)
                + "\n"
            )

        meta = {}
        if self.index_meta_path.exists():
            with self.index_meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)
        meta["num_vectors"] = index.ntotal
        with self.index_meta_path.open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        print(f"[Wisdom] FAISS index updated: {index.ntotal} vectors (added 1 wisdom chunk)")

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """Simple keyword extraction (mirrors PriorStore._extract_keywords_pure)."""
        import re
        from collections import Counter

        phrases = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", text)
        words = re.findall(r"\b[a-z]{7,}\b", text.lower())
        stopwords = {"following", "between", "results", "through", "using", "shown"}
        common = [w for w, _ in Counter(words).most_common(10) if w not in stopwords]
        return list(dict.fromkeys(phrases + common))[:5]

    def save(
        self,
        structured_problem: Dict[str, Any],
        trajectory: List[Dict[str, Any]],
        completed_subtasks: List[Any],
        task_name: str,
    ):
        """End-to-end: extract wisdom from trajectory then persist it."""
        print(f"[HCC-L3] Extracting task-level wisdom from {len(trajectory)} trajectory nodes...")
        wisdom_text = self.extract_wisdom(structured_problem, trajectory, completed_subtasks)
        print(f"[HCC-L3] Wisdom extracted ({len(wisdom_text)} chars):")
        print(f"[HCC-L3] >>> {wisdom_text[:200]}{'...' if len(wisdom_text) > 200 else ''}")

        task_description = structured_problem.get("task_description", task_name)
        self.store_wisdom(task_description, wisdom_text, task_name)
        print(f"[HCC-L3] Wisdom saved to prior index for task: {task_name}")
