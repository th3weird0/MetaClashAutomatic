"""Offline checks using temporary files and a local synthetic HTTP controller."""
import copy
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse

sys.dont_write_bytecode = True
import yaml
import metaclash_checks as checks

HERE = Path(__file__).resolve().parent


def candidate():
    return {
        'proxies': [{'name': 'Japan NF', 'type': 'http', 'server': '127.0.0.1', 'port': 1}],
        'proxy-groups': [
            {'name': checks.MANUAL, 'type': 'select', 'proxies': ['Japan NF']},
            {'name': checks.FISH, 'type': 'select', 'proxies': [checks.MANUAL, 'DIRECT']},
            {'name': checks.AI, 'type': 'url-test', 'proxies': ['REJECT'], 'include-all': True, 'filter': 'Japan'},
            {'name': checks.AI_SERVICE, 'type': 'select', 'proxies': [checks.AI]},
            {'name': checks.NF, 'type': 'select', 'proxies': [checks.NF_AUTO]},
            {'name': checks.NF_AUTO, 'type': 'url-test', 'proxies': ['REJECT'], 'include-all': True, 'filter': 'NF$'},
        ],
        'rule-providers': {'sample': {'type': 'inline', 'behavior': 'domain', 'payload': ['example.com']}},
        'rules': ['RULE-SET,sample,' + checks.MANUAL, 'MATCH,' + checks.FISH],
    }


def runtime():
    result = {'Japan NF': {'type': 'Http', 'alive': True}, 'DIRECT': {'type': 'Direct'}, 'REJECT': {'type': 'Reject'}}
    for group in candidate()['proxy-groups']:
        values = group.get('proxies', [])
        if group['name'] in (checks.NF_AUTO, checks.AI):
            values = ['Japan NF', 'REJECT']
        result[group['name']] = {'type': 'URLTest' if group['type'] == 'url-test' else 'Selector',
                                 'all': values, 'now': next(n for n in values if n != 'REJECT')}
    return result


class ChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='metaclash-user-checks-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = candidate()

    def check(self):
        return checks.check_config(self.data, self.root)

    def group(self, name):
        return next(g for g in self.data['proxy-groups'] if g['name'] == name)

    def rejected(self, code):
        with self.assertRaisesRegex(checks.CheckError, code):
            self.check()

    def cli(self, script=HERE / 'check.py', *args, env=None):
        return subprocess.run([sys.executable, '-B', str(script), *map(str, args)], cwd=self.root,
                              env=env, capture_output=True, encoding='utf-8', timeout=20)

    def write(self, path, data):
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding='utf-8')

    def test_direct_nodes_pass(self):
        report = self.check()
        self.assertTrue(report['passed'])
        self.assertEqual(report['eligible_netflix'], 1)

    def test_inline_and_cached_file_providers(self):
        rows = self.data.pop('proxies')
        self.data['proxy-providers'] = {'inline': {'type': 'inline', 'payload': rows},
                                        'file': {'type': 'file', 'path': 'nodes.yaml'}}
        self.write(self.root / 'nodes.yaml', {'proxies': rows})
        self.assertEqual(self.check()['eligible_ai'], 1)

    def test_missing_cache_rejected(self):
        self.data['proxy-providers'] = {'file': {'type': 'http', 'path': 'absent.yaml'}}
        self.rejected('PROVIDER_CACHE_MISSING')

    def test_include_all_proxies_excludes_provider_only_nodes(self):
        self.data['proxy-providers'] = {'other': {'type': 'inline', 'payload': [
            {'name': 'Japan provider NF', 'type': 'http', 'server': '127.0.0.1', 'port': 2}]}}
        group = self.group(checks.NF_AUTO)
        group.pop('include-all')
        group['include-all-proxies'] = True
        self.assertEqual(self.check()['eligible_netflix'], 1)
        group['include-all-providers'] = True
        self.assertEqual(self.check()['eligible_netflix'], 2)

    def test_unknown_outbound_rejected(self):
        self.group(checks.MANUAL)['proxies'] = ['missing']
        self.rejected('OUTBOUND_REFERENCE_MISSING')

    def test_duplicate_group_and_node_collision_rejected(self):
        self.data['proxy-groups'].append(copy.deepcopy(self.group(checks.MANUAL)))
        self.rejected('DUPLICATE_OBJECT_NAME')
        self.data['proxy-groups'].pop()
        self.data['proxies'].append({'name': checks.MANUAL, 'type': 'http'})
        self.rejected('GROUP_NAME_COLLISION')

    def test_ambiguous_provider_node_rejected(self):
        self.data['proxy-providers'] = {'other': {'type': 'inline', 'payload': [
            {'name': 'Japan NF', 'type': 'http', 'server': '127.0.0.1', 'port': 2}]}}
        self.rejected('AMBIGUOUS_NODE_NAME')

    def test_group_cycle_rejected(self):
        self.group(checks.MANUAL)['proxies'] = [checks.FISH]
        self.rejected('CYCLIC_GROUP_REFERENCE')

    def test_empty_automatic_group_and_sentinel_rejected(self):
        self.group(checks.NF_AUTO)['filter'] = '^absent$'
        with self.assertRaisesRegex(checks.CheckError, 'AUTOMATIC_GROUP_HAS_ZERO_NODES') as caught:
            self.check()
        self.assertEqual(caught.exception.details['group_number'], 6)

    def test_explicit_netflix_filter_bypass_rejected(self):
        self.group(checks.NF_AUTO)['proxies'] = ['Japan NF']
        self.group(checks.NF_AUTO)['filter'] = '^absent$'
        self.rejected('NETFLIX_FILTER_BYPASSED')

    def test_ai_selector_bypass_rejected(self):
        self.group(checks.AI_SERVICE)['proxies'].append('DIRECT')
        self.rejected('AI_SERVICE_POOL_BYPASSED')

    def test_missing_routing_and_dns_rule_providers(self):
        self.data['rules'].insert(0, 'AND,((RULE-SET,missing),(NETWORK,TCP)),DIRECT')
        self.rejected('ROUTING_RULE_PROVIDER_MISSING')
        self.data['rules'].pop(0)
        self.data['dns'] = {'nameserver-policy': {'rule-set:sample,missing': 'system'}}
        self.rejected('DNS_RULE_PROVIDER_MISSING')

    def test_unmodelled_transform_requests_core_check(self):
        self.group(checks.AI)['exclude-type'] = 'Http'
        self.rejected('EXCLUDE_TYPE_REQUIRES_CORE_CHECK')

    def test_duplicate_yaml_keys_rejected_without_echo(self):
        path = self.root / 'bad.yaml'
        path.write_text('private-fixture-value: 1\nprivate-fixture-value: 2\n', encoding='utf-8')
        result = self.cli(HERE / 'check.py', 'config', path, '--data-dir', self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn('DUPLICATE_YAML_KEY', result.stdout)
        self.assertNotIn('private-fixture-value', result.stdout + result.stderr)

    def test_standalone_copy_cli_and_read_only_input(self):
        folder = self.root / 'standalone'
        folder.mkdir()
        for name in ('check.py', 'metaclash_checks.py'):
            shutil.copy2(HERE / name, folder / name)
        path = self.root / 'candidate.yaml'
        self.write(path, self.data)
        before = path.read_bytes()
        result = self.cli(folder / 'check.py', 'config', path, '--data-dir', self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual({p.name for p in folder.iterdir()}, {'check.py', 'metaclash_checks.py'})

    def state_report(self, state=None, provider=None):
        return checks.check_state({'meta': True}, {'mode': 'rule'},
                                  {'proxies': state or runtime()},
                                  {'providers': {'sample': provider or {'ruleCount': 2}}})

    def test_runtime_baseline_passes(self):
        self.assertTrue(self.state_report()['passed'])

    def test_runtime_direct_catchall_and_dead_nodes_reported(self):
        state = runtime()
        state[checks.FISH]['now'] = 'DIRECT'
        state['Japan NF']['alive'] = False
        report = self.state_report(state)
        self.assertFalse(report['passed'])
        self.assertFalse(report['checks']['catchall_manual'])
        self.assertEqual(report['healthy_ai'], 0)

    def test_runtime_empty_provider_reported(self):
        self.assertFalse(self.state_report(provider={'ruleCount': 0})['passed'])

    def test_runtime_builtin_sentinel_is_zero_real_nodes(self):
        state = runtime()
        state[checks.NF_AUTO]['all'] = ['REJECT']
        self.assertEqual(self.state_report(state)['eligible_netflix'], 0)
        self.assertFalse(self.state_report(state)['passed'])

    def test_runtime_cycle_rejected(self):
        state = runtime()
        state[checks.MANUAL]['all'] = [checks.FISH]
        with self.assertRaisesRegex(checks.CheckError, 'CYCLIC_RUNTIME_GROUP'):
            self.state_report(state)

    def start_server(self, redirect=False):
        requests = []
        data = self.data
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                requests.append((self.command, parsed.path, self.headers.get('Authorization')))
                if redirect:
                    self.send_response(302)
                    self.send_header('Location', '/redirect-target')
                    self.end_headers()
                    return
                payload = {'/version': {'meta': True}, '/configs': {'mode': 'rule'},
                           '/proxies': {'proxies': runtime()},
                           '/providers/rules': {'providers': {'sample': {'ruleCount': 2}}},
                           '/nodes': {'proxies': data['proxies']}, '/sub': data}[parsed.path]
                if parsed.path in ('/sub', '/nodes'):
                    if parsed.path == '/sub':
                        query = urllib.parse.parse_qs(parsed.query)
                        assert query['target'] == ['clash'] and query['expand'] == ['false']
                        assert query['url'] == ['https://example.com/synthetic-subscription']
                    body = yaml.safe_dump(payload, allow_unicode=True).encode()
                else:
                    body = json.dumps(payload).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:' + str(server.server_port), requests

    def test_live_synthetic_controller_uses_four_gets_and_env_auth(self):
        base, requests = self.start_server()
        env = dict(os.environ, MIHOMO_API_SECRET='synthetic-fixture-secret')
        result = self.cli(HERE / 'check.py', 'state', '--controller', base, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([r[1] for r in requests], ['/version', '/configs', '/proxies', '/providers/rules'])
        self.assertTrue(all(r[0] == 'GET' and r[2] == 'Bearer synthetic-fixture-secret' for r in requests))
        self.assertNotIn('synthetic-fixture-secret', result.stdout + result.stderr)

    def test_controller_redirect_is_refused(self):
        base, requests = self.start_server(redirect=True)
        with self.assertRaisesRegex(checks.CheckError, 'CONTROLLER_REDIRECT_REFUSED'):
            checks.read_controller(base)
        self.assertEqual(len(requests), 1)

    def test_conversion_cli_writes_new_candidate_and_preserves_existing(self):
        base, requests = self.start_server()
        output = self.root / 'converted.yaml'
        env = dict(os.environ, METACLASH_SUBSCRIPTION='https://example.com/synthetic-subscription')
        args = ('convert', '--backend', base + '/sub', '--output', str(output))
        result = self.cli(HERE / 'check.py', *args, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.check()['groups'], checks.check_config(checks.load(output), self.root)['groups'])
        before = output.read_bytes()
        result = self.cli(HERE / 'check.py', *args, env=env)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(len(requests), 1)
        self.assertNotIn('synthetic-subscription', result.stdout + result.stderr)

    def test_urls_with_embedded_credentials_rejected_before_network(self):
        with self.assertRaisesRegex(checks.CheckError, 'INVALID_CONTROLLER_URL'):
            checks.read_controller('http://' + 'user:EXAMPLE_VALUE' + '@127.0.0.1:1')

    def test_fetch_missing_caches_then_config_and_preserve_existing(self):
        base, requests = self.start_server()
        data = candidate()
        data.pop('proxies')
        data['proxy-providers'] = {'fixture': {'type': 'http', 'path': 'providers/nodes.yaml', 'url': base + '/nodes'}}
        path = self.root / 'candidate.yaml'
        self.write(path, data)
        args = ('config', str(path), '--data-dir', str(self.root / 'data'))
        result = self.cli(HERE / 'check.py', *args)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(requests, [])
        result = self.cli(HERE / 'check.py', *args, '--fetch-providers')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['downloaded_proxy_caches'], 1)
        result = self.cli(HERE / 'check.py', *args, '--fetch-providers')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(requests), 1)

    def test_fetch_rejects_escaping_paths_before_network(self):
        self.data['proxy-providers'] = {'bad': {'type': 'http', 'path': '../outside.yaml',
                                              'url': 'http://127.0.0.1:1'}}
        with self.assertRaisesRegex(checks.CheckError, 'CACHE_PATH_MUST_BE_RELATIVE'):
            checks.fetch_missing_proxy_caches(self.data, self.root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
