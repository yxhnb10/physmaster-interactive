import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from tools.upgrade_release import apply, replace


class UpgradeTests(unittest.TestCase):
    def fixture(self, tmp):
        root=Path(tmp);project=root/'project';bundle=root/'bundle';project.mkdir();(bundle/'payload/core').mkdir(parents=True)
        (project/'core').mkdir();(project/'run.py').write_text('# existing');(project/'web_server.py').write_text('# existing')
        (project/'core/main.py').write_text('old');(project/'core/obsolete.py').write_text('old adapter')
        (project/'config.yaml').write_text('private configuration');(project/'outputs').mkdir();(project/'outputs/history.txt').write_text('old research')
        (bundle/'payload/core/main.py').write_text('new');(bundle/'payload/core/new.py').write_text('new entry')
        rows=[{'path':p.relative_to(bundle/'payload').as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted((bundle/'payload').rglob('*.py'))]
        (bundle/'upgrade-manifest.json').write_text(json.dumps({'version':'test','files':rows,'removed':['core/obsolete.py']}))
        return project,bundle

    def test_replaces_removes_and_backs_up_without_touching_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            project,bundle=self.fixture(tmp);result=apply(bundle,project)
            self.assertEqual((project/'core/main.py').read_text(),'new')
            self.assertFalse((project/'core/obsolete.py').exists())
            self.assertEqual((project/'config.yaml').read_text(),'private configuration')
            self.assertEqual((project/'outputs/history.txt').read_text(),'old research')
            with zipfile.ZipFile(result['backup']) as z:
                self.assertEqual(z.read('core/main.py'),b'old')
                self.assertEqual(z.read('core/obsolete.py'),b'old adapter')
                self.assertIn('core/new.py',json.loads(z.read('RESTORE.json'))['new_files'])

    def test_hash_corruption_fails_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            project,bundle=self.fixture(tmp);(bundle/'payload/core/new.py').write_text('tampered')
            with self.assertRaises(ValueError):apply(bundle,project)
            self.assertEqual((project/'core/main.py').read_text(),'old')
            self.assertTrue((project/'core/obsolete.py').is_file())

    def test_partial_write_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            project,bundle=self.fixture(tmp)
            def fail_one(source,target):
                if target.name=='new.py':raise OSError('simulated write failure')
                return replace(source,target)
            with patch('tools.upgrade_release.replace',side_effect=fail_one):
                with self.assertRaises(OSError):apply(bundle,project)
            self.assertEqual((project/'core/main.py').read_text(),'old')
            self.assertTrue((project/'core/obsolete.py').exists())
            self.assertFalse((project/'core/new.py').exists())

    def test_protected_and_escaping_paths_fail_before_mutation(self):
        for name in ('config.yaml','outputs/history.txt','../outside.py','C:/outside.py'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                project,bundle=self.fixture(tmp)
                manifest=json.loads((bundle/'upgrade-manifest.json').read_text());manifest['removed']=[name]
                (bundle/'upgrade-manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):apply(bundle,project)
                self.assertEqual((project/'core/main.py').read_text(),'old')

    def test_symlink_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            project,bundle=self.fixture(tmp);target=project/'core/main.py';target.unlink();outside=Path(tmp)/'outside.py';outside.write_text('outside');target.symlink_to(outside)
            with self.assertRaises(ValueError):apply(bundle,project)
            self.assertEqual(outside.read_text(),'outside')


if __name__=='__main__':unittest.main()
