"""Read-only, small result channel. Hash checkpoint files locally; never send weights."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path('/Users/jungwoohwang/Library/Application Support/MOA-LAYA')
RUN = 'moa-unattended-20261021-binary-train-01'
LIMIT = 2 * 1024 * 1024


def confined(root, name):
    name = Path(name)
    path = root / name
    if (name.is_absolute() or '..' in name.parts or path.is_symlink() or
            not path.resolve().is_relative_to(root.resolve()) or not path.is_file()):
        raise ValueError('invalid saved result path')
    # Reject intermediate symlinks too, even those resolving inside the run.
    if any((root / p).is_symlink() for p in path.relative_to(root).parents if p != Path('.')):
        raise ValueError('saved result directory symlink')
    return path


def metadata(path):
    if path.stat().st_size > LIMIT:
        raise ValueError('result metadata size limit')
    raw = path.read_bytes()
    if len(raw) > LIMIT:
        raise ValueError('result metadata grew beyond limit')
    return raw, dict(sha256=hashlib.sha256(raw).hexdigest(),
                    base64=base64.b64encode(raw).decode('ascii'))


def collect():
    run = ROOT / 'research' / RUN
    if run.is_symlink() or not run.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('invalid saved run')
    result_path = confined(run, 'TRAIN_RESULT.json')
    raw, result = metadata(result_path)
    saved = json.loads(raw)
    name = Path(saved['checkpoint_name'])
    if name.is_absolute() or len(name.parts) != 1 or name.name in ('.', '..'):
        raise ValueError('invalid checkpoint name')
    cp = run / name
    if cp.is_symlink() or not cp.is_dir() or cp.resolve().parent != run.resolve():
        raise ValueError('invalid checkpoint directory')
    files = {'TRAIN_RESULT.json': result,
             'TRAINING_CONTRACT.json': metadata(confined(run, 'TRAINING_CONTRACT.json'))[1]}
    if (run / 'EVALUATION.json').exists():
        files['EVALUATION.json'] = metadata(confined(run, 'EVALUATION.json'))[1]
    checkpoint = {}
    for name, expected in saved['sha256'].items():
        path = confined(cp, name)
        stat = path.stat()
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                digest.update(block)
        after = path.stat()
        if ((stat.st_ino, stat.st_size, stat.st_mtime_ns) !=
                (after.st_ino, after.st_size, after.st_mtime_ns) or digest.hexdigest() != expected):
            raise ValueError('saved checkpoint changed or hash mismatch')
        checkpoint[name] = dict(sha256=digest.hexdigest(), bytes=after.st_size)
    if result_path.read_bytes() != raw:
        raise ValueError('training receipt changed during collection')
    return dict(run_id=RUN, checkpoint_name=cp.name, checkpoint=checkpoint, files=files)


if __name__ == '__main__':
    # No action argument, upload, process launch, model load or training endpoint.
    print(json.dumps(collect(), separators=(',', ':'), allow_nan=False))
