"""Offline privacy regressions against real disposable Git repositories."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1]


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='privacy-regression-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Regression')
        self.git('config', 'user.email', 'regression@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'core.hooksPath', str(self.root / 'disabled-hooks'))
        (self.root / 'tools').mkdir()
        shutil.copy2(SOURCE / 'tools/privacy.py', self.root / 'tools/privacy.py')
        (self.root / '.gitignore').write_text('.local/\ntools/\n.githooks/\n', encoding='utf-8')
        (self.root / 'privacy-examples.json').write_text('{}', encoding='utf-8')
        self.git('add', '.gitignore', 'privacy-examples.json')
        self.git('commit', '-qm', 'baseline')
        self.baseline = self.git('rev-parse', 'HEAD').strip()
        self.branch = self.git('symbolic-ref', '--short', 'HEAD').strip()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args],
                                       env=self.env, stderr=subprocess.PIPE).decode('utf-8')

    def add(self, name, value='synthetic business note\n'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8')
        self.git('add', '-f', name)
        self.git('commit', '-qm', 'synthetic fixture')

    def remove(self, name):
        self.git('rm', '-q', name)
        self.git('commit', '-qm', 'remove fixture')

    def scan(self, stdin=None):
        command = [sys.executable, '-u', 'tools/privacy.py', '--history']
        if stdin is not None:
            command.append('--pre-push')
        proc = subprocess.run(command, cwd=self.root, env=self.env, input=stdin,
                              capture_output=True, text=True, encoding='utf-8')
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        return proc.returncode, json.loads(proc.stdout)

    def assert_path_blocked(self, name):
        code, report = self.scan()
        self.assertEqual(code, 1, report)
        self.assertTrue(report['scan_complete'], report)
        self.assertTrue(any(row.get('file') == name and row.get('category') == 'private-or-binary-artifact'
                            for row in report['findings']), report)

    def load_scanner(self):
        spec = importlib.util.spec_from_file_location('privacy_under_test', self.root / 'tools/privacy.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def install_hook(self):
        (self.root / '.githooks').mkdir()
        shutil.copy2(SOURCE / '.githooks/pre-push', self.root / '.githooks/pre-push')
        (self.root / '.githooks/pre-push').chmod(0o755)
        self.git('config', 'core.hooksPath', '.githooks')
        self.git('config', 'meta.privacyPython', sys.executable)

    def test_clean_history_passes(self):
        self.add('allowed.txt', 'ordinary public text\n')
        code, report = self.scan()
        self.assertEqual(code, 0, report)
        self.assertTrue(report['passed'] and report['scan_complete'])
        self.assertEqual(report['checked_commits'], 2)

    def test_deleted_private_path_remains_blocked(self):
        name = '.local/private-notes.txt'
        self.add(name)
        self.assert_path_blocked(name)
        self.remove(name)
        self.assert_path_blocked(name)
        self.assertEqual(self.git('show', 'HEAD~1:' + name).strip(), 'synthetic business note')

    def test_all_forbidden_suffixes_after_deletion(self):
        for suffix in ('.dat', '.mmdb', '.mrs', '.zip', '.exe', '.dll', '.key', '.pem'):
            with self.subTest(suffix=suffix):
                name = 'fixture' + suffix.upper()
                self.add(name)
                self.remove(name)
                self.assert_path_blocked(name)

    def test_same_blob_at_allowed_and_forbidden_paths(self):
        self.add('allowed.txt')
        self.add('.local/alias.txt')
        self.assertEqual(self.git('rev-parse', 'HEAD:allowed.txt'),
                         self.git('rev-parse', 'HEAD:.local/alias.txt'))
        self.remove('.local/alias.txt')
        self.assert_path_blocked('.local/alias.txt')

    def test_rename_to_private_path_then_delete(self):
        self.add('allowed.txt')
        (self.root / '.local').mkdir()
        self.git('mv', 'allowed.txt', '.local/renamed.txt')
        self.git('commit', '-qm', 'rename synthetic fixture')
        self.remove('.local/renamed.txt')
        self.assert_path_blocked('.local/renamed.txt')

    def test_other_branch_and_tag_are_scanned(self):
        self.git('checkout', '-qb', 'fixture-side')
        self.add('.local/side.txt')
        self.remove('.local/side.txt')
        self.git('tag', '-a', 'fixture-tag', '-m', 'synthetic tag')
        self.git('checkout', '-q', self.branch)
        self.assert_path_blocked('.local/side.txt')
        self.git('branch', '-D', 'fixture-side')
        self.assert_path_blocked('.local/side.txt')

    def test_pending_unreferenced_commit_is_scanned(self):
        self.add('.local/pending.txt')
        self.remove('.local/pending.txt')
        pending = self.git('rev-parse', 'HEAD').strip()
        self.git('reset', '--hard', self.baseline)
        self.assertEqual(self.scan()[0], 0)
        payload = f'{pending} {pending} refs/heads/pending {"0" * 40}\n'
        code, report = self.scan(payload)
        self.assertEqual(code, 1, report)
        self.assertTrue(any(row.get('file') == '.local/pending.txt' for row in report['findings']))

    def test_allowed_path_with_sensitive_signature_still_fails(self):
        self.add('allowed.txt', '-----BEGIN ' + 'PRIVATE KEY-----\n')
        code, report = self.scan()
        self.assertEqual(code, 1, report)
        self.assertTrue(any(f.get('category') == 'private-key'
                            for row in report['findings'] for f in row.get('findings', [])))

    def test_path_approval_cannot_waive_forbidden_path(self):
        self.add('.local/approved.txt')
        scanner = self.load_scanner()
        digest = scanner.hashlib.sha256((self.root / '.local/approved.txt').read_bytes()).hexdigest()
        (self.root / 'privacy-examples.json').write_text(json.dumps({digest: 'synthetic'}), encoding='utf-8')
        self.remove('.local/approved.txt')
        self.assert_path_blocked('.local/approved.txt')

    def test_unicode_and_space_paths_are_not_split(self):
        name = '.local/合成 note with spaces.txt'
        self.add(name)
        self.remove(name)
        self.assert_path_blocked(name)

    def test_case_alias_is_blocked_on_windows_too(self):
        self.add('.LOCAL/alias.txt')
        self.remove('.LOCAL/alias.txt')
        self.assert_path_blocked('.LOCAL/alias.txt')

    def test_index_bytes_cannot_be_hidden_by_working_bytes(self):
        self.add('allowed.txt')
        (self.root / 'allowed.txt').write_text('-----BEGIN ' + 'PRIVATE KEY-----\n', encoding='utf-8')
        self.git('add', 'allowed.txt')
        (self.root / 'allowed.txt').write_text('ordinary working bytes\n', encoding='utf-8')
        self.assertEqual(self.scan()[0], 1)

    def test_shallow_history_invalidates_previous_pass(self):
        self.assertEqual(self.scan()[0], 0)
        (self.root / '.git/shallow').write_text(self.baseline + '\n', encoding='ascii')
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertFalse(report['passed'] or report['scan_complete'])
        saved = json.loads((self.root / '.local/privacy-review.json').read_text(encoding='utf-8'))
        self.assertEqual(saved, report)

    def test_missing_historical_object_cannot_pass(self):
        self.add('old.txt')
        oid = self.git('rev-parse', 'HEAD:old.txt').strip()
        self.remove('old.txt')
        loose = self.root / '.git/objects' / oid[:2] / oid[2:]
        loose.chmod(0o600)  # Git makes loose objects read-only on Windows.
        loose.unlink()
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertFalse(report['scan_complete'])

    def test_refs_changing_during_scan_cannot_pass(self):
        scanner = self.load_scanner()
        original = scanner.git
        changed = False

        def read(*args, **kwargs):
            nonlocal changed
            if args[0] == 'ls-tree' and not changed:
                changed = True
                self.git('tag', 'concurrent-fixture')
            return original(*args, **kwargs)

        with mock.patch.object(scanner, 'git', read), mock.patch.dict(os.environ, self.env, clear=True):
            with self.assertRaisesRegex(scanner.ScanError, 'refs-changed'):
                scanner.review(history=True)

    def test_malformed_push_input_and_deletion(self):
        self.assertEqual(self.scan('bad record\n')[0], 1)
        self.assertEqual(self.scan(f'(delete) {"0" * 40} refs/heads/old {self.baseline}\n')[0], 0)

    def test_partial_clone_requires_complete_review(self):
        self.git('config', 'remote.fixture.promisor', 'true')
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertFalse(report['scan_complete'])
        self.assertIn('partial-history', report['findings'][0]['category'])

    def test_non_commit_tag_is_not_silently_ignored(self):
        self.git('tag', 'blob-fixture', 'HEAD:.gitignore')
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertFalse(report['scan_complete'])

    def test_private_identifier_in_filename_is_redacted(self):
        identifier = 'synthetic-private-identifier'
        self.add('.local/' + identifier + '.txt')
        (self.root / '.local/privacy-deny.json').write_text(json.dumps([identifier]), encoding='utf-8')
        code, report = self.scan()
        self.assertEqual(code, 1)
        self.assertNotIn(identifier, json.dumps(report))
        self.assertTrue(any(row.get('file') == '[redacted]' for row in report['findings']))

    def test_real_pre_push_hook_allows_clean_and_blocks_pending_history(self):
        self.install_hook()
        remote = self.root / '.local/remote.git'
        remote.parent.mkdir(exist_ok=True)
        subprocess.run(['git', 'init', '--bare', '-q', str(remote)], env=self.env, check=True)
        clean = subprocess.run(['git', '-C', str(self.root), 'push', str(remote), 'HEAD:refs/heads/control'],
                               env=self.env, capture_output=True)
        self.assertEqual(clean.returncode, 0, clean.stderr.decode(errors='replace'))
        self.add('.local/pending.txt')
        self.remove('.local/pending.txt')
        pending = self.git('rev-parse', 'HEAD').strip()
        self.git('reset', '--hard', self.baseline)
        denied = subprocess.run(['git', '-C', str(self.root), 'push', str(remote), pending + ':refs/heads/rejected'],
                                env=self.env, capture_output=True)
        self.assertNotEqual(denied.returncode, 0)
        refs = subprocess.check_output(['git', '-C', str(remote), 'for-each-ref', '--format=%(refname)'], env=self.env)
        self.assertIn(b'refs/heads/control', refs)
        self.assertNotIn(b'refs/heads/rejected', refs)


if __name__ == '__main__':
    unittest.main(verbosity=2)
