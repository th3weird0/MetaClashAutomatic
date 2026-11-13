"""Keep three verified local bundles; restore into a new directory, never deploy."""
import argparse
import datetime
import hashlib
import json
import pathlib
import shutil

REPO=pathlib.Path(__file__).resolve().parents[1]
LOCAL=REPO/'.local'

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def verify(folder):
    folder=pathlib.Path(folder).resolve()
    manifest=json.loads((folder/'snapshot.json').read_text(encoding='utf-8'))
    actual={p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file() and p.name!='snapshot.json'}
    if actual!=set(manifest['files']):raise ValueError('Snapshot has missing or unexpected files')
    for name,digest in manifest['files'].items():
        path=(folder/name).resolve()
        if not path.is_relative_to(folder) or not path.is_file() or sha(path)!=digest:raise ValueError('Snapshot path/hash validation failed')
    return manifest

def save(label,keep):
    settings=json.loads((LOCAL/'settings.json').read_text(encoding='utf-8'))
    evidence=json.loads((LOCAL/'validation'/'all-checks.safe.json').read_text(encoding='utf-8'))
    if not evidence['passed'] or evidence['template_sha256']!=sha(REPO/'MetaFullauto_Full.ini'):raise ValueError('Current template has no matching successful regression')
    for name,digest in evidence['source_hashes'].items():
        if sha(REPO/name)!=digest:raise ValueError('Tools/modules changed after validation; rerun checks')
    import yaml
    for name in ['synthetic-nodes.yaml','zero-nodes.yaml']:
        nodes=yaml.safe_load((LOCAL/'validation'/'runtime-data'/name).read_text(encoding='utf-8'))['proxies']
        if any(p.get('server')!='127.0.0.1' or any(k in p for k in ('password','uuid','token','secret')) for p in nodes):raise ValueError('Only synthetic loopback fixtures may enter a recovery bundle')
    parent=LOCAL/'snapshots';parent.mkdir(exist_ok=True)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    dest=parent/stamp;dest.mkdir()
    for name in ['MetaFullauto_Full.ini','Aethersailor 规则 重度分流版.ini']:shutil.copy2(REPO/name,dest/name)
    shutil.copytree(REPO/'tools',dest/'tools',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(REPO/'overwrite',dest/'overwrite')
    shutil.copytree(LOCAL/'validation'/'runtime-data',dest/'runtime-data',ignore=shutil.ignore_patterns('cache.db','.cache.db','behavior-nodes.yaml'))
    (dest/'versions').mkdir();shutil.copy2(settings['core'],dest/'versions'/'clash_meta')
    converter=pathlib.Path(settings['converter']);bin_dir=dest/'versions'/'subconverter';bin_dir.mkdir()
    for p in converter.parent.iterdir():
        if p.is_file() and p.suffix.lower() in ('.exe','.dll'):shutil.copy2(p,bin_dir/p.name)
    for name in ['converted-native-test.yaml','conversion-verification.safe.json','native-behavior.safe.json','safety-tests.safe.json','all-checks.safe.json','provider-downloads.safe.json']:
        shutil.copy2(LOCAL/'validation'/name,dest/name)
    files={p.relative_to(dest).as_posix():sha(p) for p in dest.rglob('*') if p.is_file()}
    manifest={'label':label,'validated':True,'template_sha256':evidence['template_sha256'],'tool_versions':evidence['tool_versions'],'files':files,'contains_real_subscriptions':False,'publish_as_ruleset':False}
    (dest/'snapshot.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    verify(dest)
    # Only prune snapshot directories whose validated manifest and hashes pass.
    bundles=sorted(p for p in parent.iterdir() if p.is_dir() and (p/'snapshot.json').is_file())
    for old in bundles[:-max(1,keep)]:
        resolved=old.resolve()
        if not resolved.is_relative_to(parent.resolve()):raise ValueError('Snapshot pruning escaped its directory')
        verify(old);shutil.rmtree(old)
    return dest

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('save');s.add_argument('--label',default='verified');s.add_argument('--keep',type=int,default=3)
    v=sub.add_parser('verify');v.add_argument('snapshot')
    r=sub.add_parser('restore');r.add_argument('snapshot');r.add_argument('--destination',required=True)
    args=p.parse_args()
    if args.command=='save':print(save(args.label,args.keep))
    elif args.command=='verify':print(json.dumps({'verified':True,'template_sha256':verify(args.snapshot)['template_sha256']}))
    else:
        source=pathlib.Path(args.snapshot).resolve();verify(source)
        destination=pathlib.Path(args.destination).resolve()
        if destination.exists():raise ValueError('Restore destination must be new; existing data is preserved')
        if destination.is_relative_to(source):raise ValueError('Restore destination cannot be inside its source snapshot')
        shutil.copytree(source,destination);verify(destination)
        print(json.dumps({'restored':True,'deployed':False,'destination':str(destination)},ensure_ascii=False))

if __name__=='__main__':main()
