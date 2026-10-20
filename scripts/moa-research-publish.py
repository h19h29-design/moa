"""Publish one research run to the MacBook and collect its raw dev answers.

Mini-side orchestration only: builds the pinned JOB.json, copies the train/dev
shards (never blind), runs the pinned worker actions, and writes receipts under
the private campaign folder. No operational system is touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moa import research_runner as rr  # noqa: E402
from moa.worker_handoff import questions  # noqa: E402

CAMPAIGN = Path('/Volumes/KIOXIA-2TB/Cache/MOA/unattended-20261020')
REMOTE = '/Users/jungwoohwang/Library/Application Support/MOA-LAYA/incoming'

PARENT = 'candidates/moa-laya-recovery-20261018-v1-r1/checkpoint'
PARENT_FILES = {
    'RECOVERY_METADATA.json': 'a0d5e2114e31955dc732cf83e47d638551d5828214a1bbf9e1e8d576dd19fa59',
    'encoder/config.json': '8883c64931d72df518bc39b0a883f90123d5390a65037b29569ca615b9b5ff61',
    'model.safetensors': '13b5e9fa365754bb043cbf725879b76ed0fbca900ca381df10067cd502fa123e',
    'rl_agent_config.json': '192dc47113009030ce6a653171e99091a3c08159d89f83633edfd7df9ec9837d',
    'tokenizer/special_tokens_map.json': '501906df38f65ffa6f7b77b8650448e1b95f2cc6eded074fec6213bf8c951be4',
    'tokenizer/tokenizer.json': 'dbba919c6e5e492bc22beb80abb64e6fcc7e8744dc696e8388b8c977767afa4b',
    'tokenizer/tokenizer_config.json': 'fbde3483bdffa2d869c7e8d45950efd8f1389ca986b604a38a0cef0b79b2c889',
    'training-state.pt': 'cdc5f3bf7a1c791e5d3d9908868e0c63923dda25529afa87433000c1dfe6c767',
}
PROTECTED = {
    'MODEL_REGISTRY.json': '491098af82a3ad377d2afe1938b725950539aa0ea8009aa2e99bba9c4aea76f5',
    'MODEL_MANIFEST.json': '01227d3ca6db94b1e89e42f5ecfea729b086d4705169b3fc1bde5e88dc8fd4e3',
}
INSTALLED = {
    'workers.laya_local.ml': '2eb3b845e3c8711a6268a68919a9d0fe6cd35691d90426d26c90f1fafb63d4d6',
    'workers.laya_local.policy': 'df79b0b32069dc75c78e9b46cb17231db8f3cbc78fc7a4d5ca6a841c33bcd1f4',
    'workers.laya_local.runtime': 'fafa5e7abd5ff4bd9aa7c207d8d63c808664b7c6c61b112e780a9ccac0dc72a9',
    'laya.train': 'c325941f275a90b486d00c3ca28dc4ee1ece795218fdaa7c67818d711ee79645',
}


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args):
    root = Path(__file__).resolve().parents[1]
    stage = CAMPAIGN / 'stage' / args.run_id
    stage.mkdir(parents=True, exist_ok=True, mode=0o700)
    bundle_sha = {}
    for split in ('train', 'dev'):
        name = '%s-000.jsonl' % split
        data = CAMPAIGN / 'data' / (split + '.jsonl')
        (stage / name).write_bytes(data.read_bytes())
        bundle_sha[name] = file_hash(stage / name)
    for extra in ('DATASET_SUMMARY.json',):
        (stage / extra).write_bytes((CAMPAIGN / 'data' / extra).read_bytes())
        bundle_sha[extra] = file_hash(stage / extra)
    # The runner and its pinned helper travel with every run so a fresh run id
    # never depends on files left over from an earlier evidence folder.
    (stage / 'moa-research-worker.py').write_bytes((root / 'scripts/moa-research-worker.py').read_bytes())
    (stage / 'r2_research.py').write_bytes((root / 'moa/r2_research.py').read_bytes())
    configuration = {'epochs': args.epochs, 'maximum_updates': args.max_updates,
                     'planned_updates': args.max_updates, 'maximum_seconds': args.max_seconds,
                     'micro_batch': 1, 'grad_accum': 8, 'freeze_encoder': True,
                     'loss': args.loss, 'head_lr': args.head_lr, 'min_lr': 1e-06,
                     'weight_decay': 0.01, 'grad_clip': 1.0, 'seed': args.seed,
                     'max_len': 1024, 'head_max_len': 256, 'amp': False,
                     'gradient_checkpointing': False, 'shuffle_options': ['choice']}
    job = rr.job_payload(run_id=args.run_id, bundle='incoming/' + args.run_id,
                         bundle_sha256=bundle_sha, configuration=configuration,
                         parent=PARENT, parent_files=PARENT_FILES,
                         protected_models=PROTECTED,
                         runner_sha=file_hash(root / 'scripts/moa-research-worker.py'),
                         library='r2_research.py',
                         library_sha=file_hash(root / 'moa/r2_research.py'),
                         installed=INSTALLED, questions=questions(),
                         class_prior=({'preserved': args.prior_preserved,
                                       'changed': args.prior_changed,
                                       'insufficient': args.prior_insufficient}
                                      if args.balanced else None))
    (stage / 'JOB.json').write_bytes(json.dumps(job, ensure_ascii=False, indent=1).encode())
    files = list(bundle_sha) + ['JOB.json', 'moa-research-worker.py', 'r2_research.py']
    subprocess.run(['ssh', 'jungwoohwang@192.168.1.71',
                    'mkdir -p "%s/%s" && chmod 700 "%s/%s"' % (REMOTE, args.run_id,
                                                              REMOTE, args.run_id)],
                   check=True)
    tar = subprocess.Popen(['tar', 'cf', '-'] + files, cwd=str(stage), stdout=subprocess.PIPE)
    subprocess.run(['ssh', 'jungwoohwang@192.168.1.71',
                    'cd "%s/%s" && tar xf - && chmod 600 *' % (REMOTE, args.run_id)],
                   stdin=tar.stdout, check=True)
    tar.wait()
    out = rr.ssh_capture(rr.remote_argv(rr.DEFAULT_HOST, ['inspect', '--job', 'JOB.json'],
                                        run_id=args.run_id), timeout=180)
    print(json.dumps({'inspect': out}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--epochs', type=int, default=2)
    parser.add_argument('--max-updates', type=int, default=90)
    parser.add_argument('--max-seconds', type=int, default=1800)
    parser.add_argument('--loss', default='soft-ce')
    parser.add_argument('--seed', type=int, default=20261020)
    parser.add_argument('--head-lr', type=float, default=0.0003,
                        help='E4: the tiny-overfit diagnostic converged at 1e-3, not 3e-4')
    parser.add_argument('--balanced', action='store_true',
                        help='E4: reweight the target prior so normal rows are not drowned out')
    parser.add_argument('--prior-preserved', type=float, default=2.25)
    parser.add_argument('--prior-changed', type=float, default=1.0)
    parser.add_argument('--prior-insufficient', type=float, default=3.0)
    run(parser.parse_args())


if __name__ == '__main__':
    main()
