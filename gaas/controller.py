"""GameRoom reconciliation loop. Read-only unless --apply is explicitly set."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
import uuid

from gaas.gateway import Gateway
from gaas.kube import Kube
from gaas.lifecycle import Decision, FINALIZER, decide, request_id, shared_gateway_required
from gaas.runtime import Zomboid, image_valid

RESOURCE = 'gamerooms.gaming.bingops.com'


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    return value.isoformat(timespec='microseconds').replace('+00:00', 'Z')


class Leader:
    def __init__(self, kube, namespace):
        self.kube, self.namespace = kube, namespace
        self.identity = str(uuid.uuid4())

    def acquire(self):
        lease = self.kube.get('lease', 'gaas-controller', self.namespace)
        current = now()
        if lease:
            spec = lease['spec']
            renewed = datetime.fromisoformat(spec.get('renewTime', '1970-01-01T00:00:00Z').replace('Z', '+00:00'))
            if spec.get('holderIdentity') != self.identity and renewed + timedelta(seconds=spec.get('leaseDurationSeconds', 30)) > current:
                return False
            lease['spec'].update(holderIdentity=self.identity, renewTime=timestamp(current), leaseDurationSeconds=30)
            self.kube.replace(lease)  # resourceVersion rejects competing leaders
        else:
            self.kube.create({'apiVersion': 'coordination.k8s.io/v1', 'kind': 'Lease',
                              'metadata': {'name': 'gaas-controller', 'namespace': self.namespace},
                              'spec': {'holderIdentity': self.identity, 'leaseDurationSeconds': 30,
                                       'renewTime': timestamp(current)}})
        return True


def config(kube, namespace, name, key):
    value = kube.get('configmap', name, namespace)
    if value is None:
        raise ValueError('Required operator configuration is missing: ' + name)
    return json.loads(value['data'][key])


def duplicates(rooms):
    seen, invalid = {}, set()
    for room in rooms:
        meta, spec = room['metadata'], room['spec']
        allocations = [('world', meta['namespace'], spec['world']['id'])]
        for protocol, ports in [('udp', spec.get('network', {}).get('publicPorts', {})),
                                ('nodeport', spec.get('network', {}).get('nodePorts', {}))]:
            for port in ports.values():
                allocations.append((protocol, spec.get('gatewayRef', 'shared') if protocol == 'udp' else 'cluster', port))
        for key in allocations:
            if key in seen:
                invalid.update((meta['uid'], seen[key]))
            seen[key] = meta['uid']
    return invalid


class Controller:
    def __init__(self, kube, namespace, chart, namespaces, apply=False):
        self.kube, self.namespace, self.namespaces, self.apply = kube, namespace, set(namespaces), apply
        self.runtime = Zomboid(kube, chart)
        self.leader = Leader(kube, namespace)

    def tick(self):
        rooms = self.kube.listing(RESOURCE)  # failures abort; never assume zero consumers
        releases = config(self.kube, self.namespace, 'gaas-release-catalog', 'releases.json')
        settings = config(self.kube, self.namespace, 'gaas-gateway', 'config.json')
        gateway = Gateway(self.kube, self.namespace, settings)
        managed = [room for room in rooms if room['metadata']['namespace'] in self.namespaces
                   and room['spec'].get('gatewayRef', 'shared') == settings.get('name', 'shared')]
        invalid = duplicates(rooms)
        decisions, reports = [], []
        if self.apply and not self.leader.acquire():
            return [{'phase': 'Standby'}]
        for room in managed:
            meta, spec = room['metadata'], room['spec']
            try:
                if self.apply and not self.leader.acquire():
                    raise RuntimeError('Controller leadership lost')
                if meta['uid'] in invalid:
                    raise ValueError('World or port allocation is shared by multiple GameRooms')
                release = releases.get(spec['version'])
                active = room.get('status', {}).get('activeRelease')
                stopping = meta.get('deletionTimestamp') or spec.get('state') == 'Suspended'
                if not stopping:
                    forwards = settings.get('variables', {}).get('forwards', {})
                    network = spec.get('network', {})
                    for port in ('game', 'direct'):
                        matching = [forward for forward in forwards.values() if forward['protocol'] == 'udp'
                                    and forward['listen_port'] == network.get('publicPorts', {}).get(port)
                                    and forward['target_port'] == network.get('nodePorts', {}).get(port)
                                    and forward['target_ip'] == network.get('targetIP')]
                        if not matching:
                            raise ValueError('Public route is not declared in the shared gateway configuration')
                if not stopping and image_valid(release) and active:
                    if int(release['steamBuildId']) < int(active['steamBuildId']):
                        raise ValueError('Downgrading requires an explicit restored-world workflow')
                    if room.get('status', {}).get('version') == spec['version'] and active != release:
                        raise ValueError('Existing release catalogue keys are immutable')
                if self.apply and FINALIZER not in meta.get('finalizers', []):
                    if meta.get('deletionTimestamp'):
                        raise ValueError('Deleting room has no world-preservation finalizer')
                    self.kube.patch(RESOURCE, meta['name'], meta['namespace'], {'metadata': {
                        'resourceVersion': meta['resourceVersion'], 'finalizers': meta.get('finalizers', []) + [FINALIZER]}})
                    reports.append({'room': meta['name'], 'phase': 'ProtectingWorld'})
                    continue
                if self.apply and not stopping and image_valid(release):
                    self.runtime.ensure(room, release)
                purpose = 'delete' if meta.get('deletionTimestamp') else 'suspend' if stopping else 'release:' + spec['version']
                identity = request_id(room, purpose)
                observed = self.runtime.observe(room, releases, identity, gateway.ready)
                decision = decide(room, observed, releases)
                if self.apply:
                    if decision.operation:
                        self.runtime.perform(room, observed, decision, releases)
                    status = dict(room.get('status', {}), observedGeneration=meta['generation'], phase=decision.phase,
                                  worldReady=observed.get('worldReady', False), version=observed.get('version'),
                                  backup=observed.get('backup', {}), gatewayRequired=decision.needs_gateway,
                                  everStarted=room.get('status', {}).get('everStarted', False) or observed.get('podExists', False))
                    if observed.get('version') in releases:
                        status['activeRelease'] = releases[observed['version']]
                    if status != room.get('status', {}):
                        self.kube.patch(RESOURCE, meta['name'], meta['namespace'], {'status': status}, status=True)
                    if decision.phase == 'Finalized':
                        # Children are GC-owned; retained world PVC has no ownerRef.
                        self.kube.patch(RESOURCE, meta['name'], meta['namespace'], {'metadata': {
                            'finalizers': [f for f in meta.get('finalizers', []) if f != FINALIZER]}})
                decisions.append(decision)
                reports.append({'room': meta['name'], 'namespace': meta['namespace'], 'phase': decision.phase,
                                'operation': decision.operation, 'needsGateway': decision.needs_gateway})
            except (ValueError, RuntimeError, KeyError) as error:
                # Conservative retention on any incomplete room observation.
                decisions.append(Decision('Blocked', None, '', True))
                reports.append({'room': meta['name'], 'phase': 'Blocked', 'reason': str(error)})
        # Finalizer-only room processing is incomplete this tick. Do not infer
        # a zero-consumer gateway transition until every room was observed.
        if len(decisions) != len(managed):
            return reports
        required = shared_gateway_required(decisions, inventory_complete=True)
        if self.apply:
            if not self.leader.acquire():
                raise RuntimeError('Controller leadership lost')
            gateway.reconcile(required)
        reports.append({'gateway': settings.get('name', 'shared'), 'required': required, 'provisioned': gateway.ready})
        return reports


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--context')
    parser.add_argument('--namespace', default='pz-server')
    parser.add_argument('--game-namespace', action='append')
    parser.add_argument('--chart', type=Path, default=Path(__file__).resolve().parents[1] / 'charts/pz-server')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=5)
    args = parser.parse_args()
    controller = Controller(Kube(args.context), args.namespace, args.chart,
                            args.game_namespace or [args.namespace], apply=args.apply)
    while True:
        try:
            print(json.dumps(controller.tick()), flush=True)
        except (RuntimeError, ValueError, KeyError):
            print(json.dumps({'phase': 'Blocked', 'reason': 'Reconciliation incomplete; no gateway removal authorized'}), flush=True)
            if args.once:
                raise SystemExit(1)
        if args.once:
            return
        time.sleep(max(1, args.interval))


if __name__ == '__main__':
    main()
