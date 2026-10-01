from pathlib import Path
import json
import re
import yaml
from utils.llm_client import call_model_without_tools


def load_prompt(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")

SYSTEM_PROMPT = load_prompt("prompts/clarifier_system_prompt.txt")
USER_PROMPT = load_prompt("prompts/clarifier_prompt.txt")

class Clarifier:
    """Stage 1 of the pipeline. Turns raw physics problem text into a
    structured JSON contract via LLM, optionally augmented with a
    matching LANDAU workflow reference.

    The overall flow is:
      raw_input text
        -> task_spec(): format prompt with contract schema, search for a
           relevant workflow YAML, call LLM to produce JSON
        -> _parse_result(): extract the first {...} JSON block from LLM output
        -> _normalize_contract(): keep only keys present in the schema
        -> return the cleaned contract dict
    """

    def __init__(self, config, workflow_enabled: bool = True, config_path:str = 'config.yaml'):
        self.max_keys = config.get("max_key_concpets",5)
        self.workflow_dir = self._resolve_workflow_dir(config)
        self.workflow_enabled = bool(workflow_enabled)
        self.config_path = config_path
        # Stopwords are stripped before workflow matching to reduce noise
        self._stopwords = {
            "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
            "in", "is", "it", "of", "on", "or", "that", "the", "this", "to",
            "with", "without", "via", "use", "using", "based",
            "problem", "task", "workflow", "methodology", "method",
        }

    def _resolve_workflow_dir(self,config):
        """Try to resolve a workflow directory from config. Returns an
        absolute Path if workflow_dir or workflow_path is configured,
        otherwise None (fallback will use the default LANDAU path)."""
        # parents[2] goes up from core/ to project root
        project_root = Path(__file__).resolve().parents[2]
        configured = (
            config.get("workflow_dir")
            or config.get("workflow_path")
        )
        if configured:
            p = Path(str(configured))
            # support both absolute and relative paths in config
            return p if p.is_absolute() else (project_root / p).resolve()
        
    def _resolve_default_workflow_dir(self) -> Path:
        project_root = Path(__file__).resolve().parents[1]
        candidates = [
            project_root / "LANDAU" / "workflow",
            project_root / "LANDAU" / "methodology" / "workflow",
        ]
        for cand in candidates:
            if cand.exists():
                return cand.resolve()
        return candidates[0].resolve()

    def _tokenize_query(self, query: str) -> list[str]:
        """Split query into lowercase tokens: ascii words, digits, or CJK chars."""
        q = (query or "").strip().lower()
        if not q:
            return []
        return re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", q)

    def _remove_stopwords(self, tokens: list[str]) -> list[str]:
        return [tok for tok in tokens if tok not in self._stopwords]

    def _extract_workflow_goal(self, data: dict) -> str:
        """Pull the Goal string from a workflow YAML. Supports both
        capitalized and lowercase key names."""
        if not isinstance(data, dict):
            return ""
        wf = data.get("Workflow") or data.get("workflow") or {}
        if not isinstance(wf, dict):
            return ""
        goal = wf.get("Goal") or wf.get("goal") or ""
        return str(goal).strip()
    
    def _parse_workflow_file(self):
        """If workflow_dir points directly to a YAML file, parse it.
        Returns {"path", "goal", "raw"} or None on failure."""
        if self.workflow_dir.exists() and self.workflow_dir.is_file():
            try:
                raw = self.workflow_dir.read_text(encoding="utf-8")
                data = yaml.safe_load(raw)
                goal = self._extract_workflow_goal(data)
                return {"path": str(self.workflow_dir), "goal": goal, "raw": raw}
            except Exception:
                print(f"[Clarifier] Workflow loading failed, loading path: {self.workflow_dir}")
        else:
            print(f"[Clarifier] Workflow loading failed, loading path: {self.workflow_dir}")
            return None

    def _select_workflow_by_goal(self, user_query: str) -> dict | None:
        """Pick the best-matching workflow YAML by token overlap between
        the user query and each workflow's Goal field.

        Matching logic:
          1. Tokenize query and each workflow Goal into lowercase tokens.
          2. Remove stopwords from both.
          3. Compute set intersection and a goal_ratio (overlap / goal_tokens).
          4. Require at least 1 overlap token and a minimum goal_ratio
             threshold (25% for long goals, 34% for short ones).
          5. Score = overlap_count*2 + goal_ratio, pick highest.
        Returns {"path", "goal", "raw"} dict or None.
        """
        print("[Clarifier] Start searching workflows")

        tokens = self._remove_stopwords(self._tokenize_query(user_query))
        if not tokens:
            print("[Clarifier] Workflow search finished (no tokens)")
            return None

        workflow_file = self._parse_workflow_file()
        if workflow_file is not None:
            print(f"[Clarifier] Selected workflow reference {workflow_file['path']}")
            return workflow_file
        
        default_dir = self._resolve_default_workflow_dir()
        if not default_dir.exists():
            print(f"[Clarifier] Workflow search skipped (dir not found): {default_dir}")
            return None
        tokens = self._remove_stopwords(self._tokenize_query(user_query))
        if not tokens:
            print("[Clarifier] Workflow search finished (no tokens)")
            return None
        best = None
        best_score = 0

        for path in sorted(default_dir.glob("*.y*ml")):
            try:
                raw = path.read_text(encoding="utf-8")
                data = yaml.safe_load(raw)
            except Exception:
                continue
            goal = self._extract_workflow_goal(data)
            if not goal:
                continue
            goal_tokens = self._remove_stopwords(self._tokenize_query(goal))
            if not goal_tokens:
                continue
            overlap = set(tokens) & set(goal_tokens)
            overlap_count = len(overlap)
            if overlap_count == 0:
                continue

            min_overlap = 1
            min_goal_ratio = 0.25 if len(goal_tokens) >= 5 else 0.34
            goal_ratio = overlap_count / max(len(goal_tokens), 1)
            if overlap_count < min_overlap:
                continue
            if goal_ratio < min_goal_ratio:
                continue

            # Score formula: heavily weight overlap count, add goal_ratio as tiebreaker
            score = overlap_count * 2 + goal_ratio
            if score > best_score:
                best_score = score
                best = {"path": str(path), "goal": goal, "raw": raw}

        if best_score <= 0:
            print("[Clarifier] Workflow search finished (no match)")
            return None
        print(f"[Clarifier] Selected workflow reference {best['path']}")
        print("[Clarifier] Workflow search finished")
        return best

    def task_spec(self, user_query):
        """Build the full LLM prompt and call the model.

        Steps:
          1. Load the contract JSON schema from utils/contract_template.json.
          2. Format the user prompt with the query text, schema, and max_keys.
          3. If workflow is enabled, search for the best-matching workflow
             YAML and append it to the prompt as additional context.
          4. Call the LLM (no tools) and return (response_str, schema_dict).
        """
        schema_path = Path(__file__).resolve().parent.parent / "utils/contract_template.json"
        if not schema_path.exists():
            raise FileNotFoundError(f"Contract template not found: {schema_path}")
        with schema_path.open("r", encoding="utf-8") as f:
            schema = json.load(f)


        system_prompt = SYSTEM_PROMPT

        user_prompt = USER_PROMPT.format(
            user_query=user_query,
            schema_json=json.dumps(schema, indent=2),
            max_keys=self.max_keys,
        )
        if self.workflow_enabled:
            workflow_hit = self._select_workflow_by_goal(user_query)
            if workflow_hit:
                user_prompt += (
                    "\n\nRelevant workflow:\n"
                    f"[file] {workflow_hit['path']}\n"
                    f"[goal] {workflow_hit['goal']}\n"
                    f"{workflow_hit['raw']}"
                )
        
        response = call_model_without_tools(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            role="clarifier",
            config_path=self.config_path
        )

        return response, schema

    def _normalize_contract(self, contract, schema):
        """
        Keep only keys defined by schema.properties.
        This prevents schema/template metadata keys from leaking into output.
        """
        if not isinstance(contract, dict):
            return contract
        if not isinstance(schema, dict):
            return contract
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return contract
        allowed = set(properties.keys())
        return {k: v for k, v in contract.items() if k in allowed}

    def _parse_result(self, result):
        """Extract the first JSON object from the LLM response string.
        The LLM often wraps the JSON in markdown or extra text, so we
        find the outermost { ... } and parse it. Returns a dict on
        success, or a dict with 'error' key on failure."""
        try:
            # Try to extract JSON from the response
            if "{" in result and "}" in result:
                start_idx = result.find("{")
                end_idx = result.rfind("}") + 1
                json_str = result[start_idx:end_idx]
                return json.loads(json_str)
            else:
                return {"raw_output": result, "error": "No structured output found"}
        except Exception as e:
            return {
                "raw_output": result, 
                "error": f"Failed to parse response: {str(e)}"
            }

    def run(self, raw_input):
        """Main entry point.

        Takes raw physics problem text as a string, sends it through
        task_spec -> _parse_result -> _normalize_contract, and returns
        a clean structured contract dict whose keys match the schema.
        """
        result, schema = self.task_spec(raw_input)
        contract = self._parse_result(result)
        contract = self._normalize_contract(contract, schema)
        return contract
