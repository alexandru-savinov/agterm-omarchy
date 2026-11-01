#!/usr/bin/env python3
"""Bounded, offline assignment ledger. Records coordination; never launches workers."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

MAX_TASKS = 500
MAX_WORKERS = 3
MAX_EVENTS = 10000
MAX_BYTES = 10 * 1024 * 1024
STATES = ('queued', 'claimed', 'running', 'blocked', 'completed', 'rejected')
SPEC = {'id', 'title', 'source', 'paths', 'depends_on', 'max_attempts'}
RUNTIME = {'state', 'attempt', 'lease', 'completion', 'reason'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def text(value, limit=1000):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 for c in value)


def github_url(value, repository, kind='source'):
    prefix = 'https://github.com/' + repository + '/'
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    suffix = value[len(prefix):]
    patterns = {'assignment': r'issues/[1-9][0-9]*(?:#issuecomment-[1-9][0-9]*)?',
                'source': r'issues/[1-9][0-9]*#issuecomment-[1-9][0-9]*',
                'evidence': (r'(?:commit/[0-9a-f]{40}|pull/[1-9][0-9]*#pullrequestreview-[1-9][0-9]*|'
                             r'actions/runs/[1-9][0-9]*(?:/(?:job|artifacts)/[1-9][0-9]*)?|'
                             r'issues/[1-9][0-9]*#issuecomment-[1-9][0-9]*)')}
    return re.fullmatch(patterns[kind], suffix) is not None


def actor(value):
    require(isinstance(value, dict) and set(value) == {'account', 'agent', 'session'}, 'invalid actor fields')
    require(all(isinstance(v, str) for v in value.values()), 'actor fields must be strings')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', str(value['account'])), 'invalid account')
    require(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', str(value['agent'])), 'invalid agent')
    require(re.fullmatch(r'[a-z0-9]{4,16}', str(value['session'])), 'invalid session')
    return dict(account=value['account'].lower(), agent=value['agent'].lower(), session=value['session'])


def overlap(left, right):
    return any(a == b or a.startswith(b + '/') or b.startswith(a + '/')
               for a in (p.casefold() for p in left) for b in (p.casefold() for p in right))


def validate_plan(plan):
    require(isinstance(plan, dict) and set(plan) == {'schema', 'repository', 'requested', 'capacity', 'assignments'},
            'invalid plan fields')
    require(plan['schema'] == 'agterm-wave-plan/1', 'unsupported plan schema')
    require(isinstance(plan['repository'], str) and re.fullmatch(
        r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', plan['repository']), 'invalid repository')
    require(integer(plan['requested'], 1, MAX_TASKS), 'requested must be 1..500')
    require(integer(plan['capacity'], 1, MAX_WORKERS), 'capacity must be 1..3; records do not grant compute')
    specs = plan['assignments']
    require(isinstance(specs, list) and len(specs) <= plan['requested'], 'too many assignments')
    ids = set()
    for task in specs:
        require(isinstance(task, dict) and set(task) == SPEC, 'invalid assignment fields')
        require(isinstance(task['id'], str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', task['id']), 'invalid task id')
        require(task['id'] not in ids, 'duplicate task id')
        ids.add(task['id'])
        require(text(task['title'], 300), 'invalid title')
        require(github_url(task['source'], plan['repository'], 'assignment'), 'invalid assignment source')
        require(integer(task['max_attempts'], 1, 10), 'max_attempts must be 1..10')
        paths = task['paths']
        require(isinstance(paths, list) and len(paths) <= 30, 'invalid paths')
        for path in paths:
            require(isinstance(path, str) and re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', path)
                    and all(part not in ('.', '..') for part in path.split('/')), 'paths must be literal repo-relative names')
        require(len(set(p.casefold() for p in paths)) == len(paths), 'duplicate owned path')
        require(isinstance(task['depends_on'], list) and all(isinstance(d, str) for d in task['depends_on'])
                and len(set(task['depends_on'])) == len(task['depends_on']), 'invalid dependencies')
    graph = {t['id']: t['depends_on'] for t in specs}
    done, visiting = set(), set()

    def visit(task_id):
        require(task_id in graph, 'unknown dependency')
        require(task_id not in visiting, 'dependency cycle')
        if task_id in done:
            return
        visiting.add(task_id)
        for dependency in graph[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        done.add(task_id)
    for task_id in graph:
        visit(task_id)


def initialize(plan):
    validate_plan(plan)
    return dict(schema='agterm-wave-ledger/1', repository=plan['repository'], requested=plan['requested'],
                capacity=plan['capacity'], revision=0, events=[], assignments={
                    task['id']: dict(deepcopy(task), state='queued', attempt=0, lease=None, completion=None, reason=None)
                    for task in plan['assignments']})


def validate_ledger(queue):
    require(isinstance(queue, dict) and set(queue) == {
        'schema', 'repository', 'requested', 'capacity', 'revision', 'events', 'assignments'}, 'invalid ledger fields')
    require(queue['schema'] == 'agterm-wave-ledger/1' and isinstance(queue['assignments'], dict), 'invalid ledger schema')
    records = queue['assignments']
    require(all(isinstance(t, dict) and set(t) == SPEC | RUNTIME and t['id'] == key
                for key, t in records.items()), 'invalid task record')
    validate_plan(dict(schema='agterm-wave-plan/1', repository=queue['repository'], requested=queue['requested'],
                       capacity=queue['capacity'], assignments=[{k: t[k] for k in SPEC} for t in records.values()]))
    require(isinstance(queue['events'], list) and len(queue['events']) <= MAX_EVENTS, 'event log exceeds bound')
    require(type(queue['revision']) is int and queue['revision'] == len(queue['events']), 'invalid revision')
    ids = []
    for event in queue['events']:
        require(isinstance(event, dict) and set(event) == {'request', 'at'} and isinstance(event['request'], dict)
                and integer(event['at'], 0, 2**53) and text(event['request'].get('event_id'), 100), 'invalid stored event')
        ids.append(event['request']['event_id'])
    require(len(set(ids)) == len(ids), 'duplicate event id')
    reservations = []
    for task in records.values():
        require(task['state'] in STATES and integer(task['attempt'], 0, task['max_attempts']), 'invalid task state/attempt')
        require(task['reason'] is None or text(task['reason']), 'invalid reason')
        lease = task['lease']
        require(task['state'] not in ('claimed', 'running') or lease is not None, 'active task has no lease')
        require(task['state'] not in ('queued', 'completed', 'rejected') or lease is None, 'terminal/queued task holds lease')
        if lease is not None:
            require(task['attempt'] > 0, 'reservation requires an attempt')
            require(isinstance(lease, dict) and set(lease) == {'owner', 'token', 'expires_at'}, 'invalid lease')
            require(actor(lease['owner']) == lease['owner'] and text(lease['token'], 100)
                    and integer(lease['expires_at'], 0, 2**53), 'invalid lease identity/deadline')
            for other in reservations:
                require(not overlap(task['paths'], other['paths']), 'conflicting reserved paths')
                require(lease['owner'] != other['lease']['owner'], 'executor holds multiple reservations')
                require((lease['owner']['account'], lease['owner']['agent']) !=
                        (other['lease']['owner']['account'], other['lease']['owner']['agent']), 'agent session collision')
            reservations.append(task)
        require((task['completion'] is not None) == (task['state'] == 'completed'), 'invalid completion state')
        if task['completion'] is not None:
            completed = task['completion']
            require(isinstance(completed, dict) and set(completed) == {'owner', 'token', 'evidence'}
                    and task['attempt'] > 0, 'invalid completion record')
            require(actor(completed['owner']) == completed['owner'] and text(completed['token'], 100),
                    'invalid completion identity')
            evidence(completed['evidence'], queue['repository'])
    require(len(reservations) <= queue['capacity'], 'worker capacity exceeded')


def held(queue):
    return [task for task in queue['assignments'].values() if task['lease'] is not None]


def claim_blockers(queue, task):
    reasons = []
    if any(queue['assignments'][d]['state'] != 'completed' for d in task['depends_on']):
        reasons.append('dependencies')
    if len(held(queue)) >= queue['capacity']:
        reasons.append('capacity')
    if any(overlap(task['paths'], other['paths']) for other in held(queue)):
        reasons.append('ownership')
    return reasons


def evidence(values, repository):
    require(isinstance(values, list) and 1 <= len(values) <= 20
            and all(github_url(v, repository, 'evidence') for v in values)
            and len(set(values)) == len(values), 'exact GitHub evidence links required')


def transition(queue, request, expected_revision, now):
    """Return a new ledger; an identical event retry returns the original without another transition."""
    validate_ledger(queue)
    require(integer(now, 0, 2**53), 'invalid event time')
    require(isinstance(request, dict), 'event must be an object')
    common = {'event_id', 'operation', 'task', 'actor', 'source'}
    options = {'claim': {'lease_seconds'}, 'start': {'token'}, 'renew': {'token', 'lease_seconds'},
               'block': {'token', 'reason'}, 'complete': {'token', 'evidence'},
               'retry': {'confirmed_stopped', 'reason'}, 'reject': {'confirmed_stopped', 'reason'}}
    operation = request.get('operation')
    require(isinstance(operation, str) and operation in options and set(request) == common | options[operation],
            'invalid event fields/operation')
    require(text(request['event_id'], 100), 'invalid event id')
    who = actor(request['actor'])
    require(github_url(request['source'], queue['repository']), 'state change needs a GitHub issue-comment source')
    for prior in queue['events']:
        if prior['request']['event_id'] == request['event_id']:
            require(prior['request'] == request, 'event id reused with different content')
            return queue
    require(type(expected_revision) is int and expected_revision == queue['revision'], 'stale revision; reload the published ledger')
    require(len(queue['events']) < MAX_EVENTS, 'event log full; archive this bounded wave before continuing')
    require(isinstance(request['task'], str) and request['task'] in queue['assignments'], 'unknown task')
    result = deepcopy(queue)
    task = result['assignments'][request['task']]
    lease = task['lease']
    if operation == 'claim':
        require(task['state'] == 'queued', 'task is not queued')
        require(not claim_blockers(result, task), 'claim blocked: ' + ', '.join(claim_blockers(result, task)))
        for other in held(result):
            owner = other['lease']['owner']
            require((owner['account'], owner['agent']) != (who['account'], who['agent']), 'agent already reserved or session collision')
        require(integer(request['lease_seconds'], 60, 14400), 'lease must be 60..14400 seconds')
        require(task['attempt'] < task['max_attempts'], 'retry limit reached')
        task.update(state='claimed', attempt=task['attempt'] + 1, reason=None,
                    lease=dict(owner=who, token=uuid.uuid4().hex, expires_at=now + request['lease_seconds']))
    elif operation in ('retry', 'reject'):
        require(task['state'] not in ('completed', 'rejected'), 'terminal task cannot be reopened')
        require(text(request['reason']) and type(request['confirmed_stopped']) is bool, 'reason and stop acknowledgement required')
        require(not lease or request['confirmed_stopped'], 'lease expiry is not proof of worker stop')
        if operation == 'retry':
            require(task['state'] in ('claimed', 'running', 'blocked'), 'only reserved/blocked tasks can retry')
            require(task['attempt'] < task['max_attempts'], 'retry limit reached; record rejection instead')
        task.update(state='queued' if operation == 'retry' else 'rejected', lease=None, reason=request['reason'])
    else:
        # Completion retries with a new event ID are harmless, but cannot change the accepted evidence.
        if operation == 'complete' and task['state'] == 'completed':
            require(task['completion'] == dict(owner=who, token=request['token'], evidence=request['evidence']),
                    'completion conflicts with the accepted record')
        else:
            require(lease is not None and lease['owner'] == who and lease['token'] == request['token'], 'stale or foreign lease')
            require(operation == 'block' or lease['expires_at'] > now, 'lease expired; coordinate stopped-worker retry')
            if operation == 'start':
                require(task['state'] == 'claimed', 'start requires claimed state')
                task['state'] = 'running'
            elif operation == 'renew':
                require(integer(request['lease_seconds'], 60, 14400), 'lease must be 60..14400 seconds')
                lease['expires_at'] = now + request['lease_seconds']
            elif operation == 'block':
                require(text(request['reason']), 'blocked reason required')
                task.update(state='blocked', reason=request['reason'])
            elif operation == 'complete':
                require(task['state'] == 'running', 'completion requires running state')
                evidence(request['evidence'], queue['repository'])
                task.update(state='completed', lease=None,
                            completion=dict(owner=who, token=request['token'], evidence=request['evidence']))
    result['events'].append(dict(request=deepcopy(request), at=now))
    result['revision'] += 1
    validate_ledger(result)
    return result


def summary(queue, now, limit=20):
    validate_ledger(queue)
    require(integer(limit, 1, 50), 'snapshot limit must be 1..50')
    tasks = list(queue['assignments'].values())
    eligible = [t['id'] for t in tasks if t['state'] == 'queued' and not claim_blockers(queue, t)]
    expired = [t['id'] for t in held(queue) if t['lease']['expires_at'] <= now]
    return dict(revision=queue['revision'], requested=queue['requested'], assigned=len(tasks),
                unscoped_requested=queue['requested'] - len(tasks), capacity=queue['capacity'],
                reserved_slots=len(held(queue)), reported_states={s: sum(t['state'] == s for t in tasks) for s in STATES},
                eligible_count=len(eligible), eligible=eligible[:limit], expired_lease_count=len(expired),
                expired_leases=expired[:limit], observation='Recorded coordination only; not worker liveness or additional compute.')


def dry_run(plan, limit=20):
    """Plan batches without actors, leases, writes, GitHub posts or executions."""
    queue = initialize(plan)
    pending = list(queue['assignments'].values())
    done, batches = set(), []
    while pending:
        batch = []
        for task in pending:
            if (set(task['depends_on']) <= done and len(batch) < queue['capacity']
                    and not any(overlap(task['paths'], t['paths']) for t in batch)):
                batch.append(task)
        require(batch, 'plan cannot make progress')
        done.update(t['id'] for t in batch)
        pending = [t for t in pending if t['id'] not in done]
        batches.append([t['id'] for t in batch])
    return dict(dry_run=True, execution_performed=False, summary=summary(queue, 0, limit),
                planned_batch_count=len(batches), planned_batches=batches[:limit], truncated=len(batches) > limit)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def read_json(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES, 'JSON input exceeds 10 MiB')
    return json.loads(data, object_pairs_hook=unique_object)


@contextmanager
def locked(path):
    with Path(str(path) + '.lock').open('a') as lock:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                require(time.monotonic() < deadline, 'ledger writer busy; retry after coordination')
                time.sleep(0.05)
        yield


def write_atomic(path, data):
    path = Path(path)
    serialized = json.dumps(data, indent=2) + '\n'
    require(len(serialized.encode('utf-8')) <= MAX_BYTES, 'ledger exceeds 10 MiB; archive this bounded wave')
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.' + path.name, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init'); init.add_argument('plan'); init.add_argument('ledger')
    apply = commands.add_parser('apply'); apply.add_argument('ledger'); apply.add_argument('event')
    apply.add_argument('--expected-revision', type=int, required=True)
    for name in ('summary', 'dry-run'):
        command = commands.add_parser(name); command.add_argument('input'); command.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    now = int(time.time())
    try:
        if args.command == 'dry-run':
            output = dry_run(read_json(args.input), args.limit)
        elif args.command == 'summary':
            output = summary(read_json(args.input), now, args.limit)
        else:
            path = Path(args.ledger).resolve()
            with locked(path):
                if args.command == 'init':
                    require(not path.exists(), 'ledger already exists')
                    queue = initialize(read_json(args.plan))
                    output = summary(queue, now)
                else:
                    request = read_json(args.event)
                    queue = transition(read_json(path), request, args.expected_revision, now)
                    output = dict(summary=summary(queue, now), assignment=queue['assignments'][request['task']])
                write_atomic(path, queue)
        print(json.dumps(output, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f'Wave queue rejected: {error}\n')


if __name__ == '__main__':
    main()
