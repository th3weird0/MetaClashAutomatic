"""Offline recovery/evidence regressions. All binaries and success inputs are synthetic."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import snapshot_support as support

SOURCE = Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='recovery-regression-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.local = self.root / '.local'
        self.work = self.local / 'validation'
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
        for name in ('tools', 'overwrite', '.local/bin', '.local/validation/runtime-data', '.local/validation/module-data'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in ('recovery.py', 'run-checks.py', 'snapshot_support.py'):
            shutil.copy2(SOURCE / 'tools' / name, self.root / 'tools' / name)
        for name in support.TEMPLATES:
            (self.root / name).write_text('synthetic template\n', encoding='utf-8')
        (self.root / 'overwrite/fixture.yaml').write_text('rules: []\n', encoding='utf-8')
        self.core = self.local / 'bin/core'
        self.converter = self.local / 'bin/converter.exe'
        self.dll = self.local / 'bin/helper.dll'
        self.core.write_bytes(b'non-executable synthetic core before\n')
        self.converter.write_bytes(b'non-executable synthetic converter\n')
        self.dll.write_bytes(b'synthetic converter dependency\n')
        self.settings = {
            'core': str(self.core), 'core_wsl': '/synthetic/nonexecuted-core',
            'converter': str(self.converter), 'core_version': 'synthetic-core',
            'converter_version': 'synthetic-converter', 'core_sha256': support.sha(self.core),
            'converter_sha256': support.sha(self.converter),
        }
        self.write(self.local / 'settings.json', self.settings)
        self.write(self.work / 'runtime-data/synthetic-nodes.yaml', {
            'proxies': [{'name': 'synthetic', 'type': 'http', 'server': '127.0.0.1', 'port': 1}]})
        self.write(self.work / 'runtime-data/zero-nodes.yaml', {'proxies': []})
        (self.work / 'module-data/fixture.yaml').write_text('rules: []\n', encoding='utf-8')
        for name in support.REPORTS:
            self.write(self.work / name, {})
        self.write(self.work / 'native-behavior.safe.json', {'passed': True, 'core_stopped': True, 'routing': []})
        self.write(self.work / 'safety-tests.safe.json', {'passed': True, 'template_sha256': support.sha(self.root / support.TEMPLATES[0])})
        self.write(self.work / 'module-verification.safe.json', {'passed': True, 'legacy_prepend_rules': 0})
        self.write(self.work / 'module-runtime.safe.json', {'passed': True, 'core_stopped': True})
        self.recovery = self.load('recovery.py', 'recovery_fixture')
        self.runner = self.load('run-checks.py', 'runner_fixture')
        session = self.runner.start_validation(self.root, self.fake_wsl_digest)
        self.evidence = self.runner.finish_validation(self.root, session, self.fake_wsl_digest)
        self.marker = self.work / support.MARKER

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def load(self, filename, name):
        spec = importlib.util.spec_from_file_location(name, self.root / 'tools' / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fake_wsl_digest(self, settings):
        # Test seam only: no WSL process, core, converter or network is started.
        return support.sha(Path(settings['core']))

    def cli(self, *args):
        return subprocess.run([sys.executable, '-u', 'tools/recovery.py', *map(str, args)],
                              cwd=self.root, env=self.env, capture_output=True, text=True, encoding='utf-8')

    def save(self, keep=3):
        proc = self.cli('save', '--label', 'synthetic-regression', '--keep', keep)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        folder = Path(proc.stdout.strip())
        self.assertEqual(folder.parent, self.local / 'snapshots')
        return folder

    def rewrite_manifest(self, folder):
        manifest = json.loads((folder / 'snapshot.json').read_text(encoding='utf-8'))
        manifest['files'] = {name: support.sha(path) for name, path in support.files_under(folder, ignore_bytecode=False).items()
                             if name != 'snapshot.json'}
        self.write(folder / 'snapshot.json', manifest)

    def test_unchanged_save_verify_restore_and_standalone_tool(self):
        folder = self.save()
        checked = self.cli('verify', folder)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        report = json.loads(checked.stdout)
        self.assertTrue(report['verified'])
        self.assertFalse(report['legacy'])
        self.assertEqual(report['evidence_scope'], 'full-bundle-inputs')
        destination = self.local / 'restored'
        restored = self.cli('restore', folder, '--destination', destination)
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertFalse(json.loads(restored.stdout)['deployed'])
        normal_env = {key: value for key, value in self.env.items() if key != 'PYTHONDONTWRITEBYTECODE'}
        standalone = subprocess.run([sys.executable, str(folder / 'tools/recovery.py'), 'verify', str(folder)],
                                    env=normal_env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(standalone.returncode, 0, standalone.stdout + standalone.stderr)
        self.assertFalse((folder / 'tools/__pycache__').exists())

    def test_changed_core_is_rejected_and_old_bundle_preserved(self):
        old = self.save()
        self.core.write_bytes(b'non-executable synthetic changed core\n')
        result = self.cli('save', '--keep', 1)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(old.exists())
        self.assertEqual(len(list((self.local / 'snapshots').iterdir())), 1)

    def test_updating_settings_digest_cannot_waive_stale_evidence(self):
        self.core.write_bytes(b'synthetic replacement\n')
        self.settings['core_sha256'] = support.sha(self.core)
        self.write(self.local / 'settings.json', self.settings)
        self.assertEqual(self.cli('save').returncode, 1)

    def test_changed_converter_and_dependencies_are_rejected(self):
        for path in (self.converter, self.dll):
            with self.subTest(file=path.name):
                original = path.read_bytes()
                path.write_bytes(b'synthetic changed dependency\n')
                self.assertEqual(self.cli('save').returncode, 1)
                path.write_bytes(original)
        (self.converter.parent / 'extra.dll').write_bytes(b'synthetic extra dependency')
        self.assertEqual(self.cli('save').returncode, 1)

    def test_changed_added_and_removed_source_files_are_rejected(self):
        path = self.root / 'overwrite/fixture.yaml'
        original = path.read_bytes()
        path.write_bytes(b'rules: [synthetic]\n')
        self.assertEqual(self.cli('save').returncode, 1)
        path.write_bytes(original)
        path.unlink()
        self.assertEqual(self.cli('save').returncode, 1)
        path.write_bytes(original)
        (self.root / 'tools/new.py').write_text('# synthetic new source\n', encoding='utf-8')
        self.assertEqual(self.cli('save').returncode, 1)

    def test_changed_template_or_saved_test_artifact_is_rejected(self):
        for path in (self.root / support.TEMPLATES[0], self.work / 'module-native.yaml'):
            with self.subTest(file=path.name):
                original = path.read_bytes()
                path.write_bytes(b'changed synthetic input\n')
                self.assertEqual(self.cli('save').returncode, 1)
                path.write_bytes(original)

    def test_replacement_during_copy_fails_without_publishing_or_pruning(self):
        old = self.save()
        original = self.recovery.shutil.copy2
        def copy(source, destination):
            if Path(source) == self.core:
                self.core.write_bytes(b'synthetic copy-time replacement\n')
            return original(source, destination)
        with mock.patch.object(self.recovery.shutil, 'copy2', copy):
            with self.assertRaisesRegex(support.SnapshotError, 'Packed core'):
                self.recovery.save('copy-race', 1)
        self.assertTrue(old.exists())
        self.assertEqual(list((self.local / 'snapshots').iterdir()), [old])

    def test_marker_replacement_during_copy_uses_checked_bytes(self):
        marker_bytes = self.marker.read_bytes()
        original = self.recovery.shutil.copy2
        def copy(source, destination):
            self.write(self.marker, {'passed': False})
            return original(source, destination)
        with mock.patch.object(self.recovery.shutil, 'copy2', copy):
            folder = self.recovery.save('marker-race', 3)
        self.assertEqual((folder / support.MARKER).read_bytes(), marker_bytes)
        self.assertTrue(self.recovery.verify(folder)['validated'])

    def test_rehashed_file_manifest_cannot_hide_changed_core(self):
        folder = self.save()
        (folder / 'versions/clash_meta').write_bytes(b'synthetic packed replacement\n')
        self.rewrite_manifest(folder)
        self.assertEqual(self.cli('verify', folder).returncode, 1)
        self.assertEqual(self.cli('restore', folder, '--destination', self.local / 'refused').returncode, 1)
        self.assertFalse((self.local / 'refused').exists())

    def test_metadata_cannot_disagree_with_embedded_evidence(self):
        folder = self.save()
        manifest = json.loads((folder / 'snapshot.json').read_text(encoding='utf-8'))
        manifest['tool_versions']['core_sha256'] = '0' * 64
        self.write(folder / 'snapshot.json', manifest)
        self.assertEqual(self.cli('verify', folder).returncode, 1)

    def test_missing_or_non_boolean_success_is_rejected(self):
        for value in (False, 'true', 1, None):
            with self.subTest(value=value):
                changed = dict(self.evidence, passed=value)
                self.write(self.marker, changed)
                self.assertEqual(self.cli('save').returncode, 1)
        self.write(self.marker, {})
        self.assertEqual(self.cli('save').returncode, 1)

    def test_packed_success_flags_and_schema_are_checked(self):
        folder = self.save()
        evidence = json.loads((folder / support.MARKER).read_text(encoding='utf-8'))
        evidence['passed'] = 'true'
        self.write(folder / support.MARKER, evidence)
        self.rewrite_manifest(folder)
        self.assertEqual(self.cli('verify', folder).returncode, 1)

    def test_unknown_or_malformed_manifest_is_rejected(self):
        folder = self.save()
        original = json.loads((folder / 'snapshot.json').read_text(encoding='utf-8'))
        for value in ([], dict(original, format_version=3), dict(original, format_version=2.0)):
            with self.subTest(value_type=type(value).__name__):
                self.write(folder / 'snapshot.json', value)
                self.assertEqual(self.cli('verify', folder).returncode, 1)

    def test_unexpected_bytecode_is_not_ignored_in_bundle(self):
        folder = self.save()
        extra = folder / 'tools/__pycache__'
        extra.mkdir()
        (extra / 'unexpected.pyc').write_bytes(b'synthetic extra')
        self.assertEqual(self.cli('verify', folder).returncode, 1)

    def test_restore_preserves_existing_destination(self):
        folder = self.save()
        destination = self.local / 'existing'
        destination.mkdir()
        (destination / 'sentinel').write_text('preserve', encoding='utf-8')
        self.assertEqual(self.cli('restore', folder, '--destination', destination).returncode, 1)
        self.assertEqual((destination / 'sentinel').read_text(encoding='utf-8'), 'preserve')
        self.assertEqual(self.cli('restore', folder, '--destination', folder / 'nested').returncode, 1)

    def legacy_bundle(self):
        folder = self.local / 'snapshots/20000101T000000000001Z'
        folder.mkdir(parents=True)
        for name, source in support.bundle_sources(self.root, self.settings).items():
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        legacy = {key: self.evidence[key] for key in ('passed', 'template_sha256', 'source_hashes', 'tool_versions')}
        self.write(folder / support.MARKER, legacy)
        self.write(folder / 'snapshot.json', {'validated': True, 'label': 'synthetic-legacy',
                   'template_sha256': legacy['template_sha256'], 'tool_versions': legacy['tool_versions']})
        self.rewrite_manifest(folder)
        return folder

    def test_legacy_bundle_is_read_only_and_scope_is_explicit(self):
        folder = self.legacy_bundle()
        before = (folder / 'snapshot.json').read_bytes()
        result = self.cli('verify', folder)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['legacy'])
        self.assertEqual(report['evidence_scope'], 'legacy-recorded-inputs')
        self.assertEqual((folder / 'snapshot.json').read_bytes(), before)
        (folder / 'versions/clash_meta').write_bytes(b'synthetic changed legacy core')
        self.rewrite_manifest(folder)
        self.assertEqual(self.cli('verify', folder).returncode, 1)

    def test_legacy_marker_cannot_create_new_validated_bundle(self):
        legacy = {key: self.evidence[key] for key in ('passed', 'template_sha256', 'source_hashes', 'tool_versions')}
        self.write(self.marker, legacy)
        before = self.marker.read_bytes()
        self.assertEqual(self.cli('save').returncode, 1)
        self.assertEqual(self.marker.read_bytes(), before)

    def test_retention_preserves_invalid_and_foreign_directories(self):
        old = self.save()
        foreign = old.parent / 'manual-copy'
        shutil.copytree(old, foreign)
        questionable = old.parent / '20000101T000000000002Z'
        questionable.mkdir()
        (questionable / 'snapshot.json').write_text('{}', encoding='utf-8')
        new = self.save(keep=1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists() and foreign.exists() and questionable.exists())

    def test_validation_lock_blocks_save_without_changing_evidence(self):
        before = self.marker.read_bytes()
        with support.exclusive_lock(self.local / 'validation.lock'):
            self.assertEqual(self.cli('save').returncode, 1)
        self.assertEqual(self.marker.read_bytes(), before)

    def test_retention_failure_reports_saved_bundle_with_warning(self):
        old = self.save()
        warning = io.StringIO()
        with mock.patch.object(self.recovery, 'prune', side_effect=PermissionError), mock.patch('sys.stderr', warning):
            new = self.recovery.save('retention-warning', 1)
        self.assertTrue(old.exists() and new.exists())
        self.assertTrue(self.recovery.verify(new)['validated'])
        self.assertIn('retention cleanup incomplete', warning.getvalue())

    def test_start_invalidates_old_success_even_if_settings_are_bad(self):
        self.write(self.local / 'settings.json', {})
        with self.assertRaises(KeyError):
            self.runner.start_validation(self.root, self.fake_wsl_digest)
        self.assertIs(json.loads(self.marker.read_text(encoding='utf-8'))['passed'], False)

    def test_wsl_core_mismatch_cannot_produce_success(self):
        with self.assertRaisesRegex(support.SnapshotError, 'WSL'):
            self.runner.start_validation(self.root, lambda _: '0' * 64)
        self.assertIs(json.loads(self.marker.read_text(encoding='utf-8'))['passed'], False)

    def test_wsl_core_changed_at_finish_cannot_produce_success(self):
        session = self.runner.start_validation(self.root, self.fake_wsl_digest)
        with self.assertRaisesRegex(support.SnapshotError, 'WSL'):
            self.runner.finish_validation(self.root, session, lambda _: '0' * 64)
        self.assertIs(json.loads(self.marker.read_text(encoding='utf-8'))['passed'], False)

    def test_inputs_changed_during_regression_cannot_produce_success(self):
        session = self.runner.start_validation(self.root, self.fake_wsl_digest)
        (self.root / 'overwrite/fixture.yaml').write_text('changed synthetic source\n', encoding='utf-8')
        with self.assertRaisesRegex(support.SnapshotError, 'Inputs changed'):
            self.runner.finish_validation(self.root, session, self.fake_wsl_digest)
        self.assertIs(json.loads(self.marker.read_text(encoding='utf-8'))['passed'], False)

    def test_settings_changed_during_regression_cannot_produce_success(self):
        session = self.runner.start_validation(self.root, self.fake_wsl_digest)
        self.write(self.local / 'settings.json', dict(self.settings, core_version='changed'))
        with self.assertRaisesRegex(support.SnapshotError, 'Settings changed'):
            self.runner.finish_validation(self.root, session, self.fake_wsl_digest)

    def test_failed_child_report_cannot_produce_success(self):
        session = self.runner.start_validation(self.root, self.fake_wsl_digest)
        self.write(self.work / 'module-runtime.safe.json', {'passed': False, 'core_stopped': True})
        with self.assertRaises(support.SnapshotError):
            self.runner.finish_validation(self.root, session, self.fake_wsl_digest)
        self.assertIs(json.loads(self.marker.read_text(encoding='utf-8'))['passed'], False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
