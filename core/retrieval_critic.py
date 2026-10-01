"""Scores search results for relevance to a physics query.

Called right after web_search / library_search returns, filters out
results below a relevance threshold before they reach the Supervisor.
Fails open: any error returns the original results unchanged.
"""

import json

from utils.llm_client import call_model_without_tools


SYSTEM_PROMPT = """You are a retrieval quality judge for physics research.
Given a QUERY and a list of SEARCH RESULTS, score each result from 0.0 to 1.0.

Scoring rubric:
- 1.0 = directly answers the query, authoritative source
        (Wikipedia, arXiv, NIST, Physics Stack Exchange, textbook sites)
- 0.6 = related to physics, partially useful
- 0.3 = wrong subfield or superficial
- 0.0 = completely irrelevant (different domain: biology, marketing, etc.)

Return ONLY a JSON object with this exact schema:
{"scores": [<float>, ...], "reasons": [<short string>, ...]}

The two lists MUST have the same length as the input results.
Do not include any prose outside the JSON."""


class RetrievalCritic:
    def __init__(self, config_path: str = "config.yaml", threshold: float = 0.5):
        self.config_path = config_path
        self.threshold = float(threshold)

    def filter(self, query: str, results: list) -> list:
        """Return the subset of results whose score >= threshold.
        On any failure, return results unchanged (fail-open)."""
        if not results:
            return results

        listing = "\n\n".join(
            f"[{i}] title: {r.get('title', '')}\n"
            f"    snippet: {(r.get('snippet') or r.get('text') or '')[:200]}"
            for i, r in enumerate(results)
        )
        user_prompt = (
            f"QUERY: {query}\n\n"
            f"SEARCH RESULTS:\n{listing}\n\n"
            "Score each result. Return JSON only."
        )

        try:
            raw = call_model_without_tools(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                role="retrieval_critic",
                config_path=self.config_path,
            )
        except Exception as e:
            print(f"[RetrievalCritic] LLM call failed: {e}; fail-open")
            return results

        scores = self._parse_scores(raw, expected_len=len(results))
        if scores is None:
            print("[RetrievalCritic] could not parse scores; fail-open")
            return results

        kept = []
        for i, (r, s) in enumerate(zip(results, scores)):
            mark = "OK" if s >= self.threshold else "--"
            print(f"[RetrievalCritic] {mark} [{i}] score={s:.2f}  "
                  f"{r.get('title', '')[:50]}")
            if s >= self.threshold:
                kept.append(r)

        if not kept:
            print(f"[RetrievalCritic] all {len(results)} below threshold "
                  f"{self.threshold}; fail-open, return original")
            return results

        return kept

    def _parse_scores(self, raw, expected_len):
        if not raw:
            return None
        text = str(raw).strip()
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 2:
                text = parts[1]
                if text.startswith("json"):
                    text = text[4:]
        l, r = text.find("{"), text.rfind("}")
        if l == -1 or r == -1:
            return None
        try:
            obj = json.loads(text[l:r + 1])
        except Exception:
            return None
        scores = obj.get("scores")
        if not isinstance(scores, list) or len(scores) != expected_len:
            return None
        try:
            return [float(s) for s in scores]
        except Exception:
            return None