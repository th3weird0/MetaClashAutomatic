"""Windows/WSL foreground regression entry point; never commits or deploys."""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import uuid

from snapshot_support import (
    FORMAT_VERSION, MARKER, SnapshotError, atomic_json, capture_inputs,
    exclusive_lock, make_evidence, require, sha,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
LOCAL = REPO / '.local'


def wslpath(path):
    value = pathlib.Path(path).resolve().as_posix()
    return '/mnt/' + value[0].lower() + value[2:] if len(value) > 1 and value[1] == ':' else value


def wsl_core_digest(settings):
    code = 'import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())'
    result = subprocess.run([
        'wsl.exe', '-d', settings.get('wsl_distro', 'Ubuntu-24.04'), '--exec',
        '/usr/bin/python3', '-c', code, settings['core_wsl'],
    ], capture_output=True, check=True, timeout=60)
    return result.stdout.decode('ascii').strip()


def start_validation(repo, core_digest=wsl_core_digest):
    repo = pathlib.Path(repo)
    output = repo / '.local/validation' / MARKER
    validation_id = uuid.uuid4().hex
    # Invalidate old success before even reading settings or checking binaries.
    atomic_json(output, {'format_version': FORMAT_VERSION, 'validation_id': validation_id, 'passed': False})
    raw = (repo / '.local/settings.json').read_bytes()
    settings = json.loads(raw)
    state = capture_inputs(repo, settings)
    require(core_digest(settings) == state['tool_versions']['core_sha256'], 'WSL and packaged core differ')
    return {'settings': settings, 'settings_sha256': hashlib.sha256(raw).hexdigest(),
            'state': state, 'validation_id': validation_id, 'started': time.time()}


def finish_validation(repo, session, core_digest=wsl_core_digest):
    repo = pathlib.Path(repo)
    require(sha(repo / '.local/settings.json') == session['settings_sha256'], 'Settings changed during regression')
    work = repo / '.local/validation'
    def report(name):
        return json.loads((work / name).read_text(encoding='utf-8'))
    behavior = report('native-behavior.safe.json')
    safety = report('safety-tests.safe.json')
    modules = report('module-verification.safe.json')
    module_runtime = report('module-runtime.safe.json')
    require(all(r.get('passed') is True for r in (behavior, safety, modules, module_runtime)), 'A regression report did not pass')
    require(behavior.get('core_stopped') is True and module_runtime.get('core_stopped') is True,
            'An isolated test core has not stopped')
    require(safety.get('template_sha256') == session['state']['template_sha256'], 'Safety report template mismatch')
    summary = {
        'routing_cases': len(behavior['routing']), 'legacy_rules_retained': modules['legacy_prepend_rules'],
        'module_composition_passed': True, 'seconds': round(time.time() - session['started'], 1),
        'production_changes': False, 'business_verification_pending': True,
    }
    result = make_evidence(repo, session['settings'], session['state'], session['validation_id'],
                           core_digest(session['settings']), summary)
    atomic_json(work / MARKER, result)
    return result


def main():
    try:
        with exclusive_lock(LOCAL / 'validation.lock'):
            session = start_validation(REPO)
            settings = session['settings']
            env = dict(os.environ, PYTHONIOENCODING='utf-8')
            def run(command):
                subprocess.run(command, cwd=REPO, env=env, check=True)
            # These offline tool tests never start a core or write a real success marker.
            for filename in ('test_privacy.py', 'test_recovery.py'):
                run([sys.executable, str(REPO / 'tools' / filename)])
            run([sys.executable, str(REPO / 'tools/check-local.py')])
            run([sys.executable, str(REPO / 'tools/overwrite-check.py')])
            for filename in ('runtime-tests.py', 'test-safety.py'):
                run(['wsl.exe', '-d', settings.get('wsl_distro', 'Ubuntu-24.04'), '--exec', '/usr/bin/python3', '-u',
                     wslpath(REPO / 'tools' / filename), wslpath(LOCAL), settings['core_wsl']])
            run([sys.executable, str(REPO / 'tools/privacy.py'), '--history'])
            result = finish_validation(REPO, session)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # start_validation already invalidated old success; lock contention leaves the writer alone.
        reason = str(error) if isinstance(error, SnapshotError) else 'Regression command or input failed'
        print(json.dumps({'passed': False, 'error': reason}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
