#!/usr/bin/env python3
# Copyright 2026 Michael Still and contributors
"""Analyse one CI bundle for the 507 sufficient_idle_cpu picture."""
import collections
import datetime
import json
import os
import sys


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime('%H:%M:%S')


def parse_ts(s):
    return datetime.datetime.strptime(s[:23], '%Y-%m-%dT%H:%M:%S.%f').replace(
        tzinfo=datetime.UTC).timestamp()


def journal_rows(path):
    rows = []
    for line in open(path, errors='replace'):
        i = line.find('{')
        if i < 0:
            continue
        # journald prefixes the JSON with 'hostname {"logger_name"[pid]:' for some units,
        # mangling the first key. Repair the common form.
        body = line[i:].strip()
        if '"[' in body[:40] and ']: ' in body[:60]:
            k = body.find(']: ')
            body = '{"logger_name": ' + body[k + 3:]
        try:
            rows.append(json.loads(body))
        except Exception:
            continue
    return rows


def main(bdir):
    b = os.path.join(bdir, 'bundle')
    t0 = float(open(os.path.join(b, 'traces/headroom-start')).read().strip())
    lp = os.path.join(b, 'traces/headroom-label')
    label = open(lp).read().strip() if os.path.exists(lp) else '?'
    print('=' * 100)
    print(bdir, label, 'test step start', iso(t0))

    # Node names.
    names = {}
    for n in os.listdir(b):
        jp = os.path.join(b, n, '_commands/journalctl-sf-units')
        if not os.path.exists(jp):
            continue
        for line in open(jp, errors='replace'):
            i = line.find('"node": "')
            if i >= 0:
                names[line[i + 9:i + 45]] = n
                break

    def short(u):
        return names.get(u, u[:8]) if u else '-'

    # Series.
    samples = []
    for line in open(os.path.join(b, 'traces/headroom.jsonl')):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if 'resources' in r:
            samples.append(r)
    first_row = None
    for r in samples:
        if any(v.get('cpu_committed_row_present') for v in r['resources']['per_node'].values()):
            first_row = r['sampled_at']
            break
    print('samples', len(samples), 'first capacity row at +%.0fs (%s)' % (
        (first_row - t0) if first_row else -1, iso(first_row) if first_row else '-'))
    nodes = sorted(samples[0]['resources']['per_node'])
    for n in nodes:
        v = samples[0]['resources']['per_node'][n]
        print('  node %-8s schedulable %s hard_max %s' % (short(n), v.get('cpu_schedulable'), v.get('cpu_hard_max')))
    stats = {}
    for n in nodes:
        tot = full = over = meas_full = 0
        for r in samples:
            v = r['resources']['per_node'].get(n, {})
            if v.get('cpu_limit') is None:
                continue
            tot += 1
            m, c = v.get('cpu_measured') or 0, v.get('cpu_committed') or 0
            if max(m, c) >= v['cpu_limit']:
                full += 1
            if m >= v['cpu_limit']:
                meas_full += 1
            if c > (v.get('instances_total') or 0):
                over += 1
        stats[n] = (tot, full, meas_full, over)
        print('  %-8s ledgered samples %3d  at-ledger %3d (%2.0f%%)  measured-at-ledger %3d  '
              'committed>instances_total %3d' % (
                  short(n), tot, full, 100 * full / tot if tot else 0, meas_full, over))

    # Journal on primary (sf-api lives there).
    J = os.path.join(b, 'primary/_commands/journalctl-sf-units')
    rows = journal_rows(J)
    msgs = collections.Counter(r.get('message', '') for r in rows)
    print('journal json rows', len(rows))
    for m in ['instance placed without capacity guard', 'capacity counter clamped at zero',
              'placement recorded despite exceeding capacity guard',
              'no candidate admitted and some refused on demand alone, waiving demand guard',
              'schedule candidate refused by capacity guard',
              'schedule failed, every candidate refused by capacity guard',
              'schedule failed, insufficient resources', 'schedule forced candidates',
              'instance placed', 'instance placement released',
              'Scheduler capacity counters drifted, corrected', 'Scheduler capacity reconcile pass complete']:
        print('  %4d  %s' % (msgs.get(m, 0), m))

    # Reconcile passes.
    print('reconcile passes:')
    for r in rows:
        if r.get('message') == 'Scheduler capacity reconcile pass complete':
            t = parse_ts(r['ts'])
            print('  %s (+%5.0fs) nodes=%s added=%s drifted=%s drift_cpus=%s' % (
                iso(t), t - t0, r.get('nodes'), r.get('nodes_added'), r.get('drifted_nodes'), r.get('drift_cpus')))
        if r.get('message', '').startswith('No scheduler capacity rows exist'):
            t = parse_ts(r['ts'])
            print('  %s (+%5.0fs) FORCED reconcile (issue 4087 path)' % (iso(t), t - t0))
    drifts = [r for r in rows if r.get('message') == 'Scheduler capacity counters drifted, corrected']
    for r in drifts:
        print('  drift %s node=%s delta_cpus=%s' % (r['ts'][11:19], short(r.get('node')), r.get('delta_used_cpus')))

    # Denials.
    den = [r for r in rows if r.get('message') == 'schedule candidate refused by capacity guard']
    demand_only = 0
    for r in den:
        ex = r.get('extra', {})
        dims = [d for d in ex.get('dimensions', []) if d.get('exceeded')]
        if dims and all(d['dimension'] == 'demand' for d in dims):
            demand_only += 1
    print('guard denials %d, demand-only %d' % (len(den), demand_only))

    # Aborts.
    forced = {}
    inputs = {}
    for r in rows:
        if r.get('message') == 'schedule forced candidates':
            forced[r.get('instance')] = r.get('extra', {}).get('candidates')
        if r.get('message') == 'schedule inputs':
            inputs[r.get('instance')] = r.get('extra', {})
    placed = collections.defaultdict(list)  # node -> [(t, inst, +cpus)]
    for r in rows:
        m = r.get('message')
        if m == 'instance placed':
            ex = r['extra']
            placed[ex.get('node')].append((
                parse_ts(r['ts']), r.get('instance'), ex.get('cpus'), 'placed',
                ex.get('node_used_cpus'), ex.get('enforce')))
        elif m == 'instance placement released':
            ex = r['extra']
            placed[ex.get('node')].append((
                parse_ts(r['ts']), r.get('instance'), -(ex.get('cpus') or 0), 'released',
                ex.get('node_used_cpus'), None))
    print('ABORTS:')
    for r in rows:
        m = r.get('message', '')
        if not (m.startswith('schedule has no candidates at stage') and 'aborting' in m):
            continue
        t = parse_ts(r['ts'])
        inst = r.get('instance')
        ex = r.get('extra', {})
        ns = inputs.get(inst, {}).get('namespace')
        print('  %s (+%5.0fs, +%5.0fs after first row) inst %s ns %s stage: %s' % (
            iso(t), t - t0, (t - first_row) if first_row else -1, inst[:8] if inst else '-', ns, m))
        print('    forced candidates:', [short(c) for c in forced.get(inst)] if forced.get(inst) else None,
              'requested cpus', inputs.get(inst, {}).get('requested_cpus'))
        for n, d in ex.get('dropped', {}).items():
            print('    dropped %-8s cur=%s meas=%s comm=%s lim=%s row=%s reason=%s' % (
                short(n), d.get('current_cpus'), d.get('measured_cpus'), d.get('committed_cpus'),
                d.get('limit_cpus'), d.get('capacity_row_present'), d.get('reason')))
        # Ledger reconstruction on each dropped node from placed/released events.
        for n in ex.get('dropped', {}):
            live = {}
            for (pt, pi, pc, kind, used, enf) in sorted(placed.get(n, []), key=lambda x: x[0]):
                if pt > t:
                    break
                if kind == 'placed':
                    live[pi] = pc
                else:
                    live.pop(pi, None)
            print('    ledger reconstruction on %s from placed/released events: %d instances, %s vcpus: %s' % (
                short(n), len(live), sum(live.values()), [k[:8] for k in live]))
        # Cluster-wide at that moment from the series.
        near = min(samples, key=lambda s: abs(s['sampled_at'] - t))
        pn = near['resources']['per_node']
        print('    nearest sample %s: ' % iso(near['sampled_at']) + '  '.join(
            '%s meas/comm/lim/inst=%s/%s/%s/%s' % (
                short(n), v.get('cpu_measured'), v.get('cpu_committed'),
                v.get('cpu_limit'), v.get('instances_total'))
            for n, v in sorted(pn.items())))
        print('    cluster committed sum %s, instances_total sum %s, ledger sum %s' % (
            sum(v.get('cpu_committed') or 0 for v in pn.values()),
            sum(v.get('instances_total') or 0 for v in pn.values()),
            sum(v.get('cpu_limit') or 0 for v in pn.values())))

    # Distribution of requested cpus in placements.
    cpus = collections.Counter(r['extra'].get('cpus') for r in rows if r.get('message') == 'instance placed')
    print('placed instance cpus distribution', dict(cpus))
    # Unguarded placement window.
    ung = [parse_ts(r['ts']) for r in rows if r.get('message') == 'instance placed without capacity guard']
    if ung:
        print('unguarded placements: %d between +%.0fs and +%.0fs' % (len(ung), min(ung) - t0, max(ung) - t0))


if __name__ == '__main__':
    for d in sys.argv[1:]:
        main(d)
