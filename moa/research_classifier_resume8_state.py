"""Restore the actual saved eight-update boundary; never replay completed windows."""
import math
import random


def remaining_windows(rows, updates):
    if updates != 8 or len(rows) != 108:
        raise ValueError('only the explicitly approved eight-update boundary and train108')
    index = 0
    for epoch in range(4):
        ordered = list(rows); random.Random(20261020 + epoch).shuffle(ordered)
        for offset in range(0, 108, 8):
            index += 1
            if index > updates: yield epoch, offset, ordered[offset:offset+8]


def restore_eight_state(torch, load_head, head_state, optimizer, scheduler, state, head, set_mps_rng):
    required = {'head', 'optimizer', 'scheduler', 'updates', 'history',
                'torch_rng', 'mps_rng', 'python_rng'}
    if (set(state) != required or state['updates'] != 8 or len(state['history']) != 8 or
            [x['step'] for x in state['history']] != list(range(1, 9)) or
            any(not math.isfinite(x['loss']) or not math.isfinite(x['gradient_norm']) for x in state['history']) or
            state['scheduler']['last_epoch'] != 8 or state['scheduler']['T_max'] != 56 or
            state['scheduler']['eta_min'] != .000001 or not state['optimizer']['state'] or
            set(head) != set(state['head']) or
            any(not torch.equal(head[n], state['head'][n]) or not torch.isfinite(head[n]).all() for n in head)):
        raise ValueError('intact saved eight-update state required')
    groups = state['optimizer']['param_groups']
    wanted_lr = .000001 + (.001 - .000001) * (1 + math.cos(math.pi * 8 / 56)) / 2
    if (len(groups) != 1 or not math.isclose(groups[0]['lr'], wanted_lr, rel_tol=1e-12) or
            groups[0]['weight_decay'] != .01 or groups[0].get('initial_lr') != .001 or
            len(groups[0]['params']) != len(optimizer.param_groups[0]['params']) or
            set(state['optimizer']['state']) != set(groups[0]['params'])):
        raise ValueError('saved optimizer configuration differs')
    for value in state['optimizer']['state'].values():
        if (set(value) != {'step', 'exp_avg', 'exp_avg_sq'} or float(value['step']) != 8 or
                not torch.isfinite(value['exp_avg']).all() or not torch.isfinite(value['exp_avg_sq']).all()):
            raise ValueError('saved Adam state must be complete at step eight')
    load_head(head)
    actual = head_state()
    if set(actual) != set(head) or any(not torch.equal(actual[n].cpu(), head[n]) for n in head):
        raise ValueError('restored head differs')
    optimizer.load_state_dict(state['optimizer']); scheduler.load_state_dict(state['scheduler'])
    torch.set_rng_state(state['torch_rng']); set_mps_rng(state['mps_rng']); random.setstate(state['python_rng'])
