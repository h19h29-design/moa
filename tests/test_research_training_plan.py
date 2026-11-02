"""The fixed research runner must execute the settings it claims to use."""
import copy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from moa import r2_research as r


def config():
    value = r.phase_config('r2a')
    return {k:v for k,v in value.items() if k not in ('parent','questions')}


def api():
    plan = getattr(r,'training_plan',None)
    assert callable(plan), 'derive scheduling from actual capped optimizer windows'
    return plan


def test_schedule_uses_real_items_epochs_and_accumulation_not_the_update_ceiling():
    cfg = dict(config(),epochs=4,maximum_updates=120,planned_updates=120)
    original = copy.deepcopy(cfg)
    plan = api()(cfg,144)
    assert plan['optimizer_updates'] == 72
    assert plan['configuration']['planned_updates'] == 72
    assert plan['configuration']['maximum_updates'] == 120
    assert plan['configuration']['maximum_seconds'] == 2700
    assert cfg == original
    changed={k for k in cfg if cfg[k]!=plan['configuration'][k]}
    assert changed == {'planned_updates'}


def test_partial_windows_and_early_update_caps_never_add_epochs_or_duplicate_data():
    plan = api()(dict(config(),epochs=2,maximum_updates=5),9)
    assert plan['optimizer_updates'] == 4
    assert plan['configuration']['epochs'] == 2
    assert plan['items_per_epoch'] == 9
    assert api()(dict(config(),epochs=10,maximum_updates=5),9)['optimizer_updates'] == 5


@pytest.mark.parametrize('override', [
    {'loss':'rlcd'}, {'micro_batch':8}, {'freeze_encoder':False},
    {'amp':True}, {'gradient_checkpointing':True}, {'shuffle_options':[]},
    {'max_len':512}, {'head_max_len':128}, {'head_lr':float('nan')},
    {'unknown_training_knob':1}, {'maximum_updates':401}, {'maximum_seconds':2701},
])
def test_fixed_runner_rejects_ignored_or_unsupported_settings(override):
    with pytest.raises(ValueError): api()(dict(config(),**override),144)


def test_sampling_count_tracks_the_actual_existing_balanced_sampler():
    count = getattr(r,'training_item_count',None)
    assert callable(count), 'publisher and worker must agree after optional resampling'
    rows=[{'expected':{'q':label}} for label in ['preserved','changed','changed','insufficient']]
    assert count(rows,None) == 4
    assert count(rows,'balanced_oversample') == 6
    with pytest.raises(ValueError): count(rows,'unknown')


def load_publisher():
    path=Path(__file__).parents[1]/'scripts/moa-research-publish.py'
    spec=importlib.util.spec_from_file_location('training_plan_publisher',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_publisher_resolves_after_the_explicit_sampler_and_keeps_all_caps():
    prepare=getattr(load_publisher(),'configuration_for',None)
    assert callable(prepare), 'publish the real scheduler horizon, not maximum_updates'
    rows=[{'expected':{'q':label}} for label in
          ['preserved','changed','changed','insufficient']*36]
    args=SimpleNamespace(epochs=4,max_updates=120,max_seconds=1800,loss='soft-ce',
                         head_lr=.001,seed=20261020,balanced=False)
    cfg=prepare(args,rows)
    assert cfg['planned_updates']==72 and cfg['maximum_updates']==120
    args.balanced=True
    assert prepare(args,rows)['planned_updates']==108


def test_paired_publisher_uses_the_same_actual_horizon():
    from moa import research_alignment as a
    prepare=getattr(a,'paired_configuration',None)
    assert callable(prepare), 'paired experiment must also resolve optimizer windows'
    cfg=prepare(144)
    assert cfg['planned_updates']==72 and cfg['maximum_updates']==120
    assert cfg['shuffle_options']==['choice']


def test_republishing_a_stage_cannot_overwrite_an_existing_job(tmp_path,monkeypatch):
    pub=load_publisher();monkeypatch.setattr(pub,'CAMPAIGN',tmp_path)
    stage=tmp_path/'stage/existing';stage.mkdir(parents=True)
    (stage/'JOB.json').write_bytes(b'preserve existing job')
    with pytest.raises(FileExistsError,match='immutable'):
        pub.run(SimpleNamespace(run_id='existing'))
    assert (stage/'JOB.json').read_bytes()==b'preserve existing job'


def test_export_records_actual_research_steps_not_inherited_pretraining_counts():
    prepare=getattr(r,'checkpoint_training_config',None)
    assert callable(prepare), 'saved model metadata must describe this actual run'
    original={'encoder':'local','training':{'updates':15987,'hours':4.97}}
    value=prepare(original,run_id='research-test',updates=72,
                  cursor={'epoch':4,'next_microbatch':0},contract_sha256='a'*64)
    assert value['training']['updates']==72 and value['training']['epochs_completed']==4
    assert value['training']['training_contract_sha256']=='a'*64
    assert value['parent_training_metadata']=={'updates':15987,'hours':4.97}
    assert original=={'encoder':'local','training':{'updates':15987,'hours':4.97}}
