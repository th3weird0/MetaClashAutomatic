"""Convert subscriptions, check YAML and read daily state; see README.md."""
import argparse
import getpass
import json
import os
import sys

sys.dont_write_bytecode = True
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from metaclash_checks import (
    CheckError, check_config, check_state, convert_subscription,
    fetch_missing_proxy_caches, load, read_controller,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    config = commands.add_parser('config', help='Check a converted YAML and existing provider caches')
    config.add_argument('input', help='Converted Mihomo YAML file')
    config.add_argument('--data-dir', required=True, help='Mihomo home directory for relative provider paths')
    config.add_argument('--fetch-providers', action='store_true', help='Download missing HTTP node caches into data-dir')
    state = commands.add_parser('state', help='Read four GET endpoints on a running controller')
    state.add_argument('--controller', required=True, help='http(s)://host:port')
    convert = commands.add_parser('convert', help='Download a staged YAML from your running converter')
    convert.add_argument('--backend', required=True, help='Trusted converter endpoint ending with /sub')
    convert.add_argument('--template', default='https://raw.githubusercontent.com/th3weird0/MetaClashAutomatic/main/MetaFullauto_Full.ini')
    convert.add_argument('--output', required=True, help='New staged YAML file')
    args = parser.parse_args()
    scope = {'config': 'config-structure', 'state': 'running-controller-state',
             'convert': 'conversion-download'}[args.command]
    try:
        if args.command == 'config':
            data = load(args.input)
            fetched = fetch_missing_proxy_caches(data, args.data_dir) if args.fetch_providers else 0
            report = {**check_config(data, args.data_dir), 'downloaded_proxy_caches': fetched}
        elif args.command == 'state':
            report = check_state(*read_controller(args.controller))
        else:
            subscription = os.environ.get('METACLASH_SUBSCRIPTION') or getpass.getpass('Subscription URL (hidden): ')
            report = convert_subscription(args.backend, subscription, args.template, args.output)
    except CheckError as error:
        report = {'passed': False, 'scope': scope, 'error': str(error), **error.details}
    except Exception:
        # YAML/parser/network exceptions may contain credentials or private node names.
        report = {'passed': False, 'scope': scope, 'error': 'INPUT_OR_CONNECTION_ERROR'}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
