"""Shared candidate, cache and controller helpers for MetaClashAutomatic."""
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

import yaml

FISH = '🐟 漏网之鱼'
MANUAL = '🚀 手动选择'
AI = '🤖 AI稳定出口'
AI_SERVICE = '🤖Ai服务'
NF = '🎥 Netflix'
NF_AUTO = '🎥 Netflix自动'
BUILTINS = {'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'COMPATIBLE'}
AUTOMATIC = {'url-test', 'fallback', 'load-balance'}
REQUIRED = {FISH, MANUAL, AI, AI_SERVICE, NF, NF_AUTO}


class CheckError(ValueError):
    """A fixed diagnostic code, with no configuration values in the message."""

    def __init__(self, code, **details):
        super().__init__(code)
        self.details = details


def require(condition, code, **details):
    if not condition:
        raise CheckError(code, **details)


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        require(key not in result, 'DUPLICATE_YAML_KEY')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        result = yaml.load(stream, Loader=UniqueLoader)
    require(isinstance(result, dict), 'YAML_ROOT_MUST_BE_MAPPING')
    return result


def named_rows(rows):
    require(isinstance(rows, list), 'EXPECTED_OBJECT_LIST')
    result = {}
    for row in rows:
        require(isinstance(row, dict) and isinstance(row.get('name'), str)
                and bool(row['name']), 'INVALID_NAMED_OBJECT')
        require(row['name'] not in result, 'DUPLICATE_OBJECT_NAME')
        result[row['name']] = row
    return result


class NodeInventory(dict):
    """Keep direct proxy membership separate from provider membership."""


def inventory(data, home):
    nodes = NodeInventory(named_rows(data.get('proxies', [])))
    nodes.direct_names = set(nodes)
    require(not nodes.keys() & BUILTINS, 'NODE_NAME_COLLIDES_WITH_BUILTIN')
    providers = {}
    definitions = data.get('proxy-providers', {})
    require(isinstance(definitions, dict), 'INVALID_PROXY_PROVIDERS')
    for name, provider in definitions.items():
        require(isinstance(provider, dict), 'INVALID_PROXY_PROVIDER')
        require(not provider.get('filter') and not provider.get('exclude-filter')
                and not provider.get('override'), 'PROVIDER_TRANSFORM_REQUIRES_CORE_CHECK')
        if provider.get('type') == 'inline':
            rows = provider.get('payload', [])
        else:
            require(provider.get('type') in ('file', 'http'), 'UNSUPPORTED_PROVIDER_TYPE')
            require(isinstance(provider.get('path'), str) and provider['path'],
                    'PROVIDER_CACHE_PATH_REQUIRED')
            path = Path(provider['path'])
            if not path.is_absolute():
                path = Path(home) / path
            require(path.is_file(), 'PROVIDER_CACHE_MISSING')
            rows = load(path).get('proxies', [])
        incoming = named_rows(rows)
        require(not incoming.keys() & BUILTINS, 'NODE_NAME_COLLIDES_WITH_BUILTIN')
        require(all(name not in nodes or nodes[name] == row for name, row in incoming.items()),
                'AMBIGUOUS_NODE_NAME')
        providers[name] = set(incoming)
        nodes.update(incoming)
    return nodes, providers


def matches(pattern, name):
    require(isinstance(pattern, str), 'INVALID_GROUP_FILTER')
    # Mihomo supports multiple alternatives separated by a backtick.
    return any(re.search(part, name) is not None for part in pattern.split('`'))


def members(group, nodes, providers):
    explicit = group.get('proxies', [])
    use = group.get('use', [])
    require(isinstance(explicit, list) and all(isinstance(n, str) for n in explicit)
            and isinstance(use, list) and all(isinstance(n, str) for n in use),
            'INVALID_GROUP_MEMBERS')
    require(not group.get('exclude-type'), 'EXCLUDE_TYPE_REQUIRES_CORE_CHECK')
    values = set(explicit)
    pattern = group.get('filter', '.*')
    included_providers = providers if group.get('include-all') or group.get('include-all-providers') else use
    for name in included_providers:
        require(name in providers, 'PROXY_PROVIDER_REFERENCE_MISSING')
        values.update(n for n in providers[name] if matches(pattern, n))
    if group.get('include-all') or group.get('include-all-proxies'):
        values.update(n for n in nodes.direct_names if matches(pattern, n))
    excluded = group.get('exclude-filter')
    if excluded:
        values = {n for n in values if not matches(excluded, n)}
    return values


def validate_ruleset_references(data):
    providers = data.get('rule-providers', {})
    require(isinstance(providers, dict), 'INVALID_RULE_PROVIDERS')
    rules = list(data.get('rules', []))
    for rows in data.get('sub-rules', {}).values():
        rules.extend(rows)
    for rule in rules:
        require(isinstance(rule, str), 'INVALID_ROUTING_RULE')
        for ref in re.finditer(r'(?:^|[,(])RULE-SET,([^,]+)', rule):
            name = ref.group(1)
            require(name in providers or (ref.start() != 0 and name.rstrip(')') in providers),
                    'ROUTING_RULE_PROVIDER_MISSING')
    for field in ('nameserver-policy', 'proxy-server-nameserver-policy'):
        for key in data.get('dns', {}).get(field, {}):
            if str(key).startswith('rule-set:'):
                require(all(name in providers for name in str(key)[len('rule-set:'):].split(',')),
                        'DNS_RULE_PROVIDER_MISSING')


def group_inventory(data, nodes, providers):
    groups = named_rows(data.get('proxy-groups', []))
    require(REQUIRED <= groups.keys(), 'METACLASH_GROUP_MISSING')
    require(not groups.keys() & (nodes.keys() | BUILTINS), 'GROUP_NAME_COLLISION')
    expanded = {name: members(group, nodes, providers) for name, group in groups.items()}
    for values in expanded.values():
        require(values <= nodes.keys() | groups.keys() | BUILTINS, 'OUTBOUND_REFERENCE_MISSING')
    leaves = {}
    def visit(name, stack):
        require(name not in stack, 'CYCLIC_GROUP_REFERENCE')
        if name in leaves:
            return leaves[name]
        values = set()
        for child in expanded[name]:
            values.update(visit(child, stack | {name}) if child in groups else {child})
        leaves[name] = values
        return values
    for name in groups:
        visit(name, set())
    return groups, expanded, leaves


def check_config(data, home):
    validate_ruleset_references(data)
    nodes, providers = inventory(data, home)
    groups, expanded, leaves = group_inventory(data, nodes, providers)
    for number, (name, group) in enumerate(groups.items(), 1):
        if group['type'] in AUTOMATIC:
            require(bool(leaves[name] & nodes.keys()), 'AUTOMATIC_GROUP_HAS_ZERO_NODES', group_number=number)
    nf = expanded[NF_AUTO] & nodes.keys()
    ai = expanded[AI] & nodes.keys()
    require(bool(nf), 'NETFLIX_HAS_ZERO_NODES')
    require(bool(ai), 'AI_HAS_ZERO_NODES')
    require(isinstance(groups[NF_AUTO].get('filter'), str), 'NETFLIX_FILTER_MISSING')
    require(all(matches(groups[NF_AUTO]['filter'], n) for n in nf), 'NETFLIX_FILTER_BYPASSED')
    require(expanded[AI_SERVICE] == {AI}, 'AI_SERVICE_POOL_BYPASSED')
    return {
        'passed': True, 'scope': 'config-structure', 'nodes': len(nodes),
        'proxy_providers': len(providers), 'groups': len(groups),
        'eligible_netflix': len(nf), 'eligible_ai': len(ai),
        'automatic_groups': sum(g['type'] in AUTOMATIC for g in groups.values()),
        'empty_fallback_reject_groups': sum(g.get('empty-fallback') == 'REJECT' for g in groups.values()),
        'catchall_default_manual': groups[FISH].get('proxies', [])[:1] == [MANUAL],
        'business_checks': 'Run Netflix playback and an authenticated AI request separately.',
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CheckError('CONTROLLER_REDIRECT_REFUSED')


def read_controller(base, timeout=10):
    parsed = urllib.parse.urlsplit(base)
    require(parsed.scheme in ('http', 'https') and parsed.hostname
            and not parsed.username and not parsed.password and not parsed.query
            and not parsed.fragment and parsed.path in ('', '/'), 'INVALID_CONTROLLER_URL')
    secret = os.environ.get('MIHOMO_API_SECRET', '')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {'Authorization': 'Bearer ' + secret} if secret else {}
    def get(path):
        request = urllib.request.Request(base.rstrip('/') + path, headers=headers, method='GET')
        try:
            with opener.open(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            raise CheckError('CONTROLLER_AUTH_FAILED' if error.code in (401, 403)
                             else 'CONTROLLER_HTTP_ERROR') from None
        require(isinstance(result, dict), 'INVALID_CONTROLLER_RESPONSE')
        return result
    return get('/version'), get('/configs'), get('/proxies'), get('/providers/rules')


def convert_subscription(backend, subscription, template, output):
    """Use the same native-provider request shape as tools/check-local.py."""
    parsed = urllib.parse.urlsplit(backend)
    require(parsed.scheme in ('http', 'https') and parsed.hostname
            and not parsed.username and not parsed.password and not parsed.query
            and not parsed.fragment and parsed.path.rstrip('/').endswith('/sub'),
            'INVALID_CONVERTER_URL')
    require(bool(subscription.strip()), 'SUBSCRIPTION_REQUIRED')
    require(not Path(output).exists(), 'OUTPUT_ALREADY_EXISTS')
    query = urllib.parse.urlencode({'target': 'clash', 'expand': 'false',
                                   'url': subscription.strip(), 'config': template})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(backend.rstrip('/') + '?' + query, timeout=140) as response:
        raw = response.read()
    data = yaml.load(raw.decode('utf-8-sig'), Loader=UniqueLoader)
    require(isinstance(data, dict) and bool(data.get('proxy-groups'))
            and bool(data.get('rules')), 'INVALID_CONVERTER_OUTPUT')
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with Path(output).open('xb') as stream:
        stream.write(raw)
    return {'passed': True, 'scope': 'conversion-download', 'bytes': len(raw),
            'next_step': 'Run config with the provider caches, then validate using the target core.'}


def fetch_missing_proxy_caches(data, home):
    """Explicitly populate missing HTTP node caches below the given data directory."""
    root = Path(home).resolve()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    targets = []
    for provider in data.get('proxy-providers', {}).values():
        if provider.get('type') != 'http':
            continue
        require(not provider.get('header'), 'PROVIDER_HEADERS_REQUIRE_CLIENT_DOWNLOAD')
        relative = Path(provider.get('path', ''))
        require(str(relative) not in ('', '.') and not relative.is_absolute()
                and '..' not in relative.parts and ':' not in str(relative), 'CACHE_PATH_MUST_BE_RELATIVE')
        target = (root / relative).resolve()
        require(target.is_relative_to(root), 'CACHE_PATH_ESCAPES_DATA_DIR')
        if target.is_file():
            continue
        parsed = urllib.parse.urlsplit(provider.get('url', ''))
        require(parsed.scheme in ('http', 'https') and parsed.hostname
                and not parsed.username and not parsed.password, 'INVALID_PROVIDER_URL')
        targets.append((target, provider['url']))
    for target, url in targets:
        with opener.open(url, timeout=45) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
        require(len(raw) <= 16 * 1024 * 1024, 'PROVIDER_CACHE_TOO_LARGE')
        payload = yaml.load(raw.decode('utf-8-sig'), Loader=UniqueLoader)
        require(isinstance(payload, dict), 'INVALID_PROVIDER_CACHE')
        named_rows(payload.get('proxies', []))
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw)
    return len(targets)


def check_state(version, config, proxies_response, providers_response):
    state = proxies_response.get('proxies')
    providers = providers_response.get('providers')
    require(isinstance(state, dict) and isinstance(providers, dict)
            and all(isinstance(row, dict) for row in state.values())
            and all(isinstance(row, dict) for row in providers.values()), 'INVALID_CONTROLLER_RESPONSE')
    require(REQUIRED <= state.keys(), 'METACLASH_GROUP_MISSING')
    def leaf_nodes(name, stack=frozenset()):
        require(name not in stack, 'CYCLIC_RUNTIME_GROUP')
        require(name in state, 'RUNTIME_REFERENCE_MISSING')
        row = state[name]
        if 'all' not in row:
            return set() if name in BUILTINS else {name}
        require(isinstance(row['all'], list) and all(isinstance(n, str) for n in row['all']),
                'INVALID_RUNTIME_GROUP')
        result = set()
        for child in row['all']:
            if child not in BUILTINS:
                result.update(leaf_nodes(child, stack | {name}))
        return result
    counts = {name: len(leaf_nodes(name)) for name, row in state.items() if 'all' in row}
    nf = leaf_nodes(NF_AUTO)
    ai = leaf_nodes(AI)
    healthy_nf = sum(state[n].get('alive') is True for n in nf)
    healthy_ai = sum(state[n].get('alive') is True for n in ai)
    empty_auto = sum(counts[name] == 0 for name in counts
                     if str(state[name].get('type', '')).lower() in ('urltest', 'fallback', 'loadbalance'))
    empty_providers = sum(type(p.get('ruleCount')) is not int or p['ruleCount'] <= 0 for p in providers.values())
    checks = {
        'rule_mode': config.get('mode') == 'rule',
        'netflix_candidates': bool(nf), 'ai_candidates': bool(ai),
        'netflix_healthy_nodes': healthy_nf > 0, 'ai_healthy_nodes': healthy_ai > 0,
        'automatic_groups_have_nodes': empty_auto == 0,
        'rule_providers_loaded': bool(providers) and empty_providers == 0,
        'catchall_manual': state[FISH].get('now') == MANUAL,
        'ai_service_uses_pool': state[AI_SERVICE].get('now') == AI,
    }
    return {
        'passed': all(checks.values()), 'scope': 'running-controller-state', 'checks': checks,
        'groups': len(counts), 'eligible_netflix': len(nf), 'eligible_ai': len(ai),
        'healthy_netflix': healthy_nf, 'healthy_ai': healthy_ai,
        'rule_providers': len(providers), 'empty_rule_providers': empty_providers,
        'ai_fixed': bool(state[AI].get('fixed')), 'core_meta': version.get('meta') is True,
        'business_checks': 'Run Netflix playback and an authenticated AI request separately.',
    }
