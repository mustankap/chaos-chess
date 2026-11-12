#!/usr/bin/env python3
"""Preview by default. Provision a fixed A1 VM via an authenticated OCI CLI."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess


SHAPE = 'VM.Standard.A1.Flex'


def cli(args, *command):
    region = ['--region', args.region] if args.region else []
    result = subprocess.run(
        ['oci', *region, '--output', 'json', '--no-retry', *command],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)['data']


def inventory(args):
    """Fail closed if existing resources make this fresh-account plan uncertain."""
    regions = cli(args, 'iam', 'region-subscription', 'list', '--tenancy-id', args.tenancy)
    if not args.region:
        args.region = next(r['region-name'] for r in regions if r['is-home-region'])
    if not any(r['is-home-region'] and r['region-name'] == args.region for r in regions):
        raise SystemExit('Stop: Always Free compute must use the tenancy home region.')
    ads = cli(args, 'iam', 'availability-domain', 'list', '--compartment-id', args.tenancy)
    compartments = cli(args, 'iam', 'compartment', 'list', '--compartment-id', args.tenancy,
                       '--compartment-id-in-subtree', 'true', '--access-level', 'ANY', '--all')
    ids = [args.tenancy] + [c['id'] for c in compartments if c['lifecycle-state'] == 'ACTIVE']
    for compartment in ids:
        queries = [
            ('compute', 'instance', 'list'),
            ('bv', 'volume', 'list'),
        ]
        for query in queries:
            resources = cli(args, *query, '--compartment-id', compartment, '--all')
            if any(r['lifecycle-state'] != 'TERMINATED' for r in resources):
                raise SystemExit('Stop: existing compute/storage requires a manual free-allowance review.')
        for ad in ads:
            volumes = cli(args, 'bv', 'boot-volume', 'list', '--compartment-id', compartment,
                          '--availability-domain', ad['name'], '--all')
            if any(v['lifecycle-state'] != 'TERMINATED' for v in volumes):
                raise SystemExit('Stop: existing boot volumes require a manual free-allowance review.')
    return ads


def provision(args):
    network = ipaddress.ip_network(args.ssh_cidr)
    if network.version != 4 or network.prefixlen != 32:
        raise SystemExit('SSH must be restricted to your single public IPv4 address (/32).')
    key = Path(args.ssh_public_key).expanduser().resolve()
    if not key.read_text().strip().startswith(('ssh-ed25519 ', 'ssh-rsa ', 'ecdsa-sha2-')):
        raise SystemExit('Provide an SSH PUBLIC key, never a private key.')
    state_path = Path('.oci-state.json')
    if state_path.exists():
        raise SystemExit('Stop: .oci-state.json already exists. Inspect it and OCI before any retry.')
    ads = inventory(args)
    ad = args.ad or ads[0]['name']
    if ad not in [item['name'] for item in ads]:
        raise SystemExit('Unknown availability domain for this tenancy/region.')
    images = cli(args, 'compute', 'image', 'list', '--compartment-id', args.tenancy,
                 '--operating-system', 'Canonical Ubuntu', '--operating-system-version', '24.04',
                 '--shape', SHAPE, '--sort-by', 'TIMECREATED', '--sort-order', 'DESC', '--all')
    images = [i for i in images if i.get('compartment-id') is None and i['lifecycle-state'] == 'AVAILABLE']
    if not images:
        raise SystemExit('No official Ubuntu 24.04 platform image for A1. No paid fallback.')
    image = images[0]
    plan = {'region': args.region, 'tenancy': args.tenancy, 'ad': ad, 'shape': SHAPE,
            'ocpus': 1, 'memory_gb': 6, 'boot_gb': 50, 'image': image['id'],
            'ssh_source': args.ssh_cidr, 'public_tcp_ports': [80, 443],
            'resources': 'one VCN, internet gateway, route table, security list, subnet, VM + boot disk'}
    print(json.dumps(plan, indent=2))
    if not args.apply:
        print('PREVIEW ONLY: no resources created. Pricing is not verified by the API inventory.')
        return
    if not args.confirm_free_account:
        raise SystemExit('Stop: verify Free Trial/Always Free (not PAYG) and current free eligibility first; '
                         'then use --confirm-free-account with --apply.')

    # Exclusive journal prevents a rerun after an uncertain API write from duplicating resources.
    state = {'plan': plan, 'created': {}, 'pending': None}
    fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(state, file, indent=2)

    def create(name, *command):
        state['pending'] = name
        state_path.write_text(json.dumps(state, indent=2))
        data = cli(args, *command, '--compartment-id', args.tenancy,
                   '--display-name', 'chaos-chess-' + name)
        state['created'][name] = data['id']
        state['pending'] = None
        state_path.write_text(json.dumps(state, indent=2))
        print(f'Created {name}: {data["id"]}', flush=True)
        return data['id']

    vcn = create('vcn', 'network', 'vcn', 'create', '--cidr-block', '10.42.0.0/16',
                 '--dns-label', 'chaos', '--wait-for-state', 'AVAILABLE')
    gateway = create('gateway', 'network', 'internet-gateway', 'create', '--vcn-id', vcn,
                     '--is-enabled', 'true', '--wait-for-state', 'AVAILABLE')
    routes = json.dumps([{'destination': '0.0.0.0/0', 'destinationType': 'CIDR_BLOCK',
                          'networkEntityId': gateway}])
    route = create('route', 'network', 'route-table', 'create', '--vcn-id', vcn,
                   '--route-rules', routes, '--wait-for-state', 'AVAILABLE')
    ingress = [{'source': source, 'protocol': '6', 'isStateless': False,
                'tcpOptions': {'destinationPortRange': {'min': port, 'max': port}}}
               for source, port in [(args.ssh_cidr, 22), ('0.0.0.0/0', 80), ('0.0.0.0/0', 443)]]
    ingress.append({'source': '0.0.0.0/0', 'protocol': '1', 'isStateless': False,
                    'icmpOptions': {'type': 3, 'code': 4}})
    security = create('security', 'network', 'security-list', 'create', '--vcn-id', vcn,
                      '--ingress-security-rules', json.dumps(ingress),
                      '--egress-security-rules', json.dumps([{'destination': '0.0.0.0/0',
                                                            'protocol': 'all', 'isStateless': False}]),
                      '--wait-for-state', 'AVAILABLE')
    subnet = create('subnet', 'network', 'subnet', 'create', '--vcn-id', vcn,
                    '--cidr-block', '10.42.1.0/24', '--dns-label', 'web',
                    '--route-table-id', route, '--security-list-ids', json.dumps([security]),
                    '--prohibit-public-ip-on-vnic', 'false', '--wait-for-state', 'AVAILABLE')
    instance = create('vm', 'compute', 'instance', 'launch', '--availability-domain', ad,
                      '--shape', SHAPE, '--shape-config', '{"ocpus":1,"memoryInGBs":6}',
                      '--image-id', image['id'], '--boot-volume-size-in-gbs', '50',
                      '--subnet-id', subnet, '--assign-public-ip', 'true',
                      '--ssh-authorized-keys-file', str(key),
                      '--user-data-file', str(Path(__file__).with_name('cloud-init.yaml').resolve()),
                      '--wait-for-state', 'RUNNING')
    vnics = cli(args, 'compute', 'instance', 'list-vnics', '--instance-id', instance)
    print('SSH: ssh ubuntu@' + vnics[0]['public-ip'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tenancy', required=True, help='Root tenancy OCID; requires administrator read access')
    parser.add_argument('--region', help='Home region; discovered automatically when omitted')
    parser.add_argument('--ad', help='Optional availability domain; defaults to first')
    parser.add_argument('--ssh-public-key', required=True)
    parser.add_argument('--ssh-cidr', required=True, help='Your public IPv4/32')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-free-account', action='store_true')
    args = parser.parse_args()
    try:
        provision(args)
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr + '\nStopped. Inspect .oci-state.json before retrying; no paid fallback.')


if __name__ == '__main__':
    main()
