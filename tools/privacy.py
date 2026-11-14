"""Local push gate: scan complete text/history, never print suspect values."""
import argparse
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import urllib.parse

REPO=pathlib.Path(__file__).resolve().parents[1]
PATTERNS={
 'private-key':r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
 'github-token':r'\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,})\b',
 'cloud-token':r'\b(?:AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{30,}|xox[baprs]-[A-Za-z0-9-]{20,})\b',
 'node-uuid':r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b',
 'proxy-link':r'(?i)\b(?:ss|ssr|vmess|vless|trojan|hysteria2?|tuic)://[^\s\'\"<>]+',
 'jwt':r'\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b',
}

class ScanError(RuntimeError):
    """An incomplete scan must never produce a passing report."""


def git(*args, input=None, allowed=(0,)):
    proc = subprocess.run(
        ['git', '--no-replace-objects', '-C', str(REPO), *args],
        input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode not in allowed:
        raise ScanError('git-read-failed')
    return proc.stdout

def scan(raw,approved_examples=frozenset(),deny=()):
    digest=hashlib.sha256(raw).hexdigest()
    try:text=raw.decode('utf-8-sig')
    except UnicodeDecodeError:return [{'category':'non-text-review-required','line':0}]
    result=[]
    def add(kind,start):result.append({'category':kind,'line':text.count('\n',0,start)+1})
    for category,pattern in PATTERNS.items():
        for m in re.finditer(pattern,text):add(category,m.start())
    for m in re.finditer(r'(?i)\b(?:password|passwd|secret|api[-_]?key|access[-_]?token)\s*[\'\"]?\s*[:=]\s*[\'\"]?([A-Za-z0-9+/=_-]{8,})',text):
        value=m.group(1)
        if value.startswith(('EXAMPLE_','REDACTED','MIHOMO_')) or value in ('None','true','false'):continue
        add('credential-field',m.start())
    for m in re.finditer(r'https?://[^\s<>\"\'`\]\)）]+',text):
        u=urllib.parse.urlsplit(m.group());keys={k.lower() for k in urllib.parse.parse_qs(u.query)}
        if u.username or u.password or keys & {'token','key','apikey','api_key','access_token','auth','secret','password'}:add('authenticated-url',m.start())
    for m in re.finditer(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?',text):
        try:
            token=m.group();ip=ipaddress.ip_address(token.split('/')[0])
        except ValueError:continue
        # Whole network definitions and loopback test fixtures carry no device map.
        if '/' in token:
            net=ipaddress.ip_network(token,strict=False)
            if str(net.network_address)==str(ip) and net.prefixlen<=16:continue
        if ip.is_loopback or ip.is_unspecified:continue
        if ip.is_private and digest not in approved_examples:add('private-host-ip-review-required',m.start())
    for word in deny:
        if len(word)>=4:
            for m in re.finditer(re.escape(word),text,re.I):
                if digest not in approved_examples:add('private-identifier-review-required',m.start())
    return result

FORBIDDEN_SUFFIXES = {'.dat', '.mmdb', '.mrs', '.zip', '.exe', '.dll', '.key', '.pem'}
OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


def forbidden_path(name):
    """Apply one POSIX Git-path policy, including Windows case aliases."""
    path = pathlib.PurePosixPath(name)
    return path.parts[0].casefold() == '.local' or path.suffix.casefold() in FORBIDDEN_SUFFIXES


def push_roots(raw):
    roots = set()
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) != 4:
            raise ScanError('invalid-pre-push-input')
        local, remote = fields[1].decode('ascii'), fields[3].decode('ascii')
        if not OID.fullmatch(local) or not OID.fullmatch(remote):
            raise ScanError('invalid-pre-push-input')
        if set(local) != {'0'}:
            roots.add(local)
    return roots


def ref_state():
    refs = git('for-each-ref', '--format=%(refname) %(objectname)')
    head = git('rev-parse', '--verify', '--quiet', 'HEAD', allowed=(0, 1)).strip()
    return refs, head


def complete_history_required():
    if git('rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ScanError('shallow-history-review-required')
    graft_path = pathlib.Path(git('rev-parse', '--git-path', 'info/grafts').decode().strip())
    if not graft_path.is_absolute():
        graft_path = REPO / graft_path
    if graft_path.exists() and graft_path.read_bytes().strip():
        raise ScanError('grafted-history-review-required')
    partial = git('config', '--get-regexp', r'^(extensions\.partialclone|remote\..*\.promisor)$', allowed=(0, 1))
    if partial:
        raise ScanError('partial-history-review-required')


def review(history=False, extra_roots=()):
    before = None
    if history:
        complete_history_required()
        before = ref_state()
    approved = set(json.loads((REPO / 'privacy-examples.json').read_text(encoding='utf-8')))
    denyfile = REPO / '.local/privacy-deny.json'
    deny = json.loads(denyfile.read_text(encoding='utf-8')) if denyfile.exists() else []
    if not isinstance(deny, list) or any(not isinstance(word, str) for word in deny):
        raise ScanError('invalid-deny-list')
    failures, checked, path_count = [], set(), 0

    def check_path(rawname, **location):
        nonlocal path_count
        name = rawname.decode('utf-8')
        path_count += 1
        rows = scan(rawname, frozenset(), deny)
        # A path may itself contain a private identifier; never echo that value.
        location['file'] = '[redacted]' if rows else name
        if rows:
            location['path_sha256'] = hashlib.sha256(rawname).hexdigest()
            failures.append({**location, 'filename_findings': rows})
        if forbidden_path(name):
            failures.append({**location, 'category': 'private-or-binary-artifact'})
        return name

    def check_blob(oid):
        if oid not in checked:
            checked.add(oid)
            rows = scan(git('cat-file', 'blob', oid), approved, deny)
            if rows:
                failures.append({'object': oid, 'findings': rows})

    # Read staged bytes as well as working bytes: the two may differ.
    working = set(git('ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0'))
    for rawname in sorted(working - {b''}):
        name = check_path(rawname, source='working')
        path = REPO / name
        if path.is_symlink():
            raw = os.readlink(path).encode('utf-8')
        elif path.is_file():
            raw = path.read_bytes()
        else:
            continue
        rows = scan(raw, approved, deny)
        if rows:
            failures.append({'path_sha256': hashlib.sha256(rawname).hexdigest(), 'source': 'working', 'findings': rows})
    for entry in git('ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        metadata, rawname = entry.split(b'\t', 1)
        mode, oid, stage = metadata.split()
        check_path(rawname, source='index')
        if stage != b'0' or mode == b'160000':
            raise ScanError('unmerged-or-submodule-index-review-required')
        check_blob(oid.decode('ascii'))

    commits = []
    if history:
        roots = {line.rsplit(b' ', 1)[1].decode('ascii') for line in before[0].splitlines()}
        if before[1]:
            roots.add(before[1].decode('ascii'))
        roots.update(extra_roots)
        peeled = set()
        for root in sorted(roots):
            if not OID.fullmatch(root):
                raise ScanError('invalid-history-root')
            # Non-commit tags need separate review, rather than partial coverage.
            peeled.add(git('rev-parse', '--verify', '--end-of-options', root + '^{commit}').decode().strip())
            obj = root
            while git('cat-file', '-t', obj).strip() == b'tag':
                raw = git('cat-file', 'tag', obj)
                rows = scan(raw, approved, deny)
                if rows:
                    failures.append({'object': obj, 'findings': rows})
                obj = raw.splitlines()[0].split(b' ', 1)[1].decode('ascii')
        if peeled:
            commits = git('rev-list', '--stdin', input=('\n'.join(sorted(peeled)) + '\n').encode('ascii')).decode().splitlines()
        for commit in commits:
            # Every commit/path pair is checked, independently of blob identity.
            for entry in git('ls-tree', '-r', '-z', '--full-tree', commit).split(b'\0'):
                if not entry:
                    continue
                metadata, rawname = entry.split(b'\t', 1)
                mode, kind, oid = metadata.split()
                check_path(rawname, commit=commit)
                if kind != b'blob':
                    raise ScanError('submodule-history-review-required')
                check_blob(oid.decode('ascii'))
            rows = scan(git('cat-file', 'commit', commit), approved, deny)
            if rows:
                failures.append({'commit': commit, 'findings': rows})
        if before != ref_state():
            raise ScanError('history-refs-changed-during-scan')
    return {
        'checked_items': len(checked), 'checked_paths': path_count, 'checked_commits': len(commits),
        'findings': failures, 'passed': not failures, 'scan_complete': True,
        'scope': 'working files, index and complete reachable history including push roots' if history else 'working files and index',
        'human_review_required_for_new_hostnames': True,
    }


def write_report(report):
    out = REPO / '.local/privacy-review.json'
    out.parent.mkdir(exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=out.parent, delete=False) as stream:
            temporary = pathlib.Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, out)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--pre-push', action='store_true', help='Read Git pre-push update records from stdin; implies --history')
    args = parser.parse_args()
    report = {'passed': False, 'scan_complete': False, 'findings': []}
    try:
        write_report(report)  # Invalidate an older passing result before scanning.
        roots = push_roots(sys.stdin.buffer.read()) if args.pre_push else ()
        report = review(args.history or args.pre_push, roots)
    except (ScanError, OSError, ValueError, KeyError, IndexError, TypeError) as error:
        category = str(error) if isinstance(error, ScanError) else 'scan-read-or-format-error'
        report = {'passed': False, 'scan_complete': False, 'findings': [{'category': category}]}
    try:
        write_report(report)
    except OSError:
        report = {'passed': False, 'scan_complete': False, 'findings': [{'category': 'report-write-failed'}]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] and report['scan_complete'] else 1

if __name__=='__main__':sys.exit(main())
