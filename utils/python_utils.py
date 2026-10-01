import os
import subprocess
import tempfile

def run_python_code(code: str, cwd: str | None = None) -> str:
    """Execute a Python script in a subprocess and return its combined
    stdout+stderr. Used as the Python_code_interpreter tool by Theoretician.
    Timeout is 30 minutes to allow long numerical computations."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        f.flush()
        tmp_path = f.name

    try:
        completed = subprocess.run(
            [subprocess.sys.executable, tmp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=450,
            cwd=cwd,
        )
        return completed.stdout
    finally:
        os.unlink(tmp_path)