"""Read validation expectations from the editable INI, without generating rules."""
import hashlib
import pathlib

def manifest_for(path):
    raw=pathlib.Path(path).read_bytes()
    rules=[];groups={}
    for line in raw.decode('utf-8-sig').splitlines():
        if line.startswith('ruleset='):
            group,spec=line.split('=',1)[1].split(',',1)
            rules.append({'group':group,'spec':spec})
        if line.startswith('custom_proxy_group='):
            parts=line.split('=',1)[1].split('`');groups[parts[0]]=parts
    def pattern(name):
        return next(p for p in groups[name][2:] if not p.startswith('[]') and not p.startswith('http'))
    regions={name:pattern(name) for name in ['🇭🇰 香港节点','🇼🇸 台湾节点','🇯🇵 日本节点','🇸🇬 新加坡节点','🇺🇸 美国节点','🇰🇷 韩国节点']}
    for row in rules:
        if not row['spec'].startswith('[]') and row['spec'].rsplit(',',1)[1].split('|')[0]!='172800':raise ValueError('Remote rules must use 48h interval')
    return {'rulesets':rules,'ruleset_count':len(rules),'netflix_filter':pattern('🎥 Netflix自动'),'ai_filter':pattern('🤖 AI稳定出口'),'region_patterns':regions,'other_pattern':pattern('🌐 其他地区'),'sha256':hashlib.sha256(raw).hexdigest()}
