"""Immutable recovery agreement helpers. No model, optimizer, network or DB I/O."""
from collections import Counter
import ast
import hashlib
import json
import re

LABELS = ('preserved', 'changed', 'insufficient')
INPUT_REVISION = 'moa.laya.runtime-input.v1'
RECOVERY_ID = 'moa-laya-recovery-20261018-v1'


def sha(value, *, ordered=False):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=not ordered,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def model_input(row):
    # Gold and source/mutation metadata never enter this object.
    return {'state': row['state'], 'questions': row['questions']}


def predict_encoding_overrides(source):
    """Read the installed worker's actual predict-call literals without executing it."""
    calls=[n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.Call)
           and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name)
           and n.func.value.id=='agent' and n.func.attr=='predict']
    if len(calls)!=1: raise ValueError('runtime_predict_call_ambiguous')
    out={}
    for keyword in calls[0].keywords:
        if keyword.arg is None: raise ValueError('runtime_encoding_not_literal')
        if keyword.arg not in ('max_len','head_max_len'): continue
        try: value=ast.literal_eval(keyword.value)
        except (ValueError,TypeError): raise ValueError('runtime_encoding_not_literal') from None
        if type(value) is not int or value<1: raise ValueError('runtime_encoding_not_literal')
        out[keyword.arg]=value
    return out


def encoding_settings(checkpoint, training, overrides, *, parallel):
    if parallel: raise ValueError('unsupported_parallel_training')
    effective={k:overrides.get(k,checkpoint.get(k)) for k in ('max_len','head_max_len')}
    if any(type(v) is not int or v<1 or training.get(k)!=v for k,v in effective.items()):
        raise ValueError('encoding_config_mismatch')
    return {**effective,'parallel_options':False}


def record_token_input(ledger, tensors, gold, split):
    """Key by actual per-question encoder tensors, never by source JSON or gold."""
    if gold not in LABELS: raise ValueError('label_mapping')
    key=sha(tensors)
    if key in ledger:
        old_gold,old_split=ledger[key]
        if old_gold!=gold: raise ValueError('token_input_contradictory_gold')
        if old_split!=split: raise ValueError('token_input_split_leakage')
    ledger[key]=(gold,split)
    return key


def build_manifest(rows, *, bundle_sha256, base_sha256, previous_sha256):
    """Bind existing records and semantic gold, preserving insertion order fingerprints."""
    for value in (bundle_sha256, base_sha256, previous_sha256):
        if not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('checkpoint_or_bundle_hash')
    ledgers = {k:{} for k in ('original_group_id','family_group_id','source_sha256')}
    effective, splits = {}, {s:[] for s in ('train','calibration','validation')}
    for row in rows:
        m=row['meta']; split=m['split']
        if split not in splits: raise ValueError('split_forbidden')
        if (m.get('label_origin') not in ('deterministic_oracle','human_independent') or
                m.get('forbidden_lineage') != [] or m.get('muse_derived') is not False or
                m.get('rights_status') != 'allowed' or not m.get('rights_evidence') or
                not m.get('lineage') or any(x not in ('original_source','deterministic_transform','independent_human') for x in m['lineage'])):
            raise ValueError('label_provenance')
        if any(k in row['state'] for k in ('expected','gold','mutation','label','split','meta')):
            raise ValueError('state_label_leakage')
        if set(row['expected']) != set(row['questions']) or not row['questions']:
            raise ValueError('label_mapping')
        for key,q in row['questions'].items():
            if (set(q.get('criteria',{})) != set(LABELS) or
                    row['expected'][key] not in LABELS): raise ValueError('label_mapping')
            if list(q['criteria'])!=list(LABELS): raise ValueError('option_order')
        for field,ledger in ledgers.items():
            identity=m[field]
            if identity in ledger and ledger[identity] != split: raise ValueError('group_leakage')
            ledger[identity]=split
        fingerprint=sha(model_input(row))
        gold=sha(row['expected'])
        if fingerprint in effective:
            old_split,old_gold=effective[fingerprint]
            if old_gold != gold: raise ValueError('contradictory_gold')
            if old_split != split: raise ValueError('effective_input_split_leakage')
        effective[fingerprint]=(split,gold)
        splits[split].append(row)
    counts={s:sum(len(r['questions']) for r in selected) for s,selected in splits.items()}
    if counts != {'train':600,'calibration':200,'validation':200}:
        raise ValueError('existing_split_counts_changed')
    summaries={}
    for split,selected in splits.items():
        summaries[split]={
            'records':len(selected),'questions':counts[split],
            'role': {'train':'training','calibration':'development_and_optional_calibration',
                     'validation':'previously_observed_regression_holdout'}[split],
            'records_sha256':sha(selected),
            'model_input_sha256':sha([model_input(r) for r in selected],ordered=True),
            'gold_sha256':sha([r['expected'] for r in selected]),
            'class_counts':dict(Counter(v for r in selected for v in r['expected'].values())),
            'mutation_counts':dict(Counter(r['meta']['mutation']['operation'] for r in selected)),
            'original_groups':sorted({r['meta']['original_group_id'] for r in selected}),
            'family_groups':sorted({r['meta']['family_group_id'] for r in selected}),
        }
    return {'manifest_version':'moa.laya.recovery.v1','recovery_id':RECOVERY_ID,
            'run_ids':{'S1':RECOVERY_ID+'-s1','R1':RECOVERY_ID+'-r1'},
            'training_executor':'macbook_codex_only','status':'ready_for_book_preflight',
            'bundle_sha256':bundle_sha256,'base_checkpoint_sha256':base_sha256,
            'previous_checkpoint_sha256':previous_sha256,'model_input_revision':INPUT_REVISION,
            'model_input_fingerprint_spec':'sha256-ordered-json-utf8-compact-v1',
            'transport_hash_is_not_model_input_hash':True,'splits':summaries,
            'label_order':list(LABELS),'label_to_index':{k:i for i,k in enumerate(LABELS)},
            'gold_encoding':'semantic-label-id; recompute target index after any option permutation',
            'preprocessing':'fresh-items-per-process; reject persisted items without exact input/tokenizer/version hashes',
            'budgets':{'S1':{'maximum_questions':24,'maximum_updates':40,'maximum_seconds':600},
                       'R1':{'maximum_candidates':1,'maximum_updates':120,'maximum_seconds':2700},
                       'training_total':{'maximum_updates':160,'maximum_seconds':3300},
                       'evaluation':{'maximum_slice_seconds':600,'maximum_total_seconds':1800}},
            'first_limit_wins':True,'encoder_frozen':True,'s1_is_not_r1_start':True,
            'checkpoint_interval':{'optimizer_updates':20,'seconds':300},
            'evaluation_after_save_in_separate_process':True,'automatic_retry_allowed':False,
            'paid_models_allowed':False,'operational_promotion_allowed':False,
            'human_approval_created':False,'auto_publish_enabled':False,
            'fresh_final_evaluation':'not_supplied; existing holdout is already observed'}
