"""Ticket E4: the ported E2 v10 draft reference tests, one pytest function per section of the draft `run_tests`.

Source: `evidence/e2/e2-v10-tools.py` `run_tests` (lines 1464-2154); the sections are cut at the draft's own `# ---` headings and the statements the later
sections share are module-level setup (hoisted unchanged, in the draft's order). Changes: `SystemExit` -> `AssertionError`; the burst-section list `expect` is
`expect_lat` (the draft reused the name for the helper function); `patched` patches the reference module's globals; example/schema/table files are not written
(see `test_g1e_frozen_artifacts.py`). The assertions are the draft's.
"""
from __future__ import annotations

import copy, hashlib, inspect, itertools, json, math, random, re, tempfile
from pathlib import Path

import jsonschema

from tests.cli_regression.support.g1e import reference
from tests.cli_regression.support.g1e.reference import *  # noqa: F401,F403  (the draft's flat namespace)

schema = build_schema()

base = make_doc()
def rejected(mutate, label, doc=base):
    d = clone(doc); mutate(d)
    v = document_ok(d)
    if not v.ok: print(f'  rejected ({label}): {v.kind}'); return
    raise AssertionError(f'NOT REJECTED: {label}')
R = lambda rid: (lambda d: d['rows'][rid])
io1 = 'stream|lat:io_ms|cell1'
def noisy_doc(noisy):
    """`base` with the given paired rows listed as noisy (registry order), every declared field refreshed."""
    d = clone(base); d['preregistration']['calibration']['noisyRows'] = [s['id'] for s in REG if s['id'] in set(noisy)]; recompute(d)
    return d
nonce = h('session-nonce')[:32]
peer_payloads = [line('win_begin', wid='i0') + b'x' * 1000 + b'\n' + line('win_end', wid='i0'),
                 line('win_begin', wid='i0') + b''.join(line('ack', text=INPUTS['io'].format(round=r, input=WORKLOAD['input'])) for r in range(30)) + line('win_end', wid='i0'),
                 line('win_begin', wid='b0r00') + b''.join(line('burst_line', kk='00', line=l, xs='x' * 300) for l in range(20)) + line('ack', text='during-burst-r00')
                 + line('done', kk='00') + line('win_end', wid='b0r00') + line('final', accepted=12345, digest='ab' * 32)]
pp = peer_payloads[2]
def expect(v, kind, frag, label):
    assert result_kind(v) == kind and frag in v[1], (label, result_kind(v), v)
BIND_IDS = {'peer_id': 'peer-0', 'session_id': 's-1', 'cutoff_id': 'cut-1'}; BIND = {**BIND_IDS, 'variant': 'L1', 'e1_sha256': E1_BOUND_SHA256}
def TB(pieces, unknown=None, **over): return {**BIND_IDS, 'in_flight_unknown': bool(pieces) if unknown is None else unknown, 'pieces': [list(x) for x in pieces], **over}
P = lambda a, b, data, after=False: {'a': a, 'b': b, 'data': data, 'after_exit': after}
AUTO = object()
def LD(pieces, peer, tb, k=None, record=AUTO):
    return loss_delivery_ok(binding={**BIND, 'variant': 'L1c' if k is not None else 'L1'}, peer=peer, pieces=pieces, renderer_record=b''.join(x['data'] for x in pieces) if record is AUTO else record, taken_batch=tb, cutoff_k=k)
data = b'0123456789' * 6; batch = [(0, 10), (10, 20), (20, 30), (30, 40)]
pre = [P(0, 10, data[0:10]), P(10, 20, data[10:20])]
REUSE = {'handle_kind': 'fd', 'freed_number': 7, 'replacement_number': 7, 'numbers_equal_harness_verified': True, 'e1_sha256': E1_BOUND_SHA256}
L1E = {**REUSE, 'precondition_retained_stream_closed_root_live': True, 'loss_step2_raw_close_for_session': False, 'unrelated_resource_open_and_usable_after_loss': True,
       'runtime_lost_exits_for_session': 0, 'identity_cleanup_proceeded': True, 't_freed_ms': 10.0, 't_replacement_opened_ms': 20.0, 't_sidecar_killed_ms': 30.0, 't_checked_ms': 40.0}
L1F = {**REUSE, 'precondition_finisher_suspended_in_emit_ignoring_first_cancel': True, 'precondition_loss_step2_closed_the_handle': True,
       'resumed_finisher_stopped_at_fence': True, 'resumed_finisher_raw_close': False, 'resumed_finisher_tty_eof': False, 'resumed_finisher_normal_exit': False,
       'unrelated_resource_open_and_usable_after_resume': True, 'runtime_lost_exits_for_session': 1, 'after_exit_bytes_only_from_taken_batch': True,
       't_sidecar_killed_ms': 10.0, 't_freed_ms': 20.0, 't_replacement_opened_ms': 30.0, 't_sink_released_ms': 40.0, 't_checked_ms': 50.0}
HA, HB = h('log-a'), h('log-b')
good = [{'t_ms': 0, 'dev': 1, 'ino': 7, 'size': 100, 'mtime_ns': 5, 'sha256': HA, 'regular': True}, {'t_ms': 300, 'dev': 1, 'ino': 7, 'size': 100, 'mtime_ns': 5, 'sha256': HA, 'regular': True}]
LF = lambda *a: log_final_check(*a, t_exit_ms=0.0, t_inventory_ms=0.0)
SEM_NEG = {n: [] for n in CHECKERS}
def why_of(v): return v['why'] if isinstance(v, dict) else v[1]
def sem_neg(name, label, v, frag, kind='invalid-semantic'):
    assert result_kind(v) == kind and frag in why_of(v), (name, label, result_kind(v), why_of(v))
    SEM_NEG[name].append(label)
G = reference.__dict__
def patched(name, value, fn):
    old = G[name]; G[name] = value
    try: return fn()
    finally: G[name] = old
BAD_HASH = h('not-the-frozen-artifact')
B9 = {'peer_id': 'peer-0', 'session_id': 's-1', 'cutoff_id': 'cut-1'}
tb9 = {**B9, 'in_flight_unknown': True, 'pieces': [[0, 1], [1, 2]]}
L1C = {**B9, 'variant': 'L1c', 'e1_sha256': E1_BOUND_SHA256}


def test_named_rejections_and_failed_correctness_rows():
    # --- named rejection / non-CONTINUE tests ---
    rejected(lambda d: d['rows'].pop(io1), 'missing row')
    rejected(lambda d: d['rows'].__setitem__('stream|lat:extra|cell1', d['rows'][io1]), 'extra row')
    rejected(lambda d: d['rows'][io1]['margin'].__setitem__('value', 5.0), 'edited margin')
    rejected(lambda d: d['rows'][io1]['pairs'][0].update(rust=999.0), 'pair value changed without counts (status stale)')
    rejected(lambda d: d['rows'][io1]['pairs'][0].update(valid=False), 'pair invalidated without counts')
    rejected(lambda d: d['rows'][io1].update(validPairs=9), 'status pass with 9 pairs')
    rejected(lambda d: d['rows'][io1].update(exceedCount=10), 'status pass with 10 exceed')
    rejected(lambda d: d['rows'][io1].update(exceedCount=5), 'pass with 5 exceed')
    rejected(lambda d: d['rows'][io1]['pairs'].pop(), 'nine pairs')
    rejected(lambda d: d['decision']['facts'].__setitem__('streamStatus', 'unresolved'), 'forged fact')
    rejected(lambda d: d.__setitem__('outcome', 'stop'), 'forged outcome')
    rejected(lambda d: d['correctness'].pop('E-fd-hygiene'), 'missing correctness row')
    rejected(lambda d: d['correctness'].__setitem__('E-fd-hygiene', {'status': 'pass-current'}), 'pass without receipt')
    rejected(lambda d: d['correctness'].__setitem__('E-fd-hygiene', {'status': 'not-required'}), 'not-required on a required row')
    rejected(lambda d: d['preregistration']['options'].__setitem__('pairsPerCell', 12), 'pairs changed')
    rejected(lambda d: d['rows']['stream|lat:io_ms|cell1']['pairs'][0].update(rust=-1.0), 'negative per-pair measurement (v9)')
    rejected(lambda d: d['independence']['canaries'][0]['values'].__setitem__(0, -1.0), 'negative canary measurement (v9)')
    rejected(lambda d: d['limitations'].append(d['limitations'][0]), 'duplicate limitation entry (v9)')
    rejected(lambda d: d['evaluated'].update(e1ProtocolSha256='f931a120049b7cd420a7085eb1d0a2f5799822e58a645a119f9bd8817faa9d24'), 'E1 v6 hash instead of the bound E1 v7')
    rejected(lambda d: d['design']['logPolicy'].update(rotationInvalidatesTrial=False), 'log policy: rotation no longer invalidates the trial')
    rejected(lambda d: d['design']['logPolicy'].update(logLimitBytes=2 ** 40), 'log policy: log limit changed')
    rejected(lambda d: d['design']['logPolicy'].update(peerMaxBytes=2 ** 30), 'log policy: peer limit changed')
    rejected(lambda d: d['design'].pop('logPolicy'), 'log policy missing')
    rejected(lambda d: d['design']['logPolicy'].update(preOriginReadMaxBytes=2 ** 30), 'log policy: pre-origin bound changed')
    rejected(lambda d: d['design']['logPolicy'].update(logComparedAfterExitFdReleasedStable=False), 'log policy: comparison no longer gated on exit + fd release + stable reads')
    rejected(lambda d: d['design']['logPolicy'].update(originFromRendererStream=False), 'log policy: origin gated on the log')
    rejected(lambda d: d['design']['logPolicy'].update(logFdReleasedAfterExit=False), 'log policy: fd-release check dropped')
    rejected(lambda d: d['design']['logPolicy'].update(logStableReads=1), 'log policy: single read after exit')
    rejected(lambda d: d['design']['logPolicy'].update(logStableGapMs=0), 'log policy: no spacing between the reads')
    rejected(lambda d: d['rows'].__setitem__('lifecycle|lat:descendant_snapshot_ms|cell1', d['rows'][io1]), 'descendant-snapshot row (not registered)')
    rejected(lambda d: d['preregistration']['options'].update(descendantSnapshot='measured'), 'descendantSnapshot=measured (only deferral is offered)')
    rejected(lambda d: d['limitations'].__setitem__(slice(None), ['SYNTHETIC illustration only']), 'named descendant-snapshot limitation missing')
    # B3: a failed row with unknown/absent attribution or bound is schema-valid AND derives STOP; an observed failure is never relabelled
    ex_all = example_docs()
    for nm in ('correctness-failed-unknown', 'correctness-failed-absent'):
        assert ex_all[nm]['decision']['facts']['correctness'] == 'failed_rust_unbounded' and ex_all[nm]['outcome'] == 'stop' and 'S3-correctness-failed-rust-unbounded' in [r['rule'] for r in ex_all[nm]['decision']['reasons']]
    for combo in ({'status': 'failed', 'attribution': 'rust-design', 'boundedCorrectivePath': 'unknown'}, {'status': 'failed', 'attribution': 'unknown', 'boundedCorrectivePath': True},
                  {'status': 'failed', 'attribution': 'rust-design'}, {'status': 'failed', 'boundedCorrectivePath': True}, {'status': 'failed'}):
        d = clone(base); d['correctness']['E-fd-hygiene'] = dict(combo); recompute(d); validate_document(d, schema)
        assert d['outcome'] == 'stop', combo
    d = clone(base); d['correctness']['E-fd-hygiene'] = {'status': 'failed', 'attribution': 'other'}; recompute(d); validate_document(d, schema); assert d['outcome'] == 'inconclusive'
    d = clone(base); d['correctness']['E-fd-hygiene'] = {'status': 'failed', 'attribution': 'rust-design', 'boundedCorrectivePath': True}; recompute(d); validate_document(d, schema); assert d['outcome'] == 'inconclusive'
    print('  failed correctness rows: unknown/absent attribution or bound -> schema-valid, STOP S3 (5 variants); other -> INCONCLUSIVE; rust-design+bounded -> INCONCLUSIVE')
    rejected(lambda d: d['correctness'].__setitem__('E-fd-hygiene', {'status': 'failed', 'attribution': 'unknown', 'boundedCorrectivePath': 'unknown'}), 'failed-unknown row with outcome left CONTINUE (stale outcome)')
    rejected(lambda d: d['correctness'].__setitem__('E-fd-hygiene', {'status': 'failed', 'attribution': 'maybe'}), 'attribution outside {rust-design, other, unknown}')
    rejected(lambda d: d['baselineIdentity'].update(status='refactor_reviewed'), 'refactor_reviewed without files')
    rejected(lambda d: d['baselineIdentity'].update(status='disclosed_not_passed'), 'disclosed_not_passed without files')
    rejected(lambda d: d['matrix'].update(accepted=39), 'complete with 39 accepted')
    rejected(lambda d: d['exploratory'].append({'id': 'x', 'description': 'y', 'nonDecisional': False}), 'decisional exploratory analysis')
    # F5: the reviewer's counterexample (invalid trial listed, everything else unchanged) must be rejected AND must not derive CONTINUE
    rejected(lambda d: d['matrix']['evidenceInvalidTrials'].append('rust-shell:4:2'), 'REVIEW-2 COUNTEREXAMPLE: invalid trial listed, complete=true, accepted=40')
    d = clone(base); d['matrix']['evidenceInvalidTrials'].append('rust-shell:4:2')
    f = derive_safe(d); assert f is None or decide(f)[0] != 'continue'
    print('  counterexample derives no CONTINUE (derivation raises or yields', None if f is None else decide(f)[0], ')')
    rejected(lambda d: (d['matrix']['evidenceInvalidTrials'].append('rust-shell:4:2'), d['matrix'].update(accepted=39, complete=False)), 'invalid trial but pairs still valid')
    rejected(lambda d: d['matrix']['missingSlots'].append('python:1:1'), 'missing slot with complete=true')
    rejected(lambda d: d['design']['schedule'].reverse(), 'schedule not reproduced from the seed')
    rejected(lambda d: d['design'].update(scheduleSeed='another-seed'), 'seed changed')
    rejected(lambda d: d['design']['backendIdentities'][1].update(pid=d['design']['backendIdentities'][0]['pid'], start=d['design']['backendIdentities'][0]['start']), 'two attempts share a backend process')
    rejected(lambda d: d['independence']['canaries'][0].update(values=[100.0 + i for i in range(10)]), 'canary drift not tripped (stale S/trip)')
    rejected(lambda d: d['preregistration']['options'].update(calibration='none'), 'D6 required but no calibration')
    rejected(lambda d: d['preregistration']['calibration'].update(noisyRows=['stream|lat:no_such|cell1']), 'unknown noisy row id')
    rejected(lambda d: d['preregistration']['calibration'].update(uncalibratedRows=['stream|lat:no_such|cell1']), 'unknown uncalibrated row id')
    rejected(lambda d: d['preregistration']['calibration'].update(unresolvableRows=[]), 'the retired calibration key')
    rejected(lambda d: d['preregistration']['calibration'].pop('noisyRows'), 'noisyRows missing')    # reader: derivation and positive/negative
    rejected(lambda d: d['readerBenchmark']['rows']['reader|m1_wall_ms|claude']['pairs'][0].update(rust=99.0), 'reader pair edited, counts stale')
    rejected(lambda d: d['readerBenchmark']['rows']['reader|m1_wall_ms|claude'].update(exceedCount=10), 'reader row pass with 10 exceed')
    rejected(lambda d: d['readerBenchmark']['m0']['claude'].update(rust=False), 'reader pass with m0 false')
    rejected(lambda d: d['readerBenchmark'].update(status='adverse'), 'reader status "adverse" is not a status')
    rw = example_docs()['reader-worse']
    assert rw['readerBenchmark']['status'] == 'worse' and rw['outcome'] == 'inconclusive' and rw['decision']['facts']['readerBenchmark'] == 'worse'
    rejected(lambda d: d['readerBenchmark'].update(status='pass'), 'reader worse relabelled pass', rw)
    rejected(lambda d: d['readerBenchmark']['rows']['reader|m1_wall_ms|claude']['pairs'][0].update(valid=False), 'reader row loses a valid pair without counts', rw)
    nr = example_docs()['complete-reader-not-required']
    rejected(lambda d: d['preregistration']['options'].update(readerRequired=True), 'not_required document claims D6 required', nr)


def test_option_combinations():
    # --- option combinations: exactly the six executable combinations validate ---
    ok_combos = []
    for rr in (True, False):
        for cal in CALIBRATION_MODES:
            try:
                d = make_doc(reader_required=rr, calibration=cal)
                validate_document(d, schema); ok_combos.append((rr, cal))
            except Exception: pass
    assert sorted(ok_combos) == sorted([(True, 'rows+reader'), (False, 'none'), (False, 'rows')]), ok_combos
    print(f'option combinations: {len(ok_combos)} of 6 (readerRequired x calibration) executable, descendant snapshot fixed to deferred: (readerRequired, calibration) = {sorted(ok_combos)}')


def test_schedule():
    # --- schedule tests ---
    s1 = make_schedule('seed-a'); assert s1 == make_schedule('seed-a') and s1 != make_schedule('seed-b')
    for seed in ('seed-a', 'seed-b', 'x', 'y', 'z'):
        sc = make_schedule(seed)
        assert sorted(s['slot'] for s in sc) == list(range(20)) and len({(s['cell'], s['repeat']) for s in sc}) == 20
        assert all(sum(1 for s in sc if s['cell'] == c and s['firstArm'] == 'python') == 5 for c in CELLS)
    print('schedule: deterministic from the seed; each seed gives a permutation of the 20 pair-slots and 5/5 arm-first balance per cell')


def test_degradation_catalogue_never_derives_continue():
    # --- evidence degradation never derives CONTINUE: mutation catalogue ---
    def degrade(label, fn):
        d = clone(base); fn(d); f = derive_safe(d)
        o = None if f is None else decide(f)[0]
        assert o != 'continue', f'DEGRADATION DERIVES CONTINUE: {label}'
        d2 = clone(base); fn(d2)                                   # the same damage from a self-consistent producer (declared fields refreshed)
        try: recompute(d2); o2 = d2['outcome']
        except Exception: o2 = 'recompute raises'
        assert o2 != 'continue', f'SELF-CONSISTENT DEGRADED DOCUMENT DERIVES CONTINUE: {label}'
        return label, ('stale document: ' + ('derivation raises' if o is None else o)), ('refreshed document: ' + o2)
    cat = [
        ('invalid trial listed', lambda d: d['matrix']['evidenceInvalidTrials'].append('rust-shell:1:0')),
        ('missing slot listed', lambda d: d['matrix']['missingSlots'].append('python:4:9')),
        ('integrity failure listed', lambda d: d['matrix']['integrityFailures'].append('x')),
        ('pair invalid flag', lambda d: d['rows'][io1]['pairs'][3].update(valid=False)),
        ('pair value null', lambda d: d['rows'][io1]['pairs'][3].update(rust=None)),
        ('pair value NaN-like', lambda d: d['rows'][io1]['pairs'][3].update(rust=float('inf'))),
        ('two pairs beyond margin', lambda d: [d['rows'][io1]['pairs'][i].update(rust=999.0) for i in (1, 2)]),
        ('all pairs beyond margin', lambda d: [p.update(rust=999.0) for p in d['rows'][io1]['pairs']]),
        ('beat row not improved', lambda d: [p.update(rust=p['python'] + 1.0) for p in d['rows']['beat|lat:kill_reap_ms|cell4']['pairs'][:3]]),
        ('beat row adverse', lambda d: [p.update(rust=p['python'] + 50.0) for p in d['rows']['beat|lat:kill_reap_ms|cell4']['pairs']]),
        ('correctness not rerun', lambda d: d['correctness'].__setitem__('E-fd-hygiene', {'status': 'not-rerun'})),
        ('correctness unknown', lambda d: d['correctness'].__setitem__('E-fd-hygiene', {'status': 'unknown'})),
        ('correctness failed unattributed', lambda d: d['correctness'].__setitem__('E-fd-hygiene', {'status': 'failed'})),
        ('correctness pass without receipt', lambda d: d['correctness']['E-fd-hygiene'].pop('receipt')),
        ('reader m0 false', lambda d: d['readerBenchmark']['m0']['claude'].update(python=False)),
        ('reader rows removed', lambda d: d['readerBenchmark'].update(rows={})),
        ('reader pair invalid', lambda d: d['readerBenchmark']['rows']['reader|m3_peak_rss_mib|opencode']['pairs'][0].update(valid=False)),
        ('reader fixtures missing', lambda d: d['readerBenchmark'].update(fixtures=[])),
        ('reader calibration runs < 10', lambda d: d['readerBenchmark'].update(calibrationRuns=3)),
        ('schedule tampered', lambda d: d['design']['schedule'].reverse()),
        ('shared backend process', lambda d: d['design']['backendIdentities'][5].update(pid=d['design']['backendIdentities'][4]['pid'], start=d['design']['backendIdentities'][4]['start'])),
        ('backend identity missing', lambda d: d['design']['backendIdentities'].pop()),
        ('canary drift', lambda d: d['independence']['canaries'][2].update(values=[100.0 + i for i in range(10)])),
        ('canary value missing', lambda d: d['independence']['canaries'][2]['values'].__setitem__(0, None)),
        ('canary removed', lambda d: d['independence']['canaries'].pop()),
        ('calibration uncalibrated row', lambda d: d['preregistration']['calibration'].update(uncalibratedRows=['stream|lat:burst_ms|cell4'])),
        ('baseline changed', lambda d: d['baselineIdentity'].update(status='changed')),
        ('locality violation', lambda d: d['locality'].update(violation=True)),
        ('maintainability incomplete', lambda d: d['maintainability'].update(complete=False)),
        ('claude observation incomplete', lambda d: d['claudeObservation'].update(state='incomplete')),
        ('row removed', lambda d: d['rows'].pop('budget|lat:warm_shell_ack_ms|cell4')),
        ('margin edited', lambda d: d['rows'][io1]['margin'].update(value=1e9)),
    ]
    for label, fn in cat: print('  degradation', degrade(label, fn))
    print(f'degradation catalogue: {len(cat)} evidence-degrading mutations, none derives CONTINUE')


def test_noisy_degradation_catalogue_never_derives_continue_unless_the_independent_check_passes():
    # --- v14-r2 T7: the catalogue extended with noisy rows, the options and the new report sections ---
    nbase = noisy_doc(['stream|lat:burst_ms|cell4', C1ROW]); sb = 'stream|lat:burst_ms|cell4'
    assert nbase['outcome'] == 'continue' and independent_continue_check(nbase), 'the positive control: a noisy row alone does not stop a CONTINUE'
    pair = lambda d, rid, i: d['rows'][rid]['pairs'][i]
    beyond = lambda d, rid, n: [pair(d, rid, i).update(rust=pair(d, rid, i)['python'] + 999.0) for i in range(n)]
    evidence = [        # damage to the evidence: after a refresh the outcome is never CONTINUE
        ('noisy C1: one difference at zero', lambda d: pair(d, C1ROW, 4).update(rust=pair(d, C1ROW, 4)['python'])),
        ('noisy C1: one difference slightly above zero', lambda d: pair(d, C1ROW, 4).update(rust=pair(d, C1ROW, 4)['python'] + 1.0)),
        ('noisy C1: nine below zero, one beyond the margin', lambda d: pair(d, C1ROW, 9).update(rust=pair(d, C1ROW, 9)['python'] + 500.0)),
        ('noisy C1: all beyond', lambda d: beyond(d, C1ROW, 10)),
        ('noisy C1: a pair invalid', lambda d: pair(d, C1ROW, 2).update(valid=False)),
        ('noisy C1: a value null', lambda d: pair(d, C1ROW, 2).update(rust=None)),
        ('noisy stream row: nine beyond the margin', lambda d: beyond(d, sb, 9)),
        ('noisy stream row: all beyond', lambda d: beyond(d, sb, 10)),
        ('noisy stream row: a pair invalid', lambda d: pair(d, sb, 5).update(valid=False)),
        ('noisy stream row: a value NaN-like', lambda d: pair(d, sb, 5).update(rust=float('inf'))),
        ('resolvable row: two beyond the margin', lambda d: beyond(d, io1, 2)),
        ('invalid trial listed', lambda d: d['matrix']['evidenceInvalidTrials'].append('rust-shell:4:1')),
        ('uncalibrated row listed', lambda d: d['preregistration']['calibration'].update(uncalibratedRows=['stream|lat:io_ms|cell1'])),
    ]
    tamper = [          # damage to the declared sections or options: the document is rejected as it stands
        ('noisyReport removed', lambda d: d.pop('noisyReport')), ('noisyReport entry removed', lambda d: d['noisyReport'].pop()), ('noisyReport label edited', lambda d: d['noisyReport'][0].update(label='noisy')),
        ('noisyReport band edited', lambda d: d['noisyReport'][0]['matrix'].update(bandExcess=[-1e9, -1e9])), ('c1Report removed', lambda d: d.pop('c1Report')),
        ('c1Report status edited', lambda d: d['c1Report'].update(status='improved') or d['c1Report'].update(meanD=1e9)), ('interpretation removed', lambda d: d.pop('interpretation')),
        ('killReap removed', lambda d: d['interpretation'].pop('killReap')), ('continueScope edited', lambda d: d['interpretation'].update(continueScope='CONTINUE.')),
        ('option c1Noisy a', lambda d: d['preregistration']['options'].update(c1Noisy='a')), ('option c1Noisy d', lambda d: d['preregistration']['options'].update(c1Noisy='d')),
        ('option guard 9', lambda d: d['preregistration']['options'].update(noisyGuard=9)), ('option guard 7', lambda d: d['preregistration']['options'].update(noisyGuard=7)),
        ('noisyCap added', lambda d: d['preregistration']['options'].update(noisyCap=5)), ('k limitation removed', lambda d: d['limitations'].__setitem__(slice(None), [t for t in d['limitations'] if not t.startswith('noisy rows: k =')])),
        ('noisy status without a listed row', lambda d: d['rows'][io1].update(status='noisy')), ('noisy list adds a one-arm budget row', lambda d: d['preregistration']['calibration']['noisyRows'].append('budget|res:live_idle:component:sidecar:rssMiB|cell1')),
        ('noisy list emptied, statuses kept', lambda d: d['preregistration']['calibration'].update(noisyRows=[])), ('noisy list reordered', lambda d: d['preregistration']['calibration']['noisyRows'].reverse()),
    ]
    for label, fn in evidence:
        stale = clone(nbase); fn(stale); v = document_ok(stale)
        assert not (v.ok and stale['outcome'] == 'continue' and not independent_continue_check(stale)), f'STALE DEGRADED DOCUMENT DERIVES CONTINUE: {label}'
        fresh = clone(nbase); fn(fresh)
        try: recompute(fresh); o = fresh['outcome']
        except Exception: o = 'recompute raises'
        assert o != 'continue', f'SELF-CONSISTENT DEGRADED DOCUMENT DERIVES CONTINUE: {label}'
    for label, fn in tamper:
        d = clone(nbase); fn(d)
        assert not document_ok(d).ok, f'NOT REJECTED: {label}'
    print(f'noisy degradation catalogue: {len(evidence)} evidence mutations never derive CONTINUE, {len(tamper)} section/option tampers are rejected')


def test_fuzz_never_derives_continue_for_invalid_documents():
    # --- fuzz: random evidence damage; invariant: derived CONTINUE implies a fully valid, in-margin document ---
    rng = random.Random(20260101)
    rows_ids = list(base['rows'])
    paired = [s['id'] for s in REG if s['source'] == 'paired-delta']
    bases = [base, noisy_doc(['stream|lat:burst_ms|cell4', C1ROW]), noisy_doc(paired), noisy_doc(rng.sample(paired, 9))]           # v14-r2 T7: documents with noisy rows, a noisy C1, all 37 noisy
    cont = 0; total = 3000
    for _ in range(total):
        d = clone(rng.choice(bases))
        for _k in range(rng.randint(0, 4)):
            kind = rng.choice(['pair-valid', 'pair-null', 'pair-beyond', 'slot', 'canary', 'correct', 'reader', 'fact', 'noisy-count'])
            rid = rng.choice(rows_ids); i = rng.randrange(10)
            if kind == 'noisy-count':
                rid = rng.choice(paired)
                for j in range(rng.randint(0, 10)): d['rows'][rid]['pairs'][j]['rust'] = d['rows'][rid]['pairs'][j]['python'] + rng.choice([1e6, -1.0, 0.0])
            elif kind == 'pair-valid': d['rows'][rid]['pairs'][i]['valid'] = False
            elif kind == 'pair-null': d['rows'][rid]['pairs'][i][rng.choice(['rust', 'python'])] = None
            elif kind == 'pair-beyond': d['rows'][rid]['pairs'][i]['rust'] = 1e6
            elif kind == 'slot': d['matrix'][rng.choice(['evidenceInvalidTrials', 'missingSlots'])].append(f'{rng.choice(ARMS)}:{rng.choice(CELLS)}:{i}')
            elif kind == 'canary': d['independence']['canaries'][rng.randrange(12)]['values'][i] = rng.choice([None, 1e6, -1e6, 100.0])
            elif kind == 'correct': d['correctness'][rng.choice(CORRECTNESS_IDS)] = rng.choice([{'status': 'not-rerun'}, {'status': 'unknown'}, {'status': 'failed'}, {'status': 'pass-current'}])
            elif kind == 'reader':
              try: rng.choice([lambda: d['readerBenchmark']['m0']['claude'].update(rust=False), lambda: d['readerBenchmark']['rows'].pop(rng.choice(READER_ROWS), None),
                                                lambda: d['readerBenchmark']['rows'][rng.choice(READER_ROWS)]['pairs'][i].update(rust=1e6)])()
              except KeyError: pass
            else: rng.choice([lambda: d['locality'].update(violation=True), lambda: d['maintainability'].update(complete=False), lambda: d['claudeObservation'].update(state='incomplete'),
                              lambda: d['baselineIdentity'].update(status='changed'), lambda: d['survivors'].update(rustArm=True), lambda: d['isolation'].update(breach=True)])()
        raw = derive_safe(d)                                   # a producer that left declared fields stale: derivation must raise or not be CONTINUE
        if raw is not None and decide(raw)[0] == 'continue': assert independent_continue_check(d), 'fuzz: raw CONTINUE for an invalid document'
        try: recompute(d)                                       # a self-consistent producer: every declared field refreshed from the damaged data
        except Exception: continue
        if d['outcome'] == 'continue':
            cont += 1
            assert independent_continue_check(d), 'fuzz: CONTINUE derived for a document that fails the independent check'
    print(f'fuzz: {total} randomly damaged documents; {cont} derived CONTINUE and every one passed the independent fully-valid check; all others raised or were STOP/INCONCLUSIVE')


def test_the_fact_space_has_9216000_combinations_and_the_noisy_domain_values_and_the_i10_noisy_reason():
    # the quick form of T6 (the exhaustive run is `test_decision_table_totality`); rf3c: baselineIdentity gained `disclosed_not_passed` (3 -> 4 values), so 6 912 000 -> 9 216 000
    assert math.prod(len(v) for v in FACT_DOMAINS.values()) == 9_216_000 and FACT_DOMAINS['c1Status'] == ['improved', 'not_improved', 'adverse', 'unresolved', 'noisy']
    f = {'matrixComplete': True, 'designCompliant': True, 'independenceScreen': 'pass', 'calibrationRows': 'clear', 'baselineIdentity': 'identical', 'survivorsRustArm': False, 'isolationBreach': False,
         'correctness': 'ok', 'claudeObservation': 'ok', 'streamStatus': 'pass', 'budgetStatus': 'pass', 'c1Status': 'improved', 'readerBenchmark': 'pass', 'localityViolation': False, 'maintainabilityComplete': True}
    assert decide(f)[0] == 'continue'; f.update(c1Status='noisy')
    assert decide(f) == ('inconclusive', ['I10-kill-reap-noisy']) and not continue_predicate(f)
    f.update(calibrationRows='noisy_listed', c1Status='improved'); assert decide(f) == ('continue', ['C-all-registered-conditions-met'])
    f.update(calibrationRows='uncalibrated'); assert decide(f) == ('inconclusive', ['I4-calibration-uncalibrated-row'])
    assert TABLE_VERSION == 'prereg-e/15' and SCHEMA_ID == 'acr-g1e-outcome/15'


def test_a_disclosed_v5_non_pass_does_not_stop_continue_but_a_changed_baseline_still_derives_i5():
    # rf3c: `disclosed_not_passed` (V5 did not pass, two distinct reviewers judged every listed file, at least one verdict `other`) is allowed to CONTINUE; `changed` is not.
    f = {'matrixComplete': True, 'designCompliant': True, 'independenceScreen': 'pass', 'calibrationRows': 'clear', 'baselineIdentity': 'disclosed_not_passed', 'survivorsRustArm': False, 'isolationBreach': False,
         'correctness': 'ok', 'claudeObservation': 'ok', 'streamStatus': 'pass', 'budgetStatus': 'pass', 'c1Status': 'improved', 'readerBenchmark': 'pass', 'localityViolation': False, 'maintainabilityComplete': True}
    assert decide(f) == ('continue', ['C-all-registered-conditions-met']) and continue_predicate(f)
    f.update(baselineIdentity='changed')
    assert decide(f) == ('inconclusive', ['I5-baseline-changed']) and not continue_predicate(f)
    f.update(baselineIdentity='disclosed_not_passed', isolationBreach=True)
    assert decide(f)[0] == 'stop', 'a disclosed baseline does not relax any stop rule'
    f.update(isolationBreach=False, c1Status='noisy')
    assert decide(f) == ('inconclusive', ['I10-kill-reap-noisy']), 'nor any other inconclusive rule'


def disclosed_doc(verdicts=('behaviour-neutral', 'other'), status='disclosed_not_passed'):
    """`base` with a V5 listing of one file judged by two reviewers (`verdicts`) and the given `baselineIdentity.status`, every derived field refreshed."""
    d = clone(base)
    d['baselineIdentity'].update(status=status, changedFiles=[{'path': 'backend/agent_team_backend/x.py', 'loadedSha256': h('loaded'), 'referenceSha256': h('reference'), 'diff': hr('diff'),
                                                              'verdicts': [{'reviewer': who, 'session': f's-{who}', 'report': hr(f'report-{who}'), 'verdict': word, 'loadedSha256': h('loaded')} for who, word in zip('AB', verdicts)]}])
    recompute(d)
    return d


def test_the_schema_accepts_a_disclosed_not_passed_baseline_only_as_a_complete_two_reviewer_judgement_with_an_other_verdict():
    """rf3e F5: negative cases of the rf3c schema rule (the positive case continues; the rest are rejected by the frozen schema or the derivation)."""
    good = disclosed_doc()
    assert document_ok(good).ok and good['outcome'] == 'continue' and good['decision']['facts']['baselineIdentity'] == 'disclosed_not_passed'
    assert document_ok(disclosed_doc(('other', 'other'))).ok
    neutral = disclosed_doc(('behaviour-neutral', 'behaviour-neutral'))
    assert not document_ok(neutral).ok, 'all verdicts neutral: nothing was disclosed, the status must be refactor_reviewed'
    assert document_ok(disclosed_doc(('behaviour-neutral', 'behaviour-neutral'), status='refactor_reviewed')).ok
    rejected(lambda d: d['baselineIdentity']['changedFiles'][0].update(verdicts=d['baselineIdentity']['changedFiles'][0]['verdicts'][:1]), 'a single reviewer', good)
    rejected(lambda d: d['baselineIdentity']['changedFiles'][0].update(verdicts=[]), 'no reviewer', good)
    rejected(lambda d: d['baselineIdentity']['changedFiles'][0]['verdicts'][0].update(verdict='not-loaded-on-python-arm'), 'a verdict value outside the two', good)
    rejected(lambda d: d['baselineIdentity']['changedFiles'][0]['verdicts'][1].pop('loadedSha256'), 'a verdict without the bytes it was given on', good)
    rejected(lambda d: d['baselineIdentity'].update(changedFiles=[]), 'disclosed_not_passed with an empty listing', good)
    rejected(lambda d: d['baselineIdentity'].update(status='identical'), 'identical with a listing', good)
    rejected(lambda d: d['baselineIdentity'].update(status='made-up'), 'a status outside the four', good)
    rejected(lambda d: d['decision']['facts'].__setitem__('baselineIdentity', 'refactor_reviewed'), 'a fact that is not the record status', good)
    changed = disclosed_doc(status='changed')
    assert document_ok(changed).ok and changed['outcome'] == 'inconclusive', '`changed` (a listed file without the complete judgement) is still I5'


def test_decision_table_totality():
    # --- decision table: total over all 15 facts, CONTINUE exactly the predicate ---
    # v14-r2 V13 (T6): 6 912 000 combinations (calibrationRows 4 values, c1Status 5 values); stop 6 420 480, inconclusive 491 508, continue 12.
    # rf3c: baselineIdentity gained `disclosed_not_passed` (it does not stop CONTINUE; `changed` still derives I5): 9 216 000 combinations; stop 4 x 2 140 160 = 8 560 640,
    # continue 3 x 6 = 18 (identical, refactor_reviewed and disclosed_not_passed), inconclusive 3 x 163 834 + 163 840 (every non-stop `changed` combination) = 655 342.
    names = list(FACT_DOMAINS); count = 0; tally = {'continue': 0, 'stop': 0, 'inconclusive': 0}; seen_rules = set()
    for combo in itertools.product(*(FACT_DOMAINS[n] for n in names)):
        f = dict(zip(names, combo)); o, reasons = decide(f)
        assert reasons and o in tally
        assert (o == 'continue') == continue_predicate(f), f
        tally[o] += 1; count += 1; seen_rules.update(reasons)
    assert count == 9_216_000 and tally == {'continue': 18, 'stop': 8_560_640, 'inconclusive': 655_342}, (count, tally)
    assert {r for r in seen_rules if r.startswith('S')} == set(STOP_RULES.values()), 'every S1-S6 is reachable'
    i_reasons = {'I1-matrix-incomplete', 'I2-design-noncompliant', 'I3-independence-screen-failed', 'I4-calibration-uncalibrated-row', 'I5-baseline-changed',
                 'I6-correctness-missing', 'I6-correctness-failed_other', 'I6-correctness-failed_rust_bounded', 'I7-claude-observation-rust_only_failure', 'I7-claude-observation-other_failure',
                 'I7-claude-observation-incomplete', 'I8-terminal-stream-unresolved', 'I9-lifecycle-budget-unresolved', 'I10-kill-reap-not_improved', 'I10-kill-reap-unresolved', 'I10-kill-reap-noisy',
                 'I11-reader-benchmark-worse', 'I11-reader-benchmark-unresolved', 'I11-reader-benchmark-not_run', 'I12-locality-violation', 'I13-maintainability-incomplete'}
    assert {r for r in seen_rules if r.startswith('I')} == i_reasons, 'every I-reason is reachable, and no other'
    assert not any('noisy_listed' in r or 'too_noisy' in r or 'too-noisy' in r for r in seen_rules) and 'noisy_listed' in FACT_DOMAINS['calibrationRows']
    assert not any('too_noisy' in str(v) for v in FACT_DOMAINS.values()) and 'noisy' in FACT_DOMAINS['c1Status']
    print(f'decision table total: {count} fact combinations, each exactly one outcome {tally}; CONTINUE is exactly the independent predicate')
    f = {n: FACT_DOMAINS[n][0] for n in names}; f['streamStatus'] = 'sideways'
    try: decide(f)
    except ValueError: print('  unknown fact value rejected')
    else: raise AssertionError('unknown fact value accepted')

def test_ledger_arithmetic_and_origin():
    # --- v6: pre-registered fixture/log bounds and their arithmetic (precision: post-origin contribution + registered pre-origin bound) ---
    fb = fixture_output_bytes()
    print(f'  log arithmetic: input text = {reference_input_bytes()} UTF-8 bytes; start line = {fb["start_line"]} B; io ack line <= {fb["ack_io_max"]} B; mid-burst ack = {fb["ack_mid"]} B; '
          f'burst line = {fb["burst_line"]} B; io window <= {fb["io_window"]} B; burst round = {fb["burst_round"]} B; {WORKLOAD["burstRounds"]["1"]} rounds = {fb["burst_rounds_total"]} B; FINAL <= {fb["final_max"]} B; '
          f'post-origin peer output <= {fb["post_origin"]} B ({fb["post_origin"] / 2**20:.3f} MiB), peer limit {PEER_MAX_BYTES} B = {fb["peer_limit_ratio"]:.2f}x; '
          f'pre-origin log <= 3 x {PRE_ORIGIN_READ_MAX} = {fb["pre_origin_log_max"]} B; whole log <= {fb["log_max"]} B ({fb["log_max"] / 2**20:.3f} MiB), log limit {LOG_LIMIT_BYTES} B = {fb["log_limit_ratio"]:.2f}x')
    assert fb['post_origin'] < PEER_MAX_BYTES and fb['log_max'] < LOG_LIMIT_BYTES and PRE_ORIGIN_READ_MAX < BUF_CAP_SOURCE
    # --- v6: origin from renderer stream + counters, log read only after close (F1) ---
    cases = 0; hidden_at_origin = 0
    for startup in (b'', b'last login: x\r\n$ ', b'\x1b[?2004h' * 50 + b'\r\n\r\n\r\nfor agents\r\n', bytes(range(1, 256)) * 3):
        for chunks in ([1], [3], [7, 1, 64], [4096]):
            for payload_sizes in ([1], [2, 3], [5, 17, 255], [4096]):
                for pp in peer_payloads:
                    sim = SimStream(nonce, chunks, payload_sizes); res = ledger_check(sim, startup, pp)
                    assert res['valid'], (startup[:8], chunks, payload_sizes, res); cases += 1
                    hidden_at_origin += not res['marker_on_disk_at_origin']
    print(f'  ledger: {cases} cases (startup output incl. ANSI/CR/noise/binary, reader chunking, flush-payload splits incl. cuts inside multi-byte characters): origin from the renderer stream and counters, log read only after close; counters exact and the whitespace-insensitive log text equals the peer payload')
    print(f'  ledger: in {hidden_at_origin} of those {cases} cases the marker was NOT yet on disk when the origin was taken — the v5 log-gated release would have waited forever there; v6 never reads the log before close')
    # the reviewer's exact case: no startup text, a marker-only flushed payload, an otherwise empty file
    msim = SimStream(nonce, [4096], [len(line('start', nonce=nonce))] + [4096])
    res = ledger_check(msim, b'', peer_payloads[2]); assert res['valid'] and res['marker_on_disk_at_origin'] is False, res
    print('  ledger: marker-only flushed payload with an otherwise empty log file -> marker not on disk at the origin (v5 deadlock condition reproduced in the model); v6 releases from the stream and the trial is valid after close')
    fsim = SimStream(nonce, [4096], [4096]); fsim.feed_all(b'' + fsim.marker); fl = line('final', accepted=1, digest='cd' * 32); fsim.feed_all(fl)
    assert tok('final', accepted=1, digest='cd' * 32) not in fsim.log.peek_disk(); fsim.exit(); assert tok('final', accepted=1, digest='cd' * 32) in fsim.log.read_after_close()
    print('  ledger: FINAL-only flushed payload is absent from disk before close and present after close (the comparison runs after terminal.exit, i.e. after close)')
    try: SimStream(nonce, [5]).log.read_after_close()
    except RuntimeError: print('  ledger: reading the log before close is refused by the reference model (the protocol has no such step)')
    else: raise AssertionError('log read before close was allowed')
    sim = SimStream(nonce, [5], [4]); sim.feed_all(b'ab c \nd\n'); sim.exit(); assert ws_strip(sim.log.read_after_close()) == 'abcd' and sim.log.read_after_close() != 'ab c \nd\n'
    print('  ledger: the log is NOT byte-equal to the stream (per-flush LF loss, line-end whitespace stripped): only the whitespace-insensitive text is compared')
    assert not ledger_check(SimStream(nonce, [5]), b'startup', b'y' * 500, peer_final_extra=3)['valid']
    r2 = ledger_check(SimStream(nonce, [5], drop_ranges=[(100, 120)]), b'startup', b'y' * 500); assert not r2['valid'] and r2['d_drop'] == 20
    assert not ledger_check(SimStream(nonce, [5]), b'noise ' + line('start', nonce=nonce), b'z' * 10)['valid']        # marker twice on the renderer stream (v10: fresh model, marker in the startup)
    assert not ledger_check(SimStream(nonce, [5]), b'q' * (PRE_ORIGIN_READ_MAX + 1), b'z' * 10)['valid']                            # pre-origin bound
    for name, bad in ((' noise line', line('win_begin', wid='i0') + b'for agents\n' + line('win_end', wid='i0')), ('noise phrase inside a line', line('win_begin', wid='i0') + b'xx esc to interrupt yy\n' + line('win_end', wid='i0')),
                      ('ANSI in payload', line('win_begin', wid='i0') + b'\x1b[31mred\n' + line('win_end', wid='i0'))):
        res = ledger_check(SimStream(nonce, [5], [4096]), b'startup', bad); assert not res['valid'] and res['why'] == 'log text differs', (name, res)
    for name, tolerated in (('blank-line run', line('win_begin', wid='i0') + b'a\n\n\n\nb\n' + line('win_end', wid='i0')), ('trailing space', line('win_begin', wid='i0') + b'q  \n' + line('win_end', wid='i0')), ('lone CR', line('win_begin', wid='i0') + b'a\rb\n' + line('win_end', wid='i0'))):
        assert ledger_check(SimStream(nonce, [5], [7]), b'startup', tolerated)['valid'], name
    print('  ledger: accepted-count mismatch, a dropped byte, a duplicated marker, pre-origin output above its bound, a noise line/phrase and an ESC sequence invalidate the trial; blank runs, trailing spaces and lone CR are tolerated')

def test_rotation_truncation_replacement():
    # --- rotation / truncation / replacement / limit: fail-closed after close, no partial voiding ---
    for label, rot in (('rotation inside the measured window (segment boundary)', 300), ('rotation at a small size', 1500)):
        res = ledger_check(SimStream(nonce, [64], [64], rotate_at=rot), b'startup' * 5, pp); assert not res['valid'], (label, res)
        print(f'  rotation: {label} -> trial invalid ({res["why"]})')
    res = ledger_check(SimStream(nonce, [64], [64]), b'startup', pp, truncate_log_before_close=True); assert not res['valid']; print(f'  rotation: truncation before close -> trial invalid ({res["why"]})')
    lg = LineBufferedLog(); lg.write('x\n'); lg.inode = 2; lg.close(); assert log_policy_ok(lg, 1, 0) == (False, 'log file replaced (inode changed)')
    lg = LineBufferedLog(); lg.segments = ['x' * (LOG_LIMIT_BYTES + 1)]; lg.close(); assert log_policy_ok(lg, 1, 0) == (False, 'log larger than the registered limit')
    lg = LineBufferedLog(); lg.segments = ['x' * (3 * PRE_ORIGIN_READ_MAX + 2)]; lg.close(); v = log_policy_ok(lg, lg.inode, 3 * PRE_ORIGIN_READ_MAX + 1); assert result_kind(v) == 'fail' and 'pre-origin' in v[1], v
    res = ledger_check(SimStream(nonce, [64], [64]), b'startup', b'q' * (PEER_MAX_BYTES + 1)); assert not res['valid'] and 'peer exceeded' in res['why']
    class LossySim(SimStream):                                                       # the log lost the marker (unavailable segment / unreadable content)
        def feed_all(self, data):
            super().feed_all(data); self.log.segments[-1] = self.log.segments[-1].replace(tok('start', nonce=self.nonce), '')
    assert ledger_check(LossySim(nonce, [64], [64]), b'startup', pp)['why'].startswith('origin marker not exactly once in the closed log')
    print('  rotation: inode replacement, over-limit log, pre-origin log above its bound, peer over its byte limit and a log missing the marker each invalidate the whole trial; there is no partial-void path')

def test_the_cell_one_peer_writes_forty_rounds_within_the_registered_byte_limits_and_a_cell_four_peer_ten():
    # --- v14-r2 V17: the byte limits follow the per-cell rounds (one constant PEER_MAX for both cells) ---
    fb = fixture_output_bytes()                                                    # default: cell 1, the byte maximum PEER_MAX is checked against
    assert fb['burst_round'] == 265_440 and fb['burst_rounds_total'] == 10_617_600 == 40 * 265_440
    assert fb['post_origin'] == 10_689_733 and fb['total_peer'] == 10_689_733 + fb['start_line'] and fb['log_max'] == 11_476_223
    assert PEER_MAX_BYTES == 12 * 1024 * 1024 == 12_582_912 and LOG_LIMIT_BYTES == 16 * 1024 * 1024 and PRE_ORIGIN_READ_MAX == 256 * 1024
    assert round(fb['peer_limit_ratio'], 2) == 1.18 and round(fb['log_limit_ratio'], 2) == 1.46
    assert fb['post_origin'] < PEER_MAX_BYTES and fb['log_max'] < LOG_LIMIT_BYTES
    fb4 = fixture_output_bytes(cell=4)
    assert fb4['burst_round'] == 265_440 and fb4['burst_rounds_total'] == 10 * 265_440 and fb4['post_origin'] < fb['post_origin'] and fb4['peer_limit_ratio'] > 4
    assert burst_rounds(1) == 40 and burst_rounds(4) == 10 and WORKLOAD['burstRounds'] == {'1': 40, '4': 10}

def test_a_peer_over_the_raised_byte_limit_still_fails_and_one_at_the_old_limit_no_longer_does():
    assert ledger_check(SimStream(nonce, [64], [64]), b'', b'q' * (12 * 1024 * 1024 + 1)).get('why') == 'peer exceeded its registered byte limit'
    assert ledger_check(SimStream(nonce, [64], [64]), b'', b'q' * (3 * 1024 * 1024 + 1)).get('why') != 'peer exceeded its registered byte limit'
    sem_neg('ledger_check', 'the old 3 MiB peer limit', ledger_check(SimStream(nonce, [5]), b'', b'z' * 10, peer_bytes_limit=3 * 1024 * 1024), 'registered PEER_MAX_BYTES')

COLD1, COLD4, KILL1, C1ROW = 'lifecycle|lat:cold_to_first_usable_ms|cell1', 'lifecycle|lat:cold_to_first_usable_ms|cell4', 'lifecycle|lat:kill_reap_ms|cell1', 'beat|lat:kill_reap_ms|cell4'
def _spec(rid): return next(s for s in REG if s['id'] == rid)

def test_r10_margins_are_value_scaled_on_exactly_the_three_rows_and_the_schema_pins_them():
    # --- v14-r2 V18 (rule R10) ---
    assert _spec(COLD1)['margin'] == _spec(COLD4)['margin'] == reference.R(0.10, 30) and _spec(C1ROW)['margin'] == C1_KEY[3] == reference.R(0.10, 20) and _spec(KILL1)['margin'] == A(40)
    assert {s['id'] for s in REG if s['margin']['mode'] == 'relative' and s['unit'] == 'ms'} == {COLD1, COLD4, C1ROW}
    assert abs(margin_for(_spec(C1ROW), 432.0) - 43.2) < 1e-9 and margin_for(_spec(C1ROW), 150.0) == 20 and abs(margin_for(_spec(COLD1), 595.0) - 59.5) < 1e-9
    assert margin_for(_spec(COLD4), 100.0) == 30 and margin_for(_spec(KILL1), 5000.0) == 40 and margin_for(_spec(io1), 1e6) == 0.5
    for rid in (COLD1, COLD4, C1ROW):
        rejected(lambda d, rid=rid: d['rows'][rid]['margin'].update(mode='absolute', value=30 if rid != C1ROW else 20, floor=None), f'{rid}: the old absolute margin')
        rejected(lambda d, rid=rid: d['rows'][rid]['margin'].update(value=0.05), f'{rid}: a different relative share')
    rejected(lambda d: d['rows'][KILL1]['margin'].update(mode='relative', value=0.10, floor=40), 'kill_reap cell 1 stays absolute')

def test_the_beat_rows_exceed_count_and_median_excess_use_the_pairs_margin_but_its_improved_ignores_it():
    spec = _spec(C1ROW)
    deltas = [-5.0, 0.0, 30.0, 40.0, 50.0, 60.0, -100.0, -100.0, -100.0, -100.0]              # python 432 ms: margin max(20, 43.2) = 43.2 (the old absolute rule would count 30, 40 too)
    row = {'pairs': [{'repeat': i, 'python': 432.0, 'rust': 432.0 + d, 'valid': True} for i, d in enumerate(deltas)]}
    c = row_counts(spec, row, set())
    assert c['exceedCount'] == 2 and c['notImprovedCount'] == 5, c                            # a pair at -5 ms counts as below 0; 0, 30 and 40 ms are not improved though inside the margin
    assert abs(c['medianExcess'] - (-2.5 - 43.2)) < 1e-9
    low = {'pairs': [{'repeat': i, 'python': 150.0, 'rust': 150.0 + 21.0, 'valid': True} for i in range(10)]}
    assert row_counts(spec, low, set())['exceedCount'] == 10, 'the floor 20 ms applies below a 200 ms value'
    assert row_counts(spec, {'pairs': [{'repeat': i, 'python': 400.0, 'rust': 440.0, 'valid': True} for i in range(10)]}, set())['exceedCount'] == 0, 'a tie (40 ms = 10 % of 400) is not exceeding'

def test_burst_round_latencies_takes_the_cells_registered_round_count():
    # --- v14-r2 V17: cell 1 = 40 rounds of one peer; a cell-4 peer = 10 rounds ---
    FX = FROZEN['fixture_registry_sha256']
    def run(rounds): return ([(100.0 * kk + 5.0, line('done', kk=f'{kk:02d}')) for kk in range(rounds)], {kk: 100.0 * kk for kk in range(rounds)})
    ev40, send40 = run(40); ev10, send10 = run(10)
    ok = burst_round_latencies(ev40, send40, FX, 1, 40); assert ok.ok and ok.value == [5.0] * 40, ok
    ok = burst_round_latencies(ev10, send10, FX, 4, 10); assert ok.ok and ok.value == [5.0] * 10, ok
    sem_neg('burst_round_latencies', 'cell 1 with the old ten rounds', burst_round_latencies(ev10, send10, FX, 1, 10), 'registered burstRounds')
    sem_neg('burst_round_latencies', 'a cell-4 peer with forty rounds', burst_round_latencies(ev40, send40, FX, 4, 40), 'registered burstRounds')
    sem_neg('burst_round_latencies', 'a cell outside the registered cells', burst_round_latencies(ev10, send10, FX, 2, 10), 'cell')

def test_burst_endpoint():
    # --- v6: one exact burst endpoint (F2) ---
    rng5 = random.Random(5); send = {kk: 1000.0 * kk for kk in range(10)}; events = []; expect_lat = []
    for kk in range(10):
        body = line('win_begin', wid=f'b0r{kk:02d}') + b''.join(line('burst_line', kk=f'{kk:02d}', line=l, xs='x' * 50) for l in range(8)) + line('done', kk=f'{kk:02d}') + line('win_end', wid=f'b0r{kk:02d}')
        pos = 0; t = send[kk] + 5.0
        done_end = body.index(line('done', kk=f'{kk:02d}')) + len(line('done', kk=f'{kk:02d}'))
        while pos < len(body):
            n = rng5.choice([1, 3, 17, 64]); t += 0.5; events.append((t, body[pos:pos + n]))
            if pos < done_end <= pos + n: expect_lat.append(t - send[kk])
            pos += n
    lv = burst_round_latencies(events, send, FROZEN['fixture_registry_sha256'], 4, 10); assert lv.ok, lv; lat = lv.value
    assert lat == expect_lat and all(x is not None for x in lat)
    med = sorted(lat); med = (med[4] + med[5]) / 2
    full = b''.join(c for _, c in events).replace(b'BASELINE_DONE:', b'BASELINE_DONE' + b' '); space_events = []; pos = 0   # deliberately wrong spelling (negative test)
    for t, c in events: space_events.append((t, full[pos:pos + len(c)])); pos += len(c)
    assert burst_round_latencies(space_events, send, FROZEN['fixture_registry_sha256'], 4, 10).value == [None] * 10
    print(f'  burst endpoint: the waiter matches only the exact bytes BASELINE_DONE:r<kk>\\n (split across chunks allowed); 10/10 rounds found, latencies exact, median {med:.1f}; a space-spelled endpoint yields 0/10 (every burst pair invalid, fail-closed)')

def test_loss_and_close_once():
    # --- v9: at-most-once loss delivery check (E1 v7 8.1, L1/L1b/L1c), validated shapes, typed bound taken batch, one-to-one matching (review-8 A) ---
    peer = bytes(range(256)) * 4
    expect(LD([P(0, 100, peer[0:100]), P(300, 500, peer[300:500])], peer, TB([])), 'pass', 'ok', 'two pieces, no batch')
    expect(LD([], peer, TB([]), record=b''), 'pass', 'ok', 'known EMPTY record')
    # missing vs known empty (review-7 B2, kept)
    expect(loss_delivery_ok(binding=BIND, peer=b'abcdefgh', pieces=[], taken_batch=TB([])), 'invalid-input', 'renderer_record: missing', 'record not supplied')
    expect(LD([], b'abcdefgh', TB([]), record=None), 'invalid-input', 'renderer_record: unknown', 'record None')
    expect(LD([], b'abcdefgh', TB([]), record='text'), 'invalid-input', 'expected bytes', 'record not bytes')
    expect(loss_delivery_ok(binding=BIND, peer=b'abcdefgh', pieces=[], renderer_record=b''), 'invalid-input', 'taken_batch: missing', 'taken-batch receipt missing')
    expect(LD([], b'abcdefgh', TB([]), record=b'ab'), 'fail', 'renderer record differs', 'record with unaccounted bytes')
    # REVIEW-8 A counterexample 1: an EMPTY STRING as the taken-batch receipt (literal v8 call shape, and the v9 call shape)
    expect(loss_delivery_ok([], b'abcdefgh', renderer_record=b'', taken_batch=''), 'invalid-input', 'INVALID INPUT', 'REVIEW-8 A1 literal v8 call')
    expect(LD([], b'abcdefgh', ''), 'invalid-input', "taken_batch: expected record (dict), got str", 'REVIEW-8 A1: taken_batch is an empty string')
    expect(LD([], b'abcdefgh', []), 'invalid-input', 'expected record (dict), got list', 'taken_batch is a bare list (not the typed record)')
    # REVIEW-8 A counterexample 2: duplicate batch intervals satisfied by ONE renderer piece (literal v8 call shape, and the v9 call shape)
    Pab = P(0, 2, b'ab')
    expect(loss_delivery_ok([Pab], b'abcdefgh', renderer_record=b'ab', taken_batch=[(0, 2), (0, 2)], cutoff_k=2), 'invalid-input', 'INVALID INPUT', 'REVIEW-8 A2 literal v8 call')
    expect(LD([Pab], b'abcdefgh', TB([(0, 2), (0, 2)]), k=2), 'invalid-input', 'duplicates, overlaps or precedes', 'REVIEW-8 A2: duplicate taken-batch intervals')
    expect(LD([Pab], b'abcdefgh', TB([(0, 2), (2, 4), (4, 6)]), k=2), 'fail', 'L1c', 'one distinct piece cannot satisfy k = 2 (one-to-one)')
    # binding and batch consistency
    expect(LD([], b'abcdefgh', TB([], session_id='s-OTHER'), record=b''), 'invalid-input', 'does not match the observation binding', 'batch receipt for another session')
    expect(LD([], b'abcdefgh', TB([], peer_id='peer-9'), record=b''), 'invalid-input', 'does not match the observation binding', 'batch receipt for another peer')
    expect(LD([], b'abcdefgh', TB([], cutoff_id='cut-2'), record=b''), 'invalid-input', 'does not match the observation binding', 'batch receipt for another cutoff')
    expect(LD([], b'abcdefgh', TB([], unknown=True), record=b''), 'invalid-input', 'in_flight_unknown is true but no taken piece', 'unknown in-flight with no pieces')
    expect(LD([], b'abcdefgh', TB([(0, 2)], unknown=False), record=b''), 'invalid-input', 'pieces listed although no batch', 'pieces without a taken batch')
    expect(LD([], b'abcdefgh', TB([(0, 2)]), k=2, record=b''), 'invalid-semantic', 'n >= 2', 'cutoff k with a one-piece batch (v10: L1c range is semantic)')
    expect(LD([], b'abcdefgh', TB([(0, 2), (1, 3)]), record=b''), 'invalid-input', 'duplicates, overlaps or precedes', 'overlapping batch intervals')
    expect(LD([], b'abcdefgh', TB([(4, 6), (0, 2)]), record=b''), 'invalid-input', 'duplicates, overlaps or precedes', 'batch intervals out of order')
    expect(LD([], b'abcdefgh', TB([(0, 9)]), record=b''), 'invalid-input', 'above the permitted maximum', 'batch interval past the peer end')
    expect(LD([], b'abcdefgh', TB([(3, 3)]), record=b''), 'invalid-input', 'empty or reversed', 'empty batch interval')
    for label, pcs, pr, kind, frag in (
        ('overlap / retried piece', [P(0, 100, peer[0:100]), P(50, 150, peer[50:150])], peer, 'invalid-input', 'duplicates, overlaps or precedes'),
        ('out of order', [P(200, 300, peer[200:300]), P(0, 100, peer[0:100])], peer, 'invalid-input', 'duplicates, overlaps or precedes'),
        ('duplicate piece', [P(0, 100, peer[0:100]), P(0, 100, peer[0:100])], peer, 'invalid-input', 'duplicates, overlaps or precedes'),
        ('wrong bytes', [P(0, 100, peer[1:101])], peer, 'fail', 'differ'),
        ('REVIEW-6 counterexample 2: interval past the end', [P(0, 9, b'abcdefgh')], b'abcdefgh', 'invalid-input', 'above the permitted maximum'),
        ('negative offset', [P(-1, 2, b'abc')], b'abcdefgh', 'invalid-input', 'below the permitted minimum'),
        ('empty interval', [P(3, 3, b'')], b'abcdefgh', 'invalid-input', 'empty'),
        ('reversed interval', [P(5, 3, b'ab')], b'abcdefgh', 'invalid-input', 'empty or reversed'),
        ('data shorter than the interval', [P(0, 4, b'abc')], b'abcdefgh', 'invalid-input', 'correspond one to one'),
        ('data longer than the interval', [P(0, 2, b'abc')], b'abcdefgh', 'invalid-input', 'correspond one to one'),
        ('malformed piece record', [{'a': 0, 'b': 2, 'data': b'ab'}], b'abcdefgh', 'invalid-input', "missing field(s) ['after_exit']"),
        ('after_exit not a bool', [{'a': 0, 'b': 2, 'data': b'ab', 'after_exit': 0}], b'abcdefgh', 'invalid-input', 'expected bool'),
    ):
        expect(LD(pcs, pr, TB([]), record=b''.join(x.get('data', b'') if isinstance(x.get('data'), bytes) else b'' for x in pcs)), kind, frag, label)
    expect(LD([P(0, 2, b'ab')], b'abcdefgh', TB([]), record=b'abde'), 'fail', 'renderer record differs', 'REVIEW-6 cex 1 piece form')
    # E1 v7 8.1 late output. Batch of n=4 pieces taken at the cutoff; the sink had accepted k=2.
    for late_count in range(0, 3):
        late = [P(x, y, data[x:y], True) for x, y in batch[2:2 + late_count]]
        expect(LD(pre + late, data, TB(batch), k=2), 'pass', 'ok', f'{late_count} late remaining pieces')
    for label, pcs, kind, frag in (
        ('a byte outside the taken batch after the exit', pre + [P(40, 50, data[40:50], True)], 'fail', 'not a remaining piece'),
        ('a remaining piece re-sent after the exit', pre + [P(20, 30, data[20:30], True), P(20, 30, data[20:30], True)], 'invalid-input', 'duplicates, overlaps or precedes'),
        ('an already-delivered piece re-sent after the exit', pre + [P(10, 20, data[10:20], True)], 'invalid-input', 'duplicates, overlaps or precedes'),
        ('remaining pieces out of order', pre + [P(30, 40, data[30:40], True), P(20, 30, data[20:30], True)], 'invalid-input', 'duplicates, overlaps or precedes'),
        ('part of a remaining piece after the exit', pre + [P(20, 25, data[20:25], True)], 'fail', 'does not coincide'),
        ('a piece straddling two batch pieces', [P(0, 10, data[0:10]), P(10, 30, data[10:30])], 'fail', 'does not coincide'),
        ('L1c cutoff: only 1 of k=2 pieces before the exit', pre[:1] + [P(10, 20, data[10:20], True)], 'fail', 'L1c'),
        ('L1c cutoff: 3 pieces before the exit with k=2', pre + [P(20, 30, data[20:30])], 'fail', 'L1c'),
        ('before-exit piece after an after-exit piece', pre + [P(20, 30, data[20:30], True), P(30, 40, data[30:40], False)], 'fail', 'marked before exit'),
    ):
        expect(LD(pcs, data, TB(batch), k=2), kind, frag, label)
    expect(LD([P(0, 10, data[0:10], True)], data, TB([])), 'fail', 'not a remaining piece', 'after-exit piece with no taken batch')
    # L1e / L1f close-once and retirement fence, now with the descriptor-number reuse and the fixture preconditions (review-8 B)
    expect(close_once_ok('L1e', L1E), 'pass', 'ok', 'L1e'); expect(close_once_ok('L1f', L1F), 'pass', 'ok', 'L1f')
    for label, route, rec, kind, frag in (
        ('L1e NO REUSE OBTAINED (freed 7, replacement 9)', 'L1e', {**L1E, 'replacement_number': 9, 'numbers_equal_harness_verified': False}, 'fail', 'no descriptor-number reuse obtained'),
        ('L1f NO REUSE OBTAINED (freed 7, replacement 9)', 'L1f', {**L1F, 'replacement_number': 9, 'numbers_equal_harness_verified': False}, 'fail', 'no descriptor-number reuse obtained'),
        ('L1e harness says equal but the numbers differ', 'L1e', {**L1E, 'replacement_number': 9}, 'fail', 'contradicts'),
        ('L1f harness says not equal but the numbers agree', 'L1f', {**L1F, 'numbers_equal_harness_verified': False}, 'fail', 'contradicts'),
        ('L1e replacement number missing', 'L1e', {k: v for k, v in L1E.items() if k != 'replacement_number'}, 'invalid-input', "missing field(s) ['replacement_number']"),
        ('L1f freed number missing', 'L1f', {k: v for k, v in L1F.items() if k != 'freed_number'}, 'invalid-input', "missing field(s) ['freed_number']"),
        ('L1e freed number not an int', 'L1e', {**L1E, 'freed_number': '7'}, 'invalid-input', 'expected int'),
        ('L1f replacement number negative', 'L1f', {**L1F, 'replacement_number': -1}, 'invalid-input', 'below the permitted minimum'),
        ('L1e handle kind unknown', 'L1e', {**L1E, 'handle_kind': 'socket'}, 'invalid-input', 'not one of'),
        ('L1e precondition not established (not root-live)', 'L1e', {**L1E, 'precondition_retained_stream_closed_root_live': False}, 'fail', 'precondition'),
        ('L1f precondition not established (finisher not suspended)', 'L1f', {**L1F, 'precondition_finisher_suspended_in_emit_ignoring_first_cancel': False}, 'fail', 'precondition'),
        ('L1f precondition not established (handle not closed by loss step 2)', 'L1f', {**L1F, 'precondition_loss_step2_closed_the_handle': False}, 'fail', 'precondition'),
        ('L1e precondition field missing', 'L1e', {k: v for k, v in L1E.items() if k != 'precondition_retained_stream_closed_root_live'}, 'invalid-input', 'missing field'),
        ('L1e raw close of the reused number', 'L1e', {**L1E, 'loss_step2_raw_close_for_session': True}, 'fail', 'violated'),
        ('L1e unrelated resource closed', 'L1e', {**L1E, 'unrelated_resource_open_and_usable_after_loss': False}, 'fail', 'violated'),
        ('L1e runtime_lost queued for an already-exited session', 'L1e', {**L1E, 'runtime_lost_exits_for_session': 1}, 'fail', 'violated'),
        ('L1e field unknown', 'L1e', {**L1E, 'unrelated_resource_open_and_usable_after_loss': None}, 'invalid-input', 'unknown (None)'),
        ('L1f resumed finisher closes', 'L1f', {**L1F, 'resumed_finisher_raw_close': True}, 'fail', 'violated'),
        ('L1f TTY_EOF to the retired sidecar', 'L1f', {**L1F, 'resumed_finisher_tty_eof': True}, 'fail', 'violated'),
        ('L1f normal exit published', 'L1f', {**L1F, 'resumed_finisher_normal_exit': True}, 'fail', 'violated'),
        ('L1f two runtime_lost exits', 'L1f', {**L1F, 'runtime_lost_exits_for_session': 2}, 'fail', 'violated'),
        ('L1f late bytes outside the batch', 'L1f', {**L1F, 'after_exit_bytes_only_from_taken_batch': False}, 'fail', 'violated'),
        ('L1f count as boolean', 'L1f', {**L1F, 'runtime_lost_exits_for_session': True}, 'invalid-input', 'expected int'),
        ('L1f boolean as string', 'L1f', {**L1F, 'resumed_finisher_stopped_at_fence': 'true'}, 'invalid-input', 'expected bool'),
        ('L1e receipt carries an L1f field', 'L1e', {**L1E, 'resumed_finisher_tty_eof': False}, 'invalid-input', 'unknown field'),
        ('receipt missing', 'L1f', None, 'invalid-input', 'unknown (None)'),
        ('route unknown', 'L1g', L1F, 'invalid-input', 'not one of'),
    ):
        expect(close_once_ok(route, rec), kind, frag, label)
    print('  loss (E1 v7 8.1, validated): typed taken-batch record bound to the same peer/session/cutoff; both review-8 A counterexamples (empty-string receipt; duplicate batch '
          'intervals satisfying k=2 with one piece) are rejected as invalid input, and one distinct piece against two distinct batch pieces fails L1c (one to one); missing vs known-empty '
          'record kept distinct; zero/one/both late remaining pieces pass; outside-batch, partial, straddling, re-sent, out-of-order and cutoff-mismatch cases fail')
    print('  close-once (E1 v7 L1e/L1f): receipts carry handle kind, freed and replacement numbers, the harness equality flag and the fixture precondition(s); "no reuse obtained" '
          '(7 vs 9), a flag contradicting the numbers, an unestablished precondition, and missing/mistyped/negative/unknown fields each fail or are rejected; behaviour violations fail')

def test_resize_assertion():
    # --- v9: byte-based resize assertion, E1 v7 (= v6) 9.4 B1 + B2 + B3, with the validated contract (stream_end and reported drop count required) ---
    RS = lambda R, before, after, dropped, rep=None, b2=True, end=6000: resize_slurp_assertion(R=R, stream_end=end, before_ack=before, after_ack=after, dropped=dropped,
                                                                                              reported_dropped_bytes=sum(y - x for x, y in dropped) if rep is None else rep, context=resize_ctx('rust-shell' if b2 else 'python'))
    for label, args, kind, frag in (
        ('plain', (1000, [(0, 1000)], [(1000, 3000)], []), 'pass', 'ok'),
        ("review-4's 950-byte partial-line case", (950, [(0, 950)], [(950, 3000)], []), 'pass', 'ok'),
        ('drop mid-window', (950, [(0, 300), (400, 950)], [(950, 3000)], [(300, 400)]), 'pass', 'ok'),
        ('drop straddling R', (950, [(0, 900)], [(1000, 3000)], [(900, 1000)]), 'pass', 'ok'),
        ('drops on both sides', (950, [(0, 300), (400, 950)], [(950, 1200), (1300, 3000)], [(300, 400), (1200, 1300)]), 'pass', 'ok'),
        ('post-slurp bytes dropped (B3)', (950, [(0, 950)], [(1100, 3000)], [(950, 1100)]), 'pass', 'ok'),
        ('post-slurp gap belongs to the R2 gap check, not B1/B2', (950, [(0, 900)], [(1000, 3000)], [(900, 950)]), 'pass', 'ok'),
        ('B1: 10 undropped pre-slurp bytes missing', (950, [(0, 940)], [(950, 3000)], []), 'fail', 'B1'),
        ('B1: a later drop cannot excuse an earlier byte', (950, [(0, 500), (510, 950)], [(950, 3000)], [(5000, 5100)]), 'fail', 'B1'),
        ('B1: pre-slurp bytes emitted after the ack', (950, [(0, 900)], [(900, 3000)], []), 'fail', 'B1'),
        ('a byte emitted twice across before/after', (950, [(0, 950)], [(900, 3000)], []), 'fail', 'emitted twice'),
        ('a byte both dropped and emitted', (950, [(0, 300), (400, 950)], [(950, 3000)], [(300, 450)]), 'fail', 'both emitted and dropped'),
        ('wrong reported drop count', (950, [(0, 300), (400, 950)], [(950, 3000)], [(300, 400)], 99), 'fail', 'reported drop count'),
        ('B2: review-6 false pass (offset R before the ack)', (950, [(0, 951)], [(951, 3000)], []), 'fail', 'B2'),
        ('B2: v6-accepted pre-ack post-slurp case', (950, [(0, 950), (950, 1000)], [(1000, 3000)], []), 'fail', 'B2'),
        ('within-list overlap', (950, [(0, 950), (900, 1000)], [(1000, 3000)], []), 'invalid-input', 'duplicates, overlaps or precedes'),
        ('interval past stream_end', (950, [(0, 950)], [(950, 7000)], []), 'invalid-input', 'above the permitted maximum'),
        ('R past stream_end', (7000, [(0, 950)], [(950, 3000)], []), 'invalid-input', 'above the permitted maximum'),
        ('empty-string interval list', (950, '', [(950, 3000)], []), 'invalid-input', 'an empty string is not a list'),
    ):
        expect(RS(*args), kind, frag, label)
    expect(RS(950, [(0, 951)], [(951, 3000)], [], b2=False), 'pass', 'ok', 'Python arm: B2 not required')
    expect(resize_slurp_assertion(R=950, stream_end=6000, before_ack=[(0, 950)], after_ack=[], dropped=[], context=resize_ctx('rust-shell')), 'invalid-input', 'reported_dropped_bytes: missing', 'reported drop count not supplied')
    assert resize_b2_observed(950, 6000, [(0, 951)], resize_ctx('python')).value is False and resize_b2_observed(950, 6000, [(0, 950)], resize_ctx('python')).value is True
    print("  resize (E1 v7 9.4, validated): B1 and B2 enforced on the Rust arm, B2 recorded on the Python arm; stream_end and the reported drop count are required inputs; "
          "within-list overlaps, out-of-range offsets and an empty-string list are rejected as invalid input")

def test_log_final_check():
    # --- v9: log final check after terminal.exit (review-7 B3), validated contract ---
    expect(LF('python', None, {'backend': False}, good), 'pass', 'ok', 'python')
    expect(LF('rust-shell', True, {'backend': False, 'sidecar': False}, good), 'pass', 'ok', 'rust live')
    expect(LF('rust-shell', False, {'backend': False}, good), 'pass', 'ok', 'rust sidecar gone')
    for label, args, kind, frag in (
        ('REVIEW-7 B3 case 1: empty inventory', ('python', None, {}, good), 'invalid-input', "missing field(s) ['backend']"),
        ('REVIEW-7 B3 case 2: backend held state unknown', ('python', None, {'backend': None}, good), 'invalid-input', 'unknown (None)'),
        ('REVIEW-7 B3 case 3: live sidecar not inspected', ('rust-shell', True, {'backend': False}, good), 'invalid-input', "missing field(s) ['sidecar']"),
        ('inspection error recorded', ('rust-shell', True, {'backend': False, 'sidecar': 'error'}, good), 'invalid-input', 'expected bool'),
        ('held state as a string', ('python', None, {'backend': 'false'}, good), 'invalid-input', 'expected bool'),
        ('held state as 0', ('python', None, {'backend': 0}, good), 'invalid-input', 'expected bool'),
        ('Rust arm: sidecar liveness unknown', ('rust-shell', None, {'backend': False}, good), 'invalid-input', 'sidecar_live: unknown'),
        ('Python arm with a sidecar entry', ('python', None, {'backend': False, 'sidecar': False}, good), 'invalid-input', 'unknown field'),
        ('Python arm with a sidecar liveness value', ('python', False, {'backend': False}, good), 'invalid-input', 'must be None'),
        ('unexpected process in the inventory', ('rust-shell', False, {'backend': False, 'helper': False}, good), 'invalid-input', 'unknown field'),
        ('inventory missing', ('python', None, None, good), 'invalid-input', 'unknown (None)'),
        ('one read only', ('python', None, {'backend': False}, good[:1]), 'invalid-input', 'at least 2'),
        ('reads not strictly increasing in time', ('python', None, {'backend': False}, [good[1], good[0]]), 'invalid-input', 'does not strictly exceed'),
        ('sha256 not hex', ('python', None, {'backend': False}, [good[0], {**good[1], 'sha256': 'aa'}]), 'invalid-input', 'does not match'),
        ('unknown arm', ('both', None, {'backend': False}, good), 'invalid-input', 'not one of'),
        ('backend still holds the log', ('python', None, {'backend': True}, good), 'fail', 'still open'),
        ('sidecar holds the log', ('rust-shell', True, {'backend': False, 'sidecar': True}, good), 'fail', 'still open'),
        ('reads too close', ('python', None, {'backend': False}, [good[0], {**good[1], 't_ms': 100}]), 'fail', 'closer'),
        ('size grew after exit', ('python', None, {'backend': False}, [good[0], {**good[1], 'size': 120, 'sha256': HB}]), 'fail', 'changed'),
        ('truncated after exit', ('python', None, {'backend': False}, [good[0], {**good[1], 'size': 40, 'sha256': HB}]), 'fail', 'changed'),
        ('replaced after exit (inode)', ('python', None, {'backend': False}, [good[0], {**good[1], 'ino': 8}]), 'fail', 'changed'),
        ('content rewritten in place (same size)', ('python', None, {'backend': False}, [good[0], {**good[1], 'sha256': HB, 'mtime_ns': 6}]), 'fail', 'changed'),
        ('not a regular file', ('python', None, {'backend': False}, [good[0], {**good[1], 'regular': False}]), 'fail', 'regular'),
    ):
        expect(LF(*args), kind, frag, label)
    print('  log final check (validated): complete per-arm inventory with real booleans required (the three review-7 cases, inspection errors, non-boolean states, unknown liveness, extra '
          'processes, too few or unordered reads and non-hex digests are invalid input); a held descriptor, reads too close, growth, truncation, replacement, rewrite and a non-regular file fail; '
          'PASS never claims that close() succeeded')

def test_pooled_null():
    # --- v4: pooled-mixture null for non-identical independent signs (N1) ---
    pn = pooled_null_check()
    print(f'  pooled null: over {pn["draws"]} random/adversarial heterogeneous p-vectors the worst P(E<=1 | mean p >= 1/2) = {pn["worst_pass_error"]:.5f} <= {pn["bound_pass"]:.5f} and the worst P(E=10 | mean p <= 1/2) = {pn["worst_adverse_error"]:.6f} <= {pn["bound_adverse"]:.6f}')

def test_semantic_negative_tests():
    # --- v10: SEMANTIC negative tests, one or more per checker (assignment 10 item 4), plus the three review-9 counterexamples ---
    # loss_delivery_ok — REVIEW-9 finding 1 exactly as executed by the reviewer (peer b'ab', batch [[0,1],[1,2]], in_flight_unknown true)
    sem_neg('loss_delivery_ok', 'REVIEW-9 F1: L1c with k = 0 (nothing received at the cutoff)', loss_delivery_ok(L1C, b'ab', [], b'', tb9, 0), '1 <= k < n')
    sem_neg('loss_delivery_ok', 'REVIEW-9 F1: L1c with k = n (nothing pending at the cutoff)',
            loss_delivery_ok(L1C, b'ab', [P(0, 1, b'a'), P(1, 2, b'b')], b'ab', tb9, 2), '1 <= k < n')
    sem_neg('loss_delivery_ok', 'L1c with cutoff_k omitted', loss_delivery_ok(L1C, b'ab', [P(0, 1, b'a')], b'a', tb9), 'requires cutoff_k')
    sem_neg('loss_delivery_ok', 'L1c with a one-piece batch', loss_delivery_ok(L1C, b'ab', [], b'', {**tb9, 'pieces': [[0, 2]]}, 1), 'n >= 2')
    sem_neg('loss_delivery_ok', 'L1c with no taken batch', loss_delivery_ok(L1C, b'ab', [], b'', {**tb9, 'in_flight_unknown': False, 'pieces': []}, 1), 'n >= 2')
    assert loss_delivery_ok(L1C, b'ab', [P(0, 1, b'a')], b'a', tb9, 1).ok                                    # the valid L1c: k = 1 of n = 2
    sem_neg('loss_delivery_ok', 'L1 with a cutoff_k', loss_delivery_ok({**L1C, 'variant': 'L1'}, b'ab', [P(0, 1, b'a')], b'a', tb9, 1), 'takes no cutoff_k')
    sem_neg('loss_delivery_ok', 'L1b-release-after with a cutoff_k', loss_delivery_ok({**L1C, 'variant': 'L1b-release-after'}, b'ab', [P(0, 1, b'a')], b'a', tb9, 1), 'takes no cutoff_k')
    sem_neg('loss_delivery_ok', 'L1b-release-after without a taken batch',
            loss_delivery_ok({**L1C, 'variant': 'L1b-release-after'}, b'ab', [], b'', {**tb9, 'in_flight_unknown': False, 'pieces': []}), 'requires a taken batch')
    sem_neg('loss_delivery_ok', 'binding to E1 v6 instead of the frozen E1 v7', loss_delivery_ok({**L1C, 'e1_sha256': 'f931a120049b7cd420a7085eb1d0a2f5799822e58a645a119f9bd8817faa9d24'}, b'ab', [P(0, 1, b'a')], b'a', tb9, 1), 'not the frozen e1')
    assert loss_delivery_ok({**L1C, 'variant': 'L1'}, b'ab', [P(0, 1, b'a')], b'a', tb9).ok and loss_delivery_ok({**L1C, 'variant': 'L1b-release-after'}, b'ab', [P(0, 1, b'a')], b'a', tb9).ok
    inside = {**L1C, 'variant': 'L1b-release-inside'}; none_tb = {**tb9, 'in_flight_unknown': False, 'pieces': []}
    assert loss_delivery_ok(inside, b'ab', [P(0, 1, b'a'), P(1, 2, b'b')], b'ab', none_tb).ok
    v = loss_delivery_ok(inside, b'ab', [P(0, 1, b'a')], b'a', none_tb); assert result_kind(v) == 'fail' and 'not every byte' in v[1], v
    dat = b'0123456789' * 4; tb4 = {**B9, 'in_flight_unknown': True, 'pieces': [[0, 10], [10, 20], [20, 30], [30, 40]]}
    v = loss_delivery_ok({**L1C}, dat, [P(0, 10, dat[0:10]), P(20, 30, dat[20:30], True)], dat[0:10] + dat[20:30], tb4, 1)
    assert result_kind(v) == 'fail' and 'contiguous prefix' in v[1], v                                          # remaining piece k+1 skipped, k+2 delivered after the exit
    print('  semantic[loss_delivery_ok]: the reviewer\'s L1c k = 0 and k = n PASSes are now invalid-semantic; L1c without cutoff_k, with n < 2 or with no batch, a cutoff_k on L1/L1b, L1b-release-after '
          'without a batch and an E1 v6 binding are rejected; the valid k = 1 of n = 2 passes; L1 and L1b-release-after keep their own variants; L1b-release-inside fails when a byte is missing; a skipped remaining piece (k+1 missing, k+2 after the exit) fails')
    # close_once_ok
    for label, route, rec, frag in (
        ('Windows HANDLE receipt in a macOS registration', 'L1f', {**L1F, 'handle_kind': 'HANDLE'}, 'E5'),
        ('E1 hash not the frozen one', 'L1e', {**L1E, 'e1_sha256': BAD_HASH}, 'not the frozen e1'),
        ('L1e: replacement opened before the number was freed', 'L1e', {**L1E, 't_replacement_opened_ms': 5.0}, 'must follow'),
        ('L1e: resource checked before the sidecar was killed', 'L1e', {**L1E, 't_checked_ms': 25.0}, 'must follow'),
        ('L1f: number freed before the sidecar was killed', 'L1f', {**L1F, 't_freed_ms': 5.0}, 'must follow'),
        ('L1f: sink released before the replacement was opened', 'L1f', {**L1F, 't_sink_released_ms': 25.0}, 'must follow'),
        ('L1f: two events at the same instant', 'L1f', {**L1F, 't_replacement_opened_ms': L1F['t_freed_ms']}, 'must follow'),
    ):
        sem_neg('close_once_ok', label, close_once_ok(route, rec), frag)
    print('  semantic[close_once_ok]: a HANDLE receipt, a non-frozen E1 hash and every out-of-order or simultaneous event pair are rejected')
    # resize_slurp_assertion / resize_b2_observed
    def RSC(R, before, after, dropped, **ctx): return resize_slurp_assertion(R=R, stream_end=6000, before_ack=before, after_ack=after, dropped=dropped, reported_dropped_bytes=sum(y - x for x, y in dropped), context=resize_ctx('rust-shell', **ctx))
    assert RSC(6000, [(0, 6000)], [], [], variant='Q').ok
    for label, v, frag in (
        ('acknowledgement before the slurp ended', RSC(950, [(0, 950)], [(950, 3000)], [], t_slurp_end_ms=30.0, t_ack_ms=20.0), 'must not precede'),
        ('S variant with R = 0 (nothing read before the slurp)', RSC(0, [], [(0, 3000)], []), '0 < R < stream_end'),
        ('S variant with R = stream_end (nothing read after the slurp)', RSC(6000, [(0, 6000)], [], []), '0 < R < stream_end'),
        ('Q variant with R < stream_end', RSC(950, [(0, 950)], [(950, 3000)], [], variant='Q'), 'R == stream_end'),
        ('E1 hash not the frozen one', RSC(950, [(0, 950)], [(950, 3000)], [], e1=BAD_HASH), 'not the frozen e1'),
    ):
        sem_neg('resize_slurp_assertion', label, v, frag)
    v = resize_slurp_assertion(R=950, stream_end=6000, before_ack=[(0, 950)], after_ack=[(950, 3000)], dropped=[], reported_dropped_bytes=0, context=resize_ctx('rust-shell'), require_b2=False)
    assert result_kind(v) == 'invalid-input' and 'arguments' in v[1], v                                          # the v9 caller flag no longer exists
    for label, ctx, frag in (
        ('Rust arm (B2 is a gate there, not a description)', resize_ctx('rust-shell'), 'only on the Python arm'),
        ('Q variant', resize_ctx('python', variant='Q'), 'only meaningful on the S variant'),
        ('E1 hash not the frozen one', resize_ctx('python', e1=BAD_HASH), 'not the frozen e1'),
        ('acknowledgement before the slurp ended', resize_ctx('python', t_slurp_end_ms=30.0, t_ack_ms=20.0), 'must not precede'),
    ):
        sem_neg('resize_b2_observed', label, resize_b2_observed(950, 6000, [(0, 950)], ctx), frag)
    sem_neg('resize_b2_observed', 'S variant with R = stream_end', resize_b2_observed(6000, 6000, [(0, 6000)], resize_ctx('python')), '0 < R < stream_end')
    print('  semantic[resize]: an acknowledgement before the slurp end, S with R = 0 or R = stream_end, Q with R < stream_end, a non-frozen E1 hash and (B2 record) a Rust-arm or Q-variant call are '
          'rejected; B2 is derived from context.arm (the v9 require_b2 flag is gone and is rejected as an argument)')
    # log_final_check
    sem_neg('log_final_check', 'first read taken before terminal.exit', log_final_check('python', None, {'backend': False}, good, t_exit_ms=100.0, t_inventory_ms=100.0), 'must not precede')
    sem_neg('log_final_check', 'inventory taken before terminal.exit',
            log_final_check('python', None, {'backend': False}, [{**x, 't_ms': x['t_ms'] + 50} for x in good], t_exit_ms=40.0, t_inventory_ms=10.0), 'must not precede')
    sem_neg('log_final_check', 'log-policy constant changed after the freeze (LOG_STABLE_GAP_MS = 1)',
            patched('LOG_STABLE_GAP_MS', 1, lambda: log_final_check('python', None, {'backend': False}, good, t_exit_ms=0.0, t_inventory_ms=0.0)), 'in-force log_policy')
    print('  semantic[log_final_check]: a read or the inventory before terminal.exit and a log-policy constant changed after the freeze are rejected')
    # burst_round_latencies — REVIEW-9 finding 2 (the literal v9 call now lacks the fixture binding; the bound form with an early endpoint is invalid, never a value)
    v = burst_round_latencies([(0.0, b'BASELINE_DONE:r00\n')], {0: 1.0}, cell=4, rounds=1); assert result_kind(v) == 'invalid-input' and v.value is None, v
    FX = FROZEN['fixture_registry_sha256']
    send10 = {kk: 100.0 * kk for kk in range(10)}; ev10 = [(100.0 * kk + 5.0, line('done', kk=f'{kk:02d}')) for kk in range(10)]
    ok10 = burst_round_latencies(ev10, send10, FX, 4, 10); assert ok10.ok and ok10.value == [5.0] * 10, ok10
    early = [(t - 6.0 if kk == 3 else t, c) for kk, (t, c) in enumerate(ev10)]
    v = burst_round_latencies(early, send10, FX, 4, 10); sem_neg('burst_round_latencies', 'REVIEW-9 F2: endpoint before its round was sent', v, 'precedes the round'); assert v.value is None
    sem_neg('burst_round_latencies', 'an endpoint occurring twice on the stream', burst_round_latencies(ev10 + [(2000.0, line('done', kk='03'))], send10, FX, 4, 10), 'more than once')
    sem_neg('burst_round_latencies', 'next round sent before the previous DONE', burst_round_latencies(ev10, {**send10, 4: 302.0}, FX, 4, 10), 'before round 03 ended')
    sem_neg('burst_round_latencies', 'send times not increasing', burst_round_latencies(ev10[:2] + ev10[3:], {**send10, 2: 50.0}, FX, 4, 10), 'must follow')
    sem_neg('burst_round_latencies', 'rounds differ from the registered burstRounds', burst_round_latencies([(5.0, line('done', kk='00'))], {0: 0.0}, FX, 4, 1), 'registered burstRounds')
    sem_neg('burst_round_latencies', 'receipt bound to another fixture', burst_round_latencies(ev10, send10, BAD_HASH, 4, 10), 'not the frozen fixture_registry')
    sem_neg('burst_round_latencies', 'fixture registry changed after the freeze (endpoint spelled with a space)',
            patched('FIXTURE_TOKENS', {**FIXTURE_TOKENS, 'done': 'BASELINE_DONE' + ' r{kk}'}, lambda: burst_round_latencies(ev10, send10, FX, 4, 10)), 'in-force fixture_registry')
    print('  semantic[burst_round_latencies]: the reviewer\'s negative latency is now invalid (literal call: no fixture binding; bound call: endpoint precedes its send) and never a value; a round sent '
          'before the previous DONE, non-increasing sends, a non-registered round count, another fixture digest and a fixture registry changed after the freeze are rejected')
    # ledger_check
    used = SimStream(nonce, [5]); used.feed_all(b'x')
    sem_neg('ledger_check', 'a model reused across trials', ledger_check(used, b'', b'z' * 10), 'not fresh')
    sem_neg('ledger_check', 'nonce not 128-bit hex', ledger_check(SimStream('nonce-xyz', [5]), b'', b'z' * 10), 'nonce')
    sem_neg('ledger_check', 'peer limit not the registered one', ledger_check(SimStream(nonce, [5]), b'', b'z' * 10, peer_bytes_limit=PEER_MAX_BYTES * 2), 'registered PEER_MAX_BYTES')
    sem_neg('ledger_check', 'fixture registry changed after the freeze',
            patched('WORKLOAD', {**WORKLOAD, 'burstRounds': 9}, lambda: ledger_check(SimStream(nonce, [5]), b'', b'z' * 10)), 'in-force fixture_registry')
    print('  semantic[ledger_check]: a reused model, a non-128-bit nonce, a non-registered peer limit and a fixture registry changed after the freeze are rejected')
    # log_policy_ok
    lg = LineBufferedLog(); lg.write('x\n'); lg.close()
    sem_neg('log_policy_ok', 'pre-origin part larger than the whole log', log_policy_ok(lg, lg.inode, 10), 'exceed the whole log')
    sem_neg('log_policy_ok', 'log limit changed after the freeze', patched('LOG_LIMIT_BYTES', 2 ** 40, lambda: log_policy_ok(lg, lg.inode, 0)), 'in-force log_policy')
    print('  semantic[log_policy_ok]: a pre-origin size above the log size and a log-policy constant changed after the freeze are rejected')
    # document_ok — REVIEW-9 finding 3 (a caller schema cannot be supplied; a substituted or weakened schema file is refused by its hash)
    dup = clone(base); dup['limitations'].append(dup['limitations'][0])
    v = document_ok(dup, {}); assert result_kind(v) == 'invalid-input', v                                       # positional {} lands on `mode` and is refused
    v = document_ok(dup, schema={}); assert result_kind(v) == 'invalid-input' and 'arguments' in v[1], v       # there is no schema parameter
    v = document_ok(dup); assert result_kind(v) == 'invalid-input' and 'limitations' in v[1], v              # the frozen schema rejects the duplicate
    weak = json.loads(FROZEN_SCHEMA_PATH.read_text()); weak['properties']['limitations'].pop('uniqueItems')
    assert weak['$id'] == SCHEMA_ID
    with tempfile.TemporaryDirectory(prefix='e2v10-schema-') as td:
        for label, content in (('REVIEW-9 F3: permissive schema {} substituted at the frozen path', '{}'), ('same-$id schema with uniqueItems removed substituted at the frozen path', json.dumps(weak))):
            fake = Path(td) / 'e2-g1e-outcome.schema.json'; fake.write_text(content)
            sem_neg('document_ok', label, patched('FROZEN_SCHEMA_PATH', fake, lambda: document_ok(dup)), 'substituted or weakened schema is refused')
    sem_neg('document_ok', 'synthetic example checked in production mode', document_ok(clone(base), mode='production'), 'cannot be checked as a production result')
    noex = clone(base); noex.pop('exampleOnly')
    sem_neg('document_ok', 'example mode without exampleOnly', document_ok(noex), 'requires exampleOnly')
    assert document_ok(noex, mode='production').ok                                                          # a document without exampleOnly is checkable as a result
    print('  semantic[document_ok]: document_ok(doc, {}) and document_ok(doc, schema={}) are invalid input (no schema parameter exists); a permissive {} and a same-$id weakened schema placed at the '
          'frozen path are refused by the SHA-256 check; with the frozen schema the reviewer\'s duplicate limitation is rejected; mode binding: an example in production mode and a result in example mode are rejected')
    assert all(SEM_NEG[n] for n in CHECKERS), ('every checker needs at least one semantic negative test', [n for n in CHECKERS if not SEM_NEG[n]])
    assert all(CHECKERS[n].semantic is not None for n in CHECKERS), 'every checker needs a semantic stage'
    print(f'semantic negative tests: {sum(len(v) for v in SEM_NEG.values())} over {len(CHECKERS)} checkers (' + ', '.join(f'{n} {len(SEM_NEG[n])}' for n in sorted(SEM_NEG)) + ')')

def test_generic_mutation_and_contract_table():
    # --- v9: generic mutation test per checker (assignment 9 item 3) and the generated checker/contract table (item 2) ---
    def _fresh_sim(): return SimStream(h('mutation-nonce')[:32], [5], [7])
    def _closed_log():
        lg = LineBufferedLog(); lg.write('x\n'); lg.close(); return lg
    BASELINES = {
        'log_policy_ok': lambda: {'log': _closed_log(), 'inode_at_create': _closed_log().inode, 'pre_origin_log_bytes': 0},
        'ledger_check': lambda: {'sim': _fresh_sim(), 'startup': b'startup', 'peer_payload': line('win_begin', wid='i0') + b'x\n' + line('win_end', wid='i0'),
                                 'peer_final_extra': 0, 'peer_bytes_limit': PEER_MAX_BYTES, 'truncate_log_before_close': False},
        'burst_round_latencies': lambda: {'events': [[100.0 * kk + 5.0, line('done', kk=f'{kk:02d}')] for kk in range(10)], 'send_ts': {kk: 100.0 * kk for kk in range(10)},
                                          'fixture_sha256': FROZEN['fixture_registry_sha256'], 'cell': 4, 'rounds': 10},
        'loss_delivery_ok': lambda: {'binding': {**BIND, 'variant': 'L1c'}, 'peer': data, 'pieces': [P(0, 10, data[0:10]), P(10, 20, data[10:20]), P(20, 30, data[20:30], True)],
                                     'renderer_record': data[0:30], 'taken_batch': TB(batch), 'cutoff_k': 2},
        'close_once_ok': lambda: {'route': 'L1f', 'rec': dict(L1F)},
        'resize_slurp_assertion': lambda: {'R': 950, 'stream_end': 6000, 'before_ack': [[0, 300], [400, 950]], 'after_ack': [[950, 1200], [1300, 3000]],
                                           'dropped': [[300, 400], [1200, 1300]], 'reported_dropped_bytes': 200, 'context': resize_ctx('rust-shell')},
        'resize_b2_observed': lambda: {'R': 950, 'stream_end': 6000, 'before_ack': [[0, 950]], 'context': resize_ctx('python')},
        'log_final_check': lambda: {'arm': 'rust-shell', 'sidecar_live': True, 'inventory': {'backend': False, 'sidecar': False}, 'reads': [dict(x) for x in good], 't_exit_ms': 0.0, 't_inventory_ms': 0.0},
        'document_ok': lambda: {'doc': clone(base), 'mode': 'example'},
    }
    assert set(BASELINES) == set(CHECKERS), ('every registered checker needs a mutation baseline', sorted(set(CHECKERS) ^ set(BASELINES)))
    WRONG = {bool: 1, int: 'one', float: 'one', bytes: 'not-bytes', str: 7, list: {'not': 'a list'}, tuple: {'not': 'a pair'}, dict: ['not', 'a record'], type(None): 'not-none'}
    CLASSES = ('wrong-type', 'missing-field', 'empty-string-for-list', 'duplicate-element', 'out-of-range')
    def walk(v, pre=()):
        out = [(pre, v)]
        if type(v) is dict:
            for k in list(v): out += walk(v[k], pre + (k,))
        elif type(v) is list:
            for i, e in enumerate(v): out += walk(e, pre + (i,))
        return out
    def mutations_for(name, args):
        sig = CHECKERS[name].signature; muts = []
        for path, v in walk(args)[1:]:
            top_default = len(path) == 1 and sig.parameters[path[0]].default is not inspect.Parameter.empty
            muts.append(('wrong-type', path, ('set', WRONG.get(type(v), 'not-an-object'))))
            parent = args
            for k in path[:-1]: parent = parent[k]
            if type(parent) is dict and not top_default: muts.append(('missing-field', path, ('delete',)))      # a field/argument, not a list element
            if type(v) is list:
                muts.append(('empty-string-for-list', path, ('set', '')))
                if v: muts.append(('duplicate-element', path, ('dup',)))
            if type(v) in (int, float) and type(v) is not bool: muts.append(('out-of-range', path, ('set', -1)))
            if type(v) is str: muts.append(('out-of-range', path, ('set', '')))
        return muts
    def apply(args, path, op):
        parent = args
        for k in path[:-1]: parent = parent[k]
        if op[0] == 'delete': del parent[path[-1]]
        elif op[0] == 'set': parent[path[-1]] = op[1]
        else: parent[path[-1]].insert(1, copy.deepcopy(parent[path[-1]][0]))
    summary = {}
    base_outcome = base['outcome']; base_facts = base['decision']['facts']
    rng9 = random.Random(9)
    for name in sorted(CHECKERS):
        fn = CHECKERS[name]; b0 = BASELINES[name]()
        r0 = fn(**b0); assert result_kind(r0) in ('pass', 'fail'), (name, 'baseline must be a valid shape', r0)
        muts = mutations_for(name, BASELINES[name]())
        if name == 'document_ok':                                                 # the document has thousands of positions: all top-level ones plus a seeded sample
            top = [m for m in muts if len(m[1]) == 1 or m[1][0] == 'doc' and len(m[1]) == 2]
            nested = [m for m in muts if m[1][0] == 'doc' and len(m[1]) > 2]
            muts = top + rng9.sample(nested, 150)
        counts = {c: 0 for c in CLASSES}; neutral = {c: 0 for c in CLASSES}; accepted_paths = []; kinds = {}
        for cls, path, op in muts:
            args = BASELINES[name](); apply(args, path, op)
            res = fn(**args); k = result_kind(res)
            assert k in ('invalid-input', 'invalid-semantic', 'fail', 'pass'), (name, cls, path, 'checker-error or not a result', res)
            if name != 'document_ok':
                assert k in ('invalid-input', 'invalid-semantic'), (name, cls, path, 'mutation not rejected as invalid input', res)
                kinds[k] = kinds.get(k, 0) + 1
            elif k == 'pass':                                                     # schema-valid and re-derivable: must leave the decision unchanged
                assert args['doc']['outcome'] == base_outcome and args['doc']['decision']['facts'] == base_facts, (cls, path, 'decision changed by an accepted mutation')
                neutral[cls] += 1; accepted_paths.append((cls, '/'.join(map(str, path)), repr(op[1])[:30] if op[0] == 'set' else op[0]))
            counts[cls] += 1
        summary[name] = (counts, neutral)
        na = [c for c in CLASSES if counts[c] == 0]
        print(f'  mutation[{name}]: ' + ', '.join(f'{c} {counts[c]}' + (f' ({neutral[c]} decision-neutral accepted)' if neutral[c] else '') for c in CLASSES if counts[c])
              + (f'; not applicable (no such position in the contract): {", ".join(na)}' if na else '') + ' -> ' + (f'every mutation rejected ({", ".join(f"{k} {n}" for k, n in sorted(kinds.items()))})' if name != 'document_ok' else 'every mutation rejected or decision-neutral'))
        for cls_, pth, opx in accepted_paths: print(f'    accepted, decision-neutral: {cls_} at {pth} -> {opx}')
        if name not in ('close_once_ok', 'log_policy_ok', 'ledger_check'): assert not na, (name, 'every mutation class must apply to a list-bearing contract', na)
    total = sum(sum(c.values()) for c, _ in summary.values())
    print(f'generic mutation test: {total} mutated calls over {len(CHECKERS)} registered checkers; none raised, none returned checker-error; every non-document mutation is rejected')
    rows = ['| Checker | Shape contract (exact types; known-empty vs missing) | Semantic preconditions (v10) | Semantic negative tests (v10) | On invalid input |', '|---|---|---|---|---|']
    for name in sorted(CHECKERS):
        c = CHECKERS[name]; contract = ' '.join((c.contract.__doc__ or '').split())
        on_invalid = ("`{'valid': False, 'kind': ..., 'why': ...}`" if c.invalid_result else "`Verdict(False, <reason>, kind=...)`") + ", kind `invalid-input` (shape) or `invalid-semantic`"
        sig_txt = str(c.signature).replace(" -> 'dict'", '').replace(' -> dict', ''); contract_txt = contract.replace('|', r'\|')
        sem_txt = ' '.join((c.semantic.__doc__ or '').split()).replace('|', r'\|'); neg_txt = (f'{len(SEM_NEG[name])}: ' + '; '.join(SEM_NEG[name])).replace('|', r'\|')
        rows.append(f'| `{name}{sig_txt}` | {contract_txt} | {sem_txt} | {neg_txt} | {on_invalid} |')
    table_text = '\n'.join(rows) + '\n'
    assert all(f'`{n}(' in table_text for n in CHECKERS)
    print(f'checker/contract table: {len(CHECKERS)} checkers')
    print('registry: 49 rows (20 stream, 12 lifecycle, 16 budget, 1 beat); no descendant-snapshot rows (deferred, named limitation); 21 correctness rows; 6 reader rows; 12 canaries; 1 report-only metric (resize_ack_ms)')
