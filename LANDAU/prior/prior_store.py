from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
from tqdm import tqdm

try:
    import faiss
except ImportError:
    faiss = None

try:
    import torch
except ImportError:
    torch = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

try:
    from nltk.tokenize import sent_tokenize as _nltk_sent_tokenize
except ImportError:
    _nltk_sent_tokenize = None

_ABBREV_SHIELD = {
    "Eq.": "EQ__PH", "Eqs.": "EQS__PH", "Fig.": "FIG__PH",
    "Figs.": "FIGS__PH", "Ref.": "REF__PH", "Refs.": "REFS__PH",
    "Vol.": "VOL__PH", "Ch.": "CH__PH", "Sec.": "SEC__PH",
    "Chap.": "CHAP__PH", "App.": "APP__PH", "et al.": "ETAL__PH",
    "e.g.": "EG__PH", "i.e.": "IE__PH", "vs.": "VS__PH",
    "cf.": "CF__PH", "resp.": "RESP__PH", "approx.": "APPROX__PH",
    "Dr.": "DR__PH", "Prof.": "PROF__PH", "No.": "NO__PH",
    "no.": "NO2__PH", "p.": "P__PH", "pp.": "PP__PH",
}
_ABBREV_UNSHIELD = {v: k for k, v in _ABBREV_SHIELD.items()}
_DISPLAY_MATH_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)


CURRENT_DIR = Path(__file__).resolve().parent

CFG = {
    "dirs": {
        "source": CURRENT_DIR / "source",
        "out": CURRENT_DIR / "out",
        "knowledge": CURRENT_DIR / "knowledge",
        "index": CURRENT_DIR / "index",
    },
    "embedding": {
        "model": "BAAI/bge-small-en-v1.5",
        "normalize_embeddings": True,
        "batch_size_cuda": 256,
        "batch_size_cpu": 32,
        "show_progress_bar": True,
        "use_fp16_on_cuda": True,
    },
    "chunking": {
        "child": {
            "max_chars": 1200,
            "overlap_chars": 240,
            "min_chars": 120
        },
        "parent": {
            "max_chars": 2400,
            "overlap_chars": 300,
            "min_chars": 200
        }
    },
    "conversion": {
        "enabled": True,
        "skip_existing_conversion": True,
        "keep_intermediate_outputs": True,
        "mineru_mode": "hybrid_auto",
    },
    "ingest": {
        "skip_existing_sources": True,
    },
    "chunk_id_fmt": "{}:ch{}:sec{}:{:04d}",
    "parent_chunk_id_fmt": "parent:{}:ch{}:sec{}:{:04d}",
    "default_chap_sec": ("0", "0"),
    "target_file": "",
    "supported_exts": [".pdf", ".md", ".txt"],
}


def _shield_abbreviations(text: str) -> str:
    for abbr, ph in _ABBREV_SHIELD.items():
        text = text.replace(abbr, ph)
    return text


def _unshield_abbreviations(text: str) -> str:
    for ph, abbr in _ABBREV_UNSHIELD.items():
        text = text.replace(ph, abbr)
    return text


def _split_sentences_physics(text: str) -> List[str]:
    """Split text into sentences while respecting physics abbreviations
    like 'Eq.', 'Fig.', 'et al.' and preserving display-math blocks."""
    math_blocks: List[str] = []

    def _stash_math(m: re.Match) -> str:
        idx = len(math_blocks)
        math_blocks.append(m.group(0))
        return f" MATHBLK{idx} "

    shielded = _DISPLAY_MATH_RE.sub(_stash_math, text)
    shielded = _shield_abbreviations(shielded)

    if _nltk_sent_tokenize is not None:
        raw_sents = _nltk_sent_tokenize(shielded)
    else:
        raw_sents = re.split(r"(?<=[\.\!\?])\s+", shielded)

    restored: List[str] = []
    for s in raw_sents:
        s = _unshield_abbreviations(s)
        for idx, block in enumerate(math_blocks):
            s = s.replace(f"MATHBLK{idx}", block)
        s = s.strip()
        if s:
            restored.append(s)
    return restored


class PriorStore:
    """Ingestion pipeline for the prior knowledge base. Converts source
    PDFs/markdown/text into parent-child chunk pairs and builds a FAISS
    dense index for retrieval."""

    def __init__(self, cfg: Dict | None = None):
        missing_deps = []
        if faiss is None:
            missing_deps.append("faiss")
        if torch is None:
            missing_deps.append("torch")
        if SentenceTransformer is None:
            missing_deps.append("sentence-transformers")
        if missing_deps:
            raise RuntimeError(
                "Missing dependencies for prior store: "
                + ", ".join(missing_deps)
                + ". Install the project requirements before running this pipeline."
            )

        self.cfg = cfg or CFG
        self.dirs = {name: Path(path) for name, path in self.cfg["dirs"].items()}
        for path in self.dirs.values():
            path.mkdir(parents=True, exist_ok=True)

        requested_device = os.environ.get("PHY_PRIOR_DEVICE", "").strip().lower()
        if requested_device:
            self.device = requested_device
        else:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"

        self.embedding_cfg = self.cfg["embedding"]
        print(f"[*] Initializing embedding model {self.embedding_cfg['model']} on {self.device}...")
        self.emb_model = SentenceTransformer(self.embedding_cfg["model"], device=self.device)
        if self.device.startswith("cuda") and self.embedding_cfg.get("use_fp16_on_cuda", False):
            try:
                self.emb_model.half()
                print("[*] Enabled fp16 for embedding model on CUDA.")
            except Exception as e:
                print(f"[!] Failed to enable fp16, continue with default precision: {e}")

        self.knowledge_path = self.dirs["knowledge"] / "chunks.jsonl"
        self.parent_knowledge_path = self.dirs["knowledge"] / "parent_chunks.jsonl"
        self.faiss_path = self.dirs["index"] / "index.faiss"
        self.id_map_path = self.dirs["index"] / "id_map.jsonl"
        self.parent_id_map_path = self.dirs["index"] / "parent_id_map.jsonl"
        self.index_meta_path = self.dirs["index"] / "index_meta.json"
        self.chunks_data: List[Dict] = []      
        self.parent_chunks_data: List[Dict] = []   
        self.new_chunks_data: List[Dict] = []
        self.new_parent_chunks_data: List[Dict] = []

    def _embedding_batch_size(self) -> int:
        env_batch = os.environ.get("PHY_PRIOR_EMBED_BATCH")
        if env_batch:
            return max(1, int(env_batch))
        if self.device.startswith("cuda"):
            return int(self.embedding_cfg.get("batch_size_cuda", 256))
        return int(self.embedding_cfg.get("batch_size_cpu", 32))

    def _safe_unlink(self, path: Path):
        if path.exists():
            path.unlink()

    def _load_existing_id_map(self) -> Dict[int, str]:
        if not self.id_map_path.exists():
            return {}
        id_map: Dict[int, str] = {}
        with self.id_map_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if "index_id" in item and "chunk_id" in item:
                    id_map[int(item["index_id"])] = item["chunk_id"]
        return id_map

    def _load_index_meta(self) -> Dict:
        if not self.index_meta_path.exists():
            return {}
        with self.index_meta_path.open("r", encoding="utf-8") as f:
            return json.load(f) or {}

    def _get_id_prefix_from_filename(self, filename: str) -> str:
        name = Path(filename).stem.lower()
        author = re.search(r"[a-z]+", name).group() if re.search(r"[a-z]+", name) else "phys"
        year = re.search(r"\d{4}", name).group() if re.search(r"\d{4}", name) else "2024"
        return f"{author}{year}"

    def _extract_paper_meta_concise(self, elements: List[Dict]) -> Tuple[str, List[str]]:
        title = "Unknown Title"
        authors = ["Unknown Author"]
        title_idx = -1
        for i, element in enumerate(elements):
            if element.get("text_level") == 1:
                title = element.get("text", "").strip()
                title_idx = i
                break
        if title_idx != -1:
            for idx in range(title_idx + 1, min(title_idx + 5, len(elements))):
                raw_text = elements[idx].get("text", "").strip()
                if not raw_text:
                    continue
                clean_text = re.sub(r"[^a-zA-Z\s,]", "", raw_text)
                names = re.findall(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", clean_text)
                if names:
                    authors = names
                    break
        return title, authors

    def _extract_keywords_pure(self, text: str) -> List[str]:
        phrases = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", text)
        words = re.findall(r"\b[a-z]{7,}\b", text.lower())
        stopwords = {"following", "between", "results", "through", "using", "shown"}
        common = [word for word, _ in Counter(words).most_common(10) if word not in stopwords]
        return list(dict.fromkeys(phrases + common))[:5]

    def _split_text_with_overlap(self, text: str, chunking_cfg: Dict) -> List[str]:
        """Split text into chunks of max_chars with sentence-aware boundaries.
        Adjacent chunks overlap by overlap_chars to preserve context."""
        clean_text = re.sub(r"\s+", " ", text or "").strip()
        if not clean_text:
            return []

        max_chars = int(chunking_cfg["max_chars"])
        overlap_chars = int(chunking_cfg["overlap_chars"])
        min_chars = int(chunking_cfg["min_chars"])

        if len(clean_text) <= max_chars:
            return [clean_text] if len(clean_text) >= min_chars else ([clean_text] if clean_text else [])

        sentences = _split_sentences_physics(clean_text)

        chunks: List[str] = []
        current = ""

        for sentence in sentences:
            if not sentence:
                continue

            candidate = f"{current} {sentence}".strip() if current else sentence

            if current and len(candidate) > max_chars:
                chunks.append(current)

                if overlap_chars > 0:
                    words = current.split()
                    overlap_text = ""
                    for word in reversed(words):
                        if len(f"{word} {overlap_text}".strip()) <= overlap_chars:
                            overlap_text = f"{word} {overlap_text}".strip()
                        else:
                            break
                    current = f"{overlap_text} {sentence}".strip() if overlap_text else sentence
                else:
                    current = sentence

                if len(current) > max_chars:
                    words = current.split()
                    truncated = ""
                    for word in words:
                        if len(f"{truncated} {word}".strip()) <= max_chars:
                            truncated = f"{truncated} {word}".strip()
                        else:
                            break
                    current = truncated
            else:
                current = candidate

        if current:
            chunks.append(current)

        # Merge short chunks with neighbors
        merged: List[str] = []
        for chunk in chunks:
            if merged and len(chunk) < min_chars:
                # Try merging with previous if it won't exceed max_chars
                if len(merged[-1]) + 1 + len(chunk) <= max_chars:
                    merged[-1] = f"{merged[-1]} {chunk}"
                    continue
            merged.append(chunk)
        # Final pass: if last chunk is still too short, merge backward
        if len(merged) > 1 and len(merged[-1]) < min_chars:
            if len(merged[-2]) + 1 + len(merged[-1]) <= max_chars:
                merged[-2] = f"{merged[-2]} {merged[-1]}"
                merged.pop()

        return merged

    def _identify_header_strict(self, text: str) -> Tuple[str | None, str, str, str]:
        """Detect whether text is a chapter or section header by matching
        leading numbers like '1.2' or 'Chapter 3'. Returns
        (header_type, number_value, inferred_chapter, title_text).
        title_text is the part after the numeric prefix."""
        clean_text = text.strip()
        sec_match = re.match(r"^(\d+(?:\.\d+)+)\s*(.*)", clean_text)
        if sec_match:
            value = sec_match.group(1)
            title_text = sec_match.group(2).strip()
            return "sec", value, value.split(".")[0], title_text
        ch_match = re.match(r"^(?:Chapter\s+)?(\d+)(?!\.)\s*(.*)", clean_text, re.I)
        if ch_match:
            value = ch_match.group(1)
            title_text = ch_match.group(2).strip()
            return "ch", value, value, title_text
        return None, "0", "0", ""

    def _load_plain_text_elements(self, path: Path, ext: str) -> List[Dict]:
        elements = []
        with path.open("r", encoding="utf-8") as f:
            for line in f.readlines():
                text = line.strip()
                if not text:
                    continue
                element = {"text": text, "type": "paragraph", "page_idx": 0}
                if ext == ".md" and text.startswith("#"):
                    level = len(re.match(r"^#+", text).group(0))
                    element["text_level"] = level
                    element["type"] = "title"
                    element["text"] = text.lstrip("#").strip()
                elements.append(element)
        if ext == ".txt" and elements:
            elements[0]["text_level"] = 1
            elements[0]["type"] = "title"
        return elements

    def _run_mineru_conversion(self, source_file: Path) -> Path | None:
        """Run the MinerU PDF-to-JSON converter as a subprocess.
        Returns the path to the content_list.json, or None on failure.
        Skips conversion if the output already exists."""
        if not self.cfg["conversion"]["enabled"]:
            return None

        base_name = source_file.stem
        mode = self.cfg["conversion"]["mineru_mode"]
        json_path = self.dirs["out"] / base_name / mode / f"{base_name}_content_list.json"
        if json_path.exists() and self.cfg["conversion"].get("skip_existing_conversion", True):
            print(f" [SKIP] Existing conversion found: {source_file.name}")
            return json_path

        cmd = ["mineru", "-p", str(source_file.resolve()), "-o", str(self.dirs["out"].resolve())]
        print("\n" + "=" * 50)
        print(f"[*] MinerU conversion: {source_file.name}")
        try:
            subprocess.run(cmd, check=True)
            print(f"[+] {source_file.name} finished MinerU conversion.")
        except subprocess.CalledProcessError as e:
            print(f"[!] MinerU error ({e.returncode}), skipping this file.")
            return None
        except Exception as e:
            print(f"[!] Unknown MinerU error: {e}")
            return None

        if not json_path.exists():
            print(f"[!] Converted JSON not found: {json_path}")
            return None
        return json_path

    def _load_elements_for_file(self, file_path: Path) -> tuple[List[Dict], bool] | tuple[None, None]:
        ext = file_path.suffix.lower()
        is_plain_text = ext in [".md", ".txt"]
        if ext == ".pdf":
            json_path = self._run_mineru_conversion(file_path)
            if json_path is None:
                return None, None
            print(f"[*] Chunking and metadata extraction: {file_path.name}")
            with json_path.open("r", encoding="utf-8") as f:
                return json.load(f), False

        print(f"[*] Chunking and metadata extraction: {file_path.name}")
        elements = self._load_plain_text_elements(file_path, ext)
        if not elements:
            print(f"[!] Warning: Empty text file: {file_path.name}")
            return None, None
        return elements, is_plain_text

    def _gather_source_files(self, target_path: str = "") -> List[Path]:
        target = (target_path or self.cfg.get("target_file", "")).strip()
        if target:
            path = Path(target).expanduser().resolve()
            if not path.is_file() or path.suffix.lower() not in self.cfg["supported_exts"]:
                raise FileNotFoundError(f"Invalid target file: {path}")
            return [path]
        source_files = sorted(
            path for path in self.dirs["source"].iterdir()
            if path.is_file() and path.suffix.lower() in self.cfg["supported_exts"]
        )
        if not self.cfg.get("ingest", {}).get("skip_existing_sources", True):
            return source_files

        existing_source_ids = {
            chunk.get("source", {}).get("source_id")
            for chunk in self.chunks_data
            if isinstance(chunk, dict)
        }
        filtered_files = [path for path in source_files if path.name not in existing_source_ids]
        skipped = len(source_files) - len(filtered_files)
        if skipped:
            print(f"[*] Skip {skipped} existing source files already ingested.")
        return filtered_files

    def _build_parent_chunks_from_text(self, full_text: str, fname: str,
                                        real_title: str, real_authors: List[str],
                                        year_val: str, c_ch: str, c_sec: str,
                                        ch_title: str, sec_title: str,
                                        page_start: int, page_end: int) -> List[Dict]:
        parent_chunking_cfg = self.cfg["chunking"]["parent"]
        parent_texts = self._split_text_with_overlap(full_text, parent_chunking_cfg)

        parent_chunks = []
        for idx, text in enumerate(parent_texts):
            parent_chunk_id = self.cfg["parent_chunk_id_fmt"].format(
                Path(fname).stem, c_ch, c_sec, idx + 1
            )
            chapter_str = f"Ch.{c_ch}" if c_sec == "0" else f"Ch.{c_sec}"
            parent_chunk = {
                "chunk_id": parent_chunk_id,
                "text": text,
                "source": {
                    "source_id": fname,
                    "title": real_title,
                    "authors": real_authors,
                    "year": int(year_val),
                    "edition": "Original",
                },
                "locator": {
                    "chapter": c_ch,
                    "section": c_sec,
                    "chapter_title": ch_title,
                    "section_title": sec_title,
                    "page_start": page_start,
                    "page_end": page_end,
                },
                "citation": f"{real_authors[0]} et al. ({year_val}), {chapter_str}, pp.{page_start}-{page_end}",
                "keywords": self._extract_keywords_pure(text),
                "child_chunk_ids": [],
                "prev_chunk_id": None,
                "next_chunk_id": None,
            }
            parent_chunks.append(parent_chunk)

        for idx in range(len(parent_chunks)):
            if idx > 0:
                parent_chunks[idx]["prev_chunk_id"] = parent_chunks[idx - 1]["chunk_id"]
            if idx < len(parent_chunks) - 1:
                parent_chunks[idx]["next_chunk_id"] = parent_chunks[idx + 1]["chunk_id"]

        return parent_chunks

    def _flush_parent_buffer(
        self, current_parent_text: str, fname: str,
        real_title: str, real_authors: List[str], year_val: str,
        id_prefix: str, c_ch: str, c_sec: str,
        ch_title: str, sec_title: str,
        page_start: int, page_end: int,
        child_chunks: List[Dict], parent_chunks: List[Dict],
    ) -> None:
        """Finalize accumulated section text into parent chunks, then
        split each parent into child chunks with metadata and citation."""
        text = current_parent_text.strip()
        if not text:
            return

        new_parent_chunks = self._build_parent_chunks_from_text(
            text, fname, real_title, real_authors,
            year_val, c_ch, c_sec, ch_title, sec_title,
            page_start, page_end,
        )
        parent_chunks.extend(new_parent_chunks)

        # Build context prefix using actual titles
        section_label = sec_title or ch_title
        if section_label:
            ctx = f"[{real_title} | {section_label}] "
        else:
            ctx = ""

        child_chunking_cfg = self.cfg["chunking"]["child"]
        for parent_chunk in new_parent_chunks:
            parent_chunk_id = parent_chunk["chunk_id"]
            child_texts = self._split_text_with_overlap(parent_chunk["text"], child_chunking_cfg)

            for child_text in child_texts:
                seq_idx = len(child_chunks) + 1
                chunk_id = self.cfg["chunk_id_fmt"].format(
                    id_prefix, c_ch, c_sec, seq_idx
                )
                chapter_str = f"Ch.{c_ch}" if c_sec == "0" else f"Ch.{c_sec}"

                eq_id = ""
                tag_match = re.search(r"\\tag\s*\{([^}]+)\}", child_text)
                if tag_match:
                    eq_id = f"({tag_match.group(1)})"

                child_chunk = {
                    "chunk_id": chunk_id,
                    "parent_chunk_id": parent_chunk_id,
                    "text": child_text,
                    "context_prefix": ctx,
                    "source": {
                        "source_id": fname,
                        "title": real_title,
                        "authors": real_authors,
                        "year": int(year_val),
                        "edition": "Original",
                    },
                    "locator": {
                        "chapter": c_ch,
                        "section": c_sec,
                        "chapter_title": ch_title,
                        "section_title": sec_title,
                        "page_start": page_start,
                        "page_end": page_end,
                        "equation_id": eq_id,
                    },
                    "citation": (
                        f"{real_authors[0]} et al. ({year_val}), {chapter_str}, "
                        f"pp.{page_start}-{page_end}"
                        + (f", Eq. {eq_id}" if eq_id else "")
                    ),
                    "keywords": self._extract_keywords_pure(child_text),
                    "prev_chunk_id": None,
                    "next_chunk_id": None,
                }
                child_chunks.append(child_chunk)
                parent_chunk["child_chunk_ids"].append(chunk_id)

    def _build_chunks_from_elements(self, elements: List[Dict], fname: str, is_plain_text: bool) -> Tuple[List[Dict], List[Dict]]:
        """Walk through parsed document elements, detect chapter/section
        headers, accumulate body text, and flush into parent-child chunk
        pairs at each section boundary."""
        real_title, real_authors = self._extract_paper_meta_concise(elements)
        id_prefix = self._get_id_prefix_from_filename(fname)
        year_match = re.search(r"\d{4}", id_prefix)
        year_val = year_match.group() if year_match else "2024"

        c_ch, c_sec = self.cfg["default_chap_sec"]
        c_ch_title = ""   # current chapter title text
        c_sec_title = ""   # current section title text
        child_chunks: List[Dict] = []
        parent_chunks: List[Dict] = []

        current_parent_text = ""
        current_page_start = 0
        current_page_end = 0

        is_skipping_toc = False
        is_in_numbered_section = True if is_plain_text else False
        one_heading_count = 0

        parent_max = int(self.cfg["chunking"]["parent"]["max_chars"])

        for element in elements:
            text = element.get("text", "").strip()
            if not text:
                continue
            page_idx = int(element.get("page_idx", 0)) + 1

            if not is_plain_text:
                if text.lower() == "contents":
                    is_skipping_toc = True
                    is_in_numbered_section = False
                    continue
                if "text_level" in element or element.get("type") in ("title", "header"):
                    h_type, h_val, inferred_ch, h_title = self._identify_header_strict(text)
                    if h_type:
                        if is_skipping_toc:
                            if re.match(r"^1(\.|\s|$)", text):
                                one_heading_count += 1
                                if one_heading_count == 2:
                                    is_skipping_toc = False
                                    is_in_numbered_section = True
                        else:
                            is_in_numbered_section = True

                            # --- Flush buffer at section boundary ---
                            if current_parent_text.strip():
                                self._flush_parent_buffer(
                                    current_parent_text, fname,
                                    real_title, real_authors, year_val,
                                    id_prefix, c_ch, c_sec,
                                    c_ch_title, c_sec_title,
                                    current_page_start, current_page_end,
                                    child_chunks, parent_chunks,
                                )
                                current_parent_text = ""
                                current_page_start = 0
                                current_page_end = 0

                        if h_type == "ch":
                            c_ch, c_sec = h_val, "0"
                            # Strip trailing page numbers from TOC lines
                            c_ch_title = re.sub(r"\s+\d+\s*$", "", h_title).strip()
                            c_sec_title = ""
                        else:
                            c_sec = h_val
                            c_sec_title = re.sub(r"\s+\d+\s*$", "", h_title).strip()
                            if c_ch == "0":
                                c_ch = inferred_ch
                    else:
                        if not is_in_numbered_section and not is_skipping_toc:
                            is_in_numbered_section = False
                    continue

            if is_skipping_toc or not is_in_numbered_section:
                continue
            if element.get("type") not in ["text", "paragraph", "list", "equation"] or len(text) <= 20:
                continue

            if not current_parent_text:
                current_page_start = page_idx
            current_parent_text += " " + text
            current_page_end = page_idx

            if len(current_parent_text) >= parent_max:
                self._flush_parent_buffer(
                    current_parent_text, fname,
                    real_title, real_authors, year_val,
                    id_prefix, c_ch, c_sec,
                    c_ch_title, c_sec_title,
                    current_page_start, current_page_end,
                    child_chunks, parent_chunks,
                )
                current_parent_text = ""
                current_page_start = 0
                current_page_end = 0

        # Flush remaining text
        if current_parent_text.strip():
            self._flush_parent_buffer(
                current_parent_text, fname,
                real_title, real_authors, year_val,
                id_prefix, c_ch, c_sec,
                c_ch_title, c_sec_title,
                current_page_start, current_page_end,
                child_chunks, parent_chunks,
            )

        for idx in range(len(child_chunks)):
            if idx > 0:
                child_chunks[idx]["prev_chunk_id"] = child_chunks[idx - 1]["chunk_id"]
            if idx < len(child_chunks) - 1:
                child_chunks[idx]["next_chunk_id"] = child_chunks[idx + 1]["chunk_id"]

        for idx in range(len(parent_chunks)):
            if idx > 0:
                parent_chunks[idx]["prev_chunk_id"] = parent_chunks[idx - 1]["chunk_id"]
            if idx < len(parent_chunks) - 1:
                parent_chunks[idx]["next_chunk_id"] = parent_chunks[idx + 1]["chunk_id"]

        return child_chunks, parent_chunks

    def process(self, target_path: str = "", reset_existing: bool = False):
        """Run the full ingestion: gather source files, parse, chunk, and
        persist new child/parent chunks to JSONL. Does not build the index."""
        self.new_chunks_data = []
        self.new_parent_chunks_data = []
        
        if reset_existing:
            self.chunks_data = []
            self.parent_chunks_data = []
            for path in [self.knowledge_path, self.parent_knowledge_path, 
                        self.faiss_path, self.id_map_path, self.parent_id_map_path,
                        self.index_meta_path]:
                self._safe_unlink(path)
        else:
            if self.knowledge_path.exists():
                with self.knowledge_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            self.chunks_data.append(json.loads(line))
                        except Exception:
                            continue
            if self.parent_knowledge_path.exists():
                with self.parent_knowledge_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            self.parent_chunks_data.append(json.loads(line))
                        except Exception:
                            continue

        existing_chunk_ids = {chunk.get("chunk_id") for chunk in self.chunks_data if isinstance(chunk, dict)}
        source_files = self._gather_source_files(target_path)
        if not source_files:
            print("[!] Error: no supported files found.")
            return

        all_child_chunks: List[Dict] = []
        all_parent_chunks: List[Dict] = []
        
        for source_file in tqdm(source_files, desc="Processing source files"):
            loaded = self._load_elements_for_file(source_file)
            if loaded == (None, None):
                continue
            elements, is_plain_text = loaded
            child_chunks, parent_chunks = self._build_chunks_from_elements(
                elements, source_file.name, bool(is_plain_text)
            )
            all_child_chunks.extend(child_chunks)
            all_parent_chunks.extend(parent_chunks)

        new_child_chunks = [chunk for chunk in all_child_chunks if chunk.get("chunk_id") not in existing_chunk_ids]
        new_parent_chunks = [chunk for chunk in all_parent_chunks 
                            if chunk.get("chunk_id") not in {p.get("chunk_id") for p in self.parent_chunks_data}]
        
        if new_child_chunks:
            with self.knowledge_path.open("a", encoding="utf-8") as f:
                for chunk in new_child_chunks:
                    f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        
        if new_parent_chunks:
            with self.parent_knowledge_path.open("a", encoding="utf-8") as f:
                for chunk in new_parent_chunks:
                    f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        
        self.chunks_data.extend(new_child_chunks)
        self.parent_chunks_data.extend(new_parent_chunks)
        self.new_chunks_data = new_child_chunks
        self.new_parent_chunks_data = new_parent_chunks
        
        print(
            "[*] Chunking and metadata extraction complete. "
            f"Generated {len(new_child_chunks)} new child chunks and {len(new_parent_chunks)} new parent chunks "
            f"(total {len(self.chunks_data)} child chunks, {len(self.parent_chunks_data)} parent chunks)."
        )

    def _chunk_embed_text(self, chunk: Dict) -> str:
        """Return the text to embed for a chunk (context_prefix + text)."""
        return chunk.get("context_prefix", "") + chunk.get("text", "")

    def build_index(self, incremental: bool = True):
        """Encode all chunks with the embedding model and build or update
        the FAISS IndexFlatIP. When incremental is True and the existing
        index is compatible, only newly added chunks are embedded."""
        if not self.chunks_data:
            print("[!] No chunks available, skip FAISS index build.")
            return

        batch_size = self._embedding_batch_size()
        if incremental and self.faiss_path.exists() and not self.new_chunks_data:
            print("[*] No new chunks detected, keep existing FAISS index.")
            return

        if incremental and self.new_chunks_data and self._can_incrementally_update_index():
            print("[*] Incrementally updating FAISS index...")
            texts = [self._chunk_embed_text(c) for c in self.new_chunks_data]
            print(f"[*] Embedding {len(texts)} new child chunks with batch_size={batch_size} on {self.device}...")
            embeddings = self._encode_texts(texts, batch_size)
            index = faiss.read_index(str(self.faiss_path))
            start_index = index.ntotal
            index.add(embeddings)
            faiss.write_index(index, str(self.faiss_path))
            self._write_id_map(self.new_chunks_data, start_index=start_index, mode="a")
            self._write_index_meta(index.ntotal, batch_size, update_mode="incremental")
            print(f"[*] FAISS index incrementally updated: {self.faiss_path} (+{len(self.new_chunks_data)} vectors)")
        else:
            print("[*] Building FAISS index from scratch...")
            texts = [self._chunk_embed_text(c) for c in self.chunks_data]
            print(f"[*] Embedding {len(texts)} child chunks with batch_size={batch_size} on {self.device}...")
            embeddings = self._encode_texts(texts, batch_size)
            index = faiss.IndexFlatIP(embeddings.shape[1])
            index.add(embeddings)

            faiss.write_index(index, str(self.faiss_path))
            self._write_id_map(self.chunks_data, start_index=0, mode="w")
            self._write_index_meta(len(self.chunks_data), batch_size, update_mode="rebuild")
            print(f"[*] FAISS index built: {self.faiss_path}")

        if self.device.startswith("cuda"):
            torch.cuda.empty_cache()
    
    def _write_id_map(self, chunks: List[Dict], start_index: int = 0, mode: str = "w"):
        with self.id_map_path.open(mode, encoding="utf-8") as f:
            for offset, chunk in enumerate(chunks):
                f.write(
                    json.dumps(
                        {"chunk_id": chunk["chunk_id"], "index_id": start_index + offset},
                        ensure_ascii=False,
                    ) + "\n"
                )
    
    def _write_index_meta(self, num_vectors: int, batch_size: int, update_mode: str):
        with self.index_meta_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "index_type": "IndexFlatIP",
                    "embedding_model": self.embedding_cfg["model"],
                    "normalized_embeddings": bool(self.embedding_cfg.get("normalize_embeddings", True)),
                    "embedding_batch_size": batch_size,
                    "device": self.device,
                    "chunking": {
                        "child": self.cfg["chunking"]["child"],
                        "parent": self.cfg["chunking"]["parent"],
                    },
                    "num_vectors": num_vectors,
                    "update_mode": update_mode,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    
    def _can_incrementally_update_index(self) -> bool:
        if not self.faiss_path.exists() or not self.id_map_path.exists():
            return False
        meta = self._load_index_meta()
        if meta.get("index_type") != "IndexFlatIP":
            return False
        if meta.get("embedding_model") != self.embedding_cfg["model"]:
            return False
        existing_id_map = self._load_existing_id_map()
        if not existing_id_map:
            return False
        existing_count = len(existing_id_map)
        existing_chunk_count = len(self.chunks_data) - len(self.new_chunks_data)
        if existing_count != existing_chunk_count:
            return False
        if meta.get("num_vectors") not in (None, existing_count):
            return False
        return True

    def _encode_texts(self, texts: List[str], batch_size: int) -> np.ndarray:
        embeddings = self.emb_model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=bool(self.embedding_cfg.get("show_progress_bar", True)),
            normalize_embeddings=bool(self.embedding_cfg.get("normalize_embeddings", True)),
            convert_to_numpy=True,
        )
        return np.asarray(embeddings, dtype="float32")
    
    def retrieve_with_parent(self, query: str, top_k: int = 5) -> List[Dict]:
        """Quick retrieval that returns both child and parent chunk data.
        Useful for testing the index without going through PriorRetriever."""
        if not self.faiss_path.exists():
            raise FileNotFoundError("FAISS index not found. Run build_index first.")
        
        query_embedding = self._encode_texts([query], self._embedding_batch_size())
        
        index = faiss.read_index(str(self.faiss_path))
        scores, indices = index.search(query_embedding, top_k)
        
        id_map = self._load_existing_id_map()
        results = []
        parent_chunks_cache = {}
        
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            chunk_id = id_map.get(int(idx))
            if not chunk_id:
                continue
            
            child_chunk = next((c for c in self.chunks_data if c.get("chunk_id") == chunk_id), None)
            if not child_chunk:
                continue
            
            parent_chunk_id = child_chunk.get("parent_chunk_id")
            if parent_chunk_id:
                if parent_chunk_id not in parent_chunks_cache:
                    parent_chunk = next((p for p in self.parent_chunks_data 
                                       if p.get("chunk_id") == parent_chunk_id), None)
                    parent_chunks_cache[parent_chunk_id] = parent_chunk
                parent_chunk = parent_chunks_cache[parent_chunk_id]
            else:
                parent_chunk = None
            
            results.append({
                "score": float(score),
                "child_chunk": child_chunk,
                "parent_chunk": parent_chunk,
            })
        
        return results


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest files under LANDAU/prior/source, split them into child/parent chunks, "
            "and build/update the FAISS dense index for prior retrieval (Parent-Child RAG)."
        )
    )
    parser.add_argument(
        "--target",
        default="",
        help="Optional single source file to ingest. Default: ingest all supported files under source/.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing chunks/index metadata before ingesting and rebuild from scratch.",
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="Rebuild the FAISS index from all chunks instead of incrementally updating it.",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Only ingest/chunk files and skip FAISS index building.",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="Print supported source files and exit.",
    )
    return parser


def _resolve_target_path(raw_target: str) -> str:
    target = (raw_target or "").strip()
    if not target:
        return ""
    path = Path(target).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return str(path.resolve())


def _print_runtime_summary(store: PriorStore):
    print("[*] Prior store configuration (Parent-Child RAG)")
    print(f"    source_dir: {store.dirs['source']}")
    print(f"    out_dir: {store.dirs['out']}")
    print(f"    knowledge_path (child chunks): {store.knowledge_path}")
    print(f"    parent_knowledge_path: {store.parent_knowledge_path}")
    print(f"    index_path: {store.faiss_path}")
    print(f"    embedding_model: {store.embedding_cfg['model']}")
    print(f"    device: {store.device}")
    print("    child chunking: "
          f"max_chars={store.cfg['chunking']['child']['max_chars']} "
          f"overlap_chars={store.cfg['chunking']['child']['overlap_chars']} "
          f"min_chars={store.cfg['chunking']['child']['min_chars']}")
    print("    parent chunking: "
          f"max_chars={store.cfg['chunking']['parent']['max_chars']} "
          f"overlap_chars={store.cfg['chunking']['parent']['overlap_chars']} "
          f"min_chars={store.cfg['chunking']['parent']['min_chars']}")


def main(argv: List[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    store = PriorStore()
    _print_runtime_summary(store)

    if args.list_sources:
        source_files = sorted(
            path for path in store.dirs["source"].iterdir()
            if path.is_file() and path.suffix.lower() in store.cfg["supported_exts"]
        )
        if not source_files:
            print("[!] No supported source files found.")
            return 1
        print("[*] Supported source files:")
        for path in source_files:
            print(f"    - {path}")
        return 0

    target_path = _resolve_target_path(args.target)
    store.process(target_path=target_path, reset_existing=bool(args.reset))

    if args.no_index:
        print("[*] Skipped FAISS index build (--no-index).")
        return 0

    store.build_index(incremental=not args.rebuild_index)
    print("[*] Prior store pipeline finished (Parent-Child RAG mode).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


    