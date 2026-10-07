import base64, hashlib, json, os, shutil, sys, tempfile, zlib
from pathlib import Path
from datetime import datetime

DATA = """
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
"""

def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
    if not (root / "run.py").is_file():
        raise RuntimeError("Save this script in the PhysMaster folder containing run.py.")
    items = json.loads(zlib.decompress(base64.b64decode(DATA)))
    pending = []
    for item in items:
        path = root / item["path"]
        text = path.read_text(encoding="utf-8-sig")
        current = digest(text)
        if current == item["after"]:
            print("Already patched:", item["path"])
            continue
        if current != item["before"]:
            raise RuntimeError("Version mismatch; no files changed: " + item["path"])
        for start, end, replacement in reversed(item["changes"]):
            text = text[:start] + replacement + text[end:]
        if digest(text) != item["after"]:
            raise RuntimeError("Patch verification failed: " + item["path"])
        if path.suffix == ".py":
            compile(text, str(path), "exec")
        pending.append((path, text, item["path"]))
    if not pending:
        print("Both fixes are already installed.")
        return
    backup = root / ("patch_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    for path, text, name in pending:
        target = backup / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    changed = []
    try:
        for path, text, name in pending:
            fd, tmp = tempfile.mkstemp(prefix="patch_", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                os.replace(tmp, path)
                changed.append((path, name))
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except Exception:
        for path, name in reversed(changed):
            shutil.copy2(backup / name, path)
        raise
    print("Installed: round history + dispatch fixes.")
    print("Backup:", backup)
    print("Restart the server, then press Ctrl+F5 in your browser.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", exc)
        sys.exit(1)
