import json
from pathlib import Path

class Codata:
    def __init__(self, path: str | None = None):
        self.path = Path(path) if path else Path(__file__).parent / "data" / "codata.json"
        self.table = {}
        if self.path.exists():
            self.table = json.loads(self.path.read_text(encoding="utf-8"))

    def lookup(self, name: str) -> dict | None:
        key = name.lower().replace(" ", "_").replace("-", "_")
        # 精确匹配
        if key in self.table:
            return {"name": key, **self.table[key]}
        # 模糊匹配
        for k, v in self.table.items():
            if key in k or k in key:
                return {"name": k, **v}
        return None