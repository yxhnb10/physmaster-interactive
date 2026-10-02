"""Portable checks for the UTF-8 settings used by the Windows tool runner."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from utils.python_utils import run_python_code,execution_receipt


class SubprocessEncodingTests(unittest.TestCase):
    def test_nested_python_uses_utf8_even_with_legacy_parent_settings(self):
        code="""import os,sys,subprocess
assert sys.flags.utf8_mode == 1
assert os.environ['PYTHONUTF8'] == '1'
child = subprocess.run([sys.executable, '-c', "print('中文 Ω ± 𝛼')"],
                       capture_output=True, text=True, check=True)
assert child.stdout.strip() == '中文 Ω ± 𝛼'
print(child.stdout, end='')
"""
        with patch.dict(os.environ,{'PYTHONUTF8':'0','PYTHONIOENCODING':'gbk'}):
            result=run_python_code(code)
        self.assertEqual(execution_receipt(result)['exit_code'],0,result)
        self.assertIn('中文 Ω ± 𝛼',result)

    def test_unexpected_raw_byte_does_not_crash_outer_reader(self):
        result=run_python_code("import sys;sys.stdout.buffer.write(b'prefix\\xab suffix')")
        self.assertEqual(execution_receipt(result)['exit_code'],0,result)
        self.assertIn('prefix\ufffd suffix',result)
        self.assertNotIn('Exception in thread',result)

    def test_nested_nonzero_exit_and_unicode_directory_are_preserved(self):
        code="""import subprocess,sys
child=subprocess.run([sys.executable,'-c',"print('嵌套错误');raise SystemExit(3)"],
                     capture_output=True,text=True)
print(child.stdout,end='')
raise SystemExit(child.returncode)
"""
        with tempfile.TemporaryDirectory(prefix='PhysMaster_中文_') as tmp:
            result=run_python_code(code,cwd=tmp)
        self.assertEqual(execution_receipt(result)['exit_code'],3,result)
        self.assertIn('嵌套错误',result)


if __name__=='__main__':unittest.main()
