"""Windows/WSL foreground regression entry point; never commits or deploys."""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

REPO=pathlib.Path(__file__).resolve().parents[1]
LOCAL=REPO/'.local'

def digest(path):return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
def wslpath(path):
    value=pathlib.Path(path).resolve().as_posix()
    return '/mnt/'+value[0].lower()+value[2:] if len(value)>1 and value[1]==':' else value

def main():
    settings=json.loads((LOCAL/'settings.json').read_text(encoding='utf-8'))
    assert digest(settings['converter'])==settings['converter_sha256'],'Converter differs from verified binary'
    assert digest(settings['core'])==settings['core_sha256'],'Core differs from verified binary'
    env=dict(os.environ,PYTHONIOENCODING='utf-8')
    def run(command):subprocess.run(command,cwd=REPO,env=env,check=True)
    start=time.time()
    report={'passed':False,'template_sha256':digest(REPO/'MetaFullauto_Full.ini'),'tool_versions':{k:settings[k] for k in ('core_version','converter_version','core_sha256','converter_sha256')}}
    output=LOCAL/'validation'/'all-checks.safe.json';output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    run([sys.executable,str(REPO/'tools'/'check-local.py')])
    run([sys.executable,str(REPO/'tools'/'overwrite-check.py')])
    for filename in ['runtime-tests.py','test-safety.py']:
        run(['wsl.exe','-d',settings.get('wsl_distro','Ubuntu-24.04'),'--exec','/usr/bin/python3','-u',wslpath(REPO/'tools'/filename),wslpath(LOCAL),settings['core_wsl']])
    run([sys.executable,str(REPO/'tools'/'privacy.py'),'--history'])
    behavior=json.loads((LOCAL/'validation'/'native-behavior.safe.json').read_text(encoding='utf-8'))
    safety=json.loads((LOCAL/'validation'/'safety-tests.safe.json').read_text(encoding='utf-8'))
    modules=json.loads((LOCAL/'validation'/'module-verification.safe.json').read_text(encoding='utf-8'))
    module_runtime=json.loads((LOCAL/'validation'/'module-runtime.safe.json').read_text(encoding='utf-8'))
    assert behavior['passed'] and behavior['core_stopped'] and safety['passed']
    assert modules['passed'] and module_runtime['passed'] and module_runtime['core_stopped']
    assert report['template_sha256']==digest(REPO/'MetaFullauto_Full.ini')==safety['template_sha256']
    report.update({'passed':True,'routing_cases':len(behavior['routing']),'legacy_rules_retained':modules['legacy_prepend_rules'],'module_composition_passed':True,'seconds':round(time.time()-start,1),'production_changes':False,'business_verification_pending':True})
    report['source_hashes']={p.relative_to(REPO).as_posix():digest(p) for folder in ['tools','overwrite'] for p in (REPO/folder).iterdir() if p.is_file()}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
