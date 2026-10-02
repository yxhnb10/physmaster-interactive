import os
import subprocess
import tempfile
import re

def run_python_code(code: str, cwd: str | None = None) -> str:
    """Execute a Python script in a subprocess and return its combined
    stdout+stderr. Used as the Python_code_interpreter tool by Theoretician.
    Timeout is 450 seconds. An explicit exit receipt accompanies the output."""
    with tempfile.NamedTemporaryFile(mode="w", encoding='utf-8', suffix=".py", delete=False) as f:
        f.write(code)
        f.flush()
        tmp_path = f.name

    try:
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        if cwd:
            # Scripts are executed from a temporary file. Their sys.path[0]
            # is otherwise the temp directory, not this node's input folder.
            paths = [os.path.abspath(cwd)]
            if env.get('PYTHONPATH'): paths.append(env['PYTHONPATH'])
            env['PYTHONPATH'] = os.pathsep.join(paths)
        completed = subprocess.run(
            [subprocess.sys.executable, '-X', 'utf8', tmp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8', errors='replace',
            timeout=450,
            cwd=cwd,
            env=env,
        )
        return f'[PhysMaster execution: exit_code={completed.returncode}]\n' + completed.stdout
    except subprocess.TimeoutExpired as exc:
        output=exc.stdout or b''
        if isinstance(output,bytes):output=output.decode('utf-8',errors='replace')
        return '[PhysMaster execution: exit_code=timeout]\n' + output
    except OSError as exc:
        return '[PhysMaster execution: exit_code=error]\n' + str(exc)
    finally:
        os.unlink(tmp_path)


def execution_receipt(output):
    match=re.match(r'^\[PhysMaster execution: exit_code=(-?\d+|timeout|error)\]\n',str(output))
    if not match:return {'exit_code':None}
    value=match[1]
    return {'exit_code':int(value) if re.fullmatch(r'-?\d+',value) else value}
