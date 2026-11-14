"""Shared file/evidence rules for regression production and recovery bundles."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

FORMAT_VERSION = 2
TEMPLATES = ('MetaFullauto_Full.ini', 'Aethersailor 规则 重度分流版.ini')
REPORTS = (
    'converted-native-test.yaml', 'module-native.yaml',
    'conversion-verification.safe.json', 'native-behavior.safe.json',
    'safety-tests.safe.json', 'provider-downloads.safe.json',
    'module-verification.safe.json', 'module-runtime.safe.json',
)
MARKER = 'all-checks.safe.json'
DATA_EXCLUDES = {'cache.db', '.cache.db', 'behavior-nodes.yaml'}
SHA256 = re.compile(r'[0-9a-f]{64}\Z')


class SnapshotError(ValueError):
    """Public, value-free failure reasons suitable for local CLI output."""


def require(condition, message):
    if not condition:
        raise SnapshotError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def relative_file(root, name):
    require(isinstance(name, str) and bool(name), 'Invalid relative bundle path')
    pure = PurePosixPath(name)
    require(not pure.is_absolute() and pure.as_posix() == name and
            '..' not in pure.parts and '\\' not in name and ':' not in name,
            'Invalid relative bundle path')
    root = Path(root).resolve()
    path = root.joinpath(*pure.parts)
    require(path.resolve().is_relative_to(root), 'Bundle path escaped its root')
    require(not any(p.is_symlink() for p in (path, *path.parents) if p != root and root in p.parents),
            'Symbolic links are not accepted in bundles')
    return path


def files_under(folder, excludes=(), ignore_bytecode=True):
    folder = Path(folder)
    require(not folder.is_symlink(), 'Linked source/data directories are not accepted')
    folder = folder.resolve()
    require(folder.is_dir(), 'Required source/data directory is missing')
    result = {}
    for path in sorted(folder.rglob('*')):
        name = path.relative_to(folder).as_posix()
        if (ignore_bytecode and ('__pycache__' in path.relative_to(folder).parts or path.suffix == '.pyc')) or path.name in excludes:
            continue
        checked = relative_file(folder, name)
        if checked.is_file():
            result[name] = checked
    return result


def source_files(repo):
    return {folder + '/' + name: path for folder in ('tools', 'overwrite')
            for name, path in files_under(Path(repo) / folder).items()}


def digest_map(value):
    require(isinstance(value, dict) and bool(value), 'Missing digest inventory')
    require(all(isinstance(k, str) and isinstance(v, str) and SHA256.fullmatch(v)
                for k, v in value.items()), 'Invalid digest inventory')
    return value


def capture_inputs(repo, settings):
    """Capture inputs before tests, then compare the same inventory afterwards."""
    repo = Path(repo)
    versions = {}
    for name in ('core', 'converter'):
        actual = sha(Path(settings[name]))
        require(actual == settings[name + '_sha256'], 'Binary differs from configured digest')
        require(isinstance(settings[name + '_version'], str), 'Missing binary version')
        versions[name + '_version'] = settings[name + '_version']
        versions[name + '_sha256'] = actual
    converter = Path(settings['converter'])
    require(converter.suffix.casefold() == '.exe', 'Expected the Windows converter executable')
    converter_files = {p.name: sha(p) for p in sorted(converter.parent.iterdir())
                       if p.is_file() and p.suffix.casefold() in ('.exe', '.dll')}
    require(converter.name in converter_files, 'Converter entry is absent')
    return {
        'template_sha256': sha(repo / TEMPLATES[0]),
        'template_hashes': {name: sha(repo / name) for name in TEMPLATES},
        'source_hashes': {name: sha(path) for name, path in source_files(repo).items()},
        'tool_versions': versions, 'converter_entry': converter.name,
        'converter_files': converter_files,
    }


def bundle_sources(repo, settings):
    repo = Path(repo)
    work = repo / '.local/validation'
    result = {name: relative_file(repo, name) for name in TEMPLATES}
    result.update(source_files(repo))
    for name in ('runtime-data', 'module-data'):
        result.update({name + '/' + rel: path for rel, path in files_under(work / name, DATA_EXCLUDES).items()})
    result.update({name: relative_file(work, name) for name in REPORTS})
    result['versions/clash_meta'] = Path(settings['core']).resolve()
    converter = Path(settings['converter'])
    for path in converter.parent.iterdir():
        if path.is_file() and path.suffix.casefold() in ('.exe', '.dll'):
            result['versions/subconverter/' + path.name] = path
    for name, path in result.items():
        require(path.is_file() and not path.is_symlink(), 'Missing or linked bundle input')
        relative_file(repo / '.local', name)  # Validate the portable destination name.
    return result


def make_evidence(repo, settings, initial, validation_id, wsl_core_sha256, summary):
    require(capture_inputs(repo, settings) == initial, 'Inputs changed during regression')
    require(wsl_core_sha256 == initial['tool_versions']['core_sha256'], 'WSL and packaged core differ')
    sources = bundle_sources(repo, settings)
    return {
        **summary, **initial, 'format_version': FORMAT_VERSION,
        'validation_id': validation_id, 'wsl_core_sha256': wsl_core_sha256,
        'snapshot_inputs': {name: sha(path) for name, path in sources.items()},
        'passed': True,
    }


def current_sources_for_evidence(repo, settings, evidence):
    require(isinstance(evidence, dict), 'Invalid regression evidence')
    require(type(evidence.get('format_version')) is int and evidence['format_version'] == FORMAT_VERSION,
            'Rerun regression to produce versioned evidence')
    require(evidence.get('passed') is True, 'No successful regression evidence')
    current = capture_inputs(repo, settings)
    require(all(evidence.get(key) == value for key, value in current.items()),
            'Template, sources or binaries changed after regression')
    require(evidence.get('wsl_core_sha256') == current['tool_versions']['core_sha256'],
            'WSL and packaged core differ')
    require(isinstance(evidence.get('validation_id'), str) and
            re.fullmatch(r'[0-9a-f]{32}', evidence['validation_id']), 'Invalid validation identifier')
    sources = bundle_sources(repo, settings)
    require({name: sha(path) for name, path in sources.items()} == digest_map(evidence.get('snapshot_inputs')),
            'Bundle inputs changed after regression')
    return sources


def verify_binding(folder, manifest, actual):
    """Verify recorded test identity as well as the self-contained file manifest."""
    folder = Path(folder)
    evidence = json.loads(relative_file(folder, MARKER).read_text(encoding='utf-8'))
    require(isinstance(evidence, dict), 'Invalid regression evidence')
    require(manifest.get('validated') is True and evidence.get('passed') is True,
            'Snapshot does not contain successful validation evidence')
    versions = evidence.get('tool_versions')
    require(isinstance(versions, dict) and manifest.get('tool_versions') == versions,
            'Snapshot tool metadata disagrees with saved evidence')
    for key in ('core_sha256', 'converter_sha256'):
        require(isinstance(versions.get(key), str) and SHA256.fullmatch(versions[key]), 'Missing tested binary digest')
    require(actual.get('versions/clash_meta') == versions['core_sha256'],
            'Packed core differs from tested core')
    require(actual.get(TEMPLATES[0]) == evidence.get('template_sha256') == manifest.get('template_sha256'),
            'Packed template differs from tested template')
    for name, expected in digest_map(evidence.get('source_hashes')).items():
        relative_file(folder, name)
        require(actual.get(name) == expected, 'Packed sources differ from tested sources')
    new = type(manifest.get('format_version')) is int and manifest['format_version'] == FORMAT_VERSION
    if new:
        require(type(evidence.get('format_version')) is int and evidence['format_version'] == FORMAT_VERSION,
                'Evidence format mismatch')
        require(isinstance(evidence.get('validation_id'), str) and
                re.fullmatch(r'[0-9a-f]{32}', evidence['validation_id']) and
                manifest.get('validation_id') == evidence['validation_id'], 'Validation identifier mismatch')
        require(evidence.get('wsl_core_sha256') == versions['core_sha256'], 'WSL evidence mismatch')
        require({name: value for name, value in actual.items() if name.startswith(('tools/', 'overwrite/'))}
                == evidence['source_hashes'], 'Packed source inventory mismatch')
        expected = digest_map(evidence.get('snapshot_inputs'))
        require({name: value for name, value in actual.items() if name != MARKER} == expected,
                'Packed files differ from tested bundle inventory')
        template_hashes = digest_map(evidence.get('template_hashes'))
        require(set(template_hashes) == set(TEMPLATES), 'Template inventory is incomplete')
        for name, expected in template_hashes.items():
            require(actual.get(name) == expected, 'Packed template inventory mismatch')
        converter_files = digest_map(evidence.get('converter_files'))
        require({name.removeprefix('versions/subconverter/'): value for name, value in actual.items()
                 if name.startswith('versions/subconverter/')} == converter_files, 'Converter dependency inventory mismatch')
        require(manifest.get('converter_entry') == evidence.get('converter_entry'), 'Converter entry mismatch')
        require(converter_files.get(evidence['converter_entry']) == versions['converter_sha256'],
                'Packed converter differs from tested converter')
    else:
        require('format_version' not in manifest and 'format_version' not in evidence,
                'Unsupported snapshot/evidence format')
        require(any(value == versions['converter_sha256'] for name, value in actual.items()
                    if name.startswith('versions/subconverter/') and name.casefold().endswith('.exe')),
                'Legacy bundle does not contain the tested converter')
    return {'legacy': not new, 'evidence_scope': 'full-bundle-inputs' if new else 'legacy-recorded-inputs'}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


@contextmanager
def exclusive_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stream = path.open('x', encoding='utf-8')
    except FileExistsError:
        raise SnapshotError('Another operation holds the lock; inspect it before removing a stale lock') from None
    try:
        stream.write(json.dumps({'pid': os.getpid()}))
        stream.flush()
        yield
    finally:
        stream.close()
        path.unlink()
