"""Local push gate: scan complete text/history, never print suspect values."""
import argparse
import hashlib
import ipaddress
import json
import pathlib
import re
import subprocess
import sys
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

def git(*args):return subprocess.check_output(['git','-C',str(REPO),*args])

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

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--history',action='store_true');args=p.parse_args()
    approvals=json.loads((REPO/'privacy-examples.json').read_text(encoding='utf-8'))
    approved=set(approvals)
    local=REPO/'.local'/'privacy-deny.json'
    deny=json.loads(local.read_text(encoding='utf-8')) if local.exists() else []
    failures=[];count=0
    paths=git('ls-files','-z','--cached','--others','--exclude-standard').split(b'\0')
    for rawname in paths:
        if not rawname:continue
        name=rawname.decode();path=REPO/name
        if not path.is_file():continue
        if name.startswith('.local/') or path.suffix.lower() in ('.dat','.mmdb','.mrs','.zip','.exe','.dll','.key','.pem'):
            failures.append({'file':name,'category':'private-or-binary-artifact'});continue
        rows=scan(path.read_bytes(),approved,deny)+scan(name.encode(),frozenset(),deny);count+=1
        if rows:failures.append({'file':name,'findings':rows})
    if args.history:
        # rev-list includes refs on every local branch/tag; no fetch or remote writes.
        checked=set()
        for line in git('rev-list','--objects','--all').decode().splitlines():
            oid=line.split(' ',1)[0]
            if ' ' in line:
                rows=scan(line.split(' ',1)[1].encode(),frozenset(),deny)
                if rows:failures.append({'object':oid,'filename_findings':rows})
            if oid in checked or git('cat-file','-t',oid).strip()!=b'blob':continue
            checked.add(oid);raw=git('cat-file','blob',oid);count+=1
            rows=scan(raw,approved,deny)
            if rows:failures.append({'object':oid,'findings':rows})
        for oid in git('rev-list','--all').decode().splitlines():
            raw=git('show','-s','--format=%B',oid);rows=scan(raw,approved,deny)
            if rows:failures.append({'commit':oid,'findings':rows})
    report={'checked_items':count,'findings':failures,'passed':not failures,'scope':'working files and complete reachable history' if args.history else 'working files','human_review_required_for_new_hostnames':True}
    out=REPO/'.local'/'privacy-review.json';out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 1 if failures else 0

if __name__=='__main__':sys.exit(main())
