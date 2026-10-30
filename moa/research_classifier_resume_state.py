"""Strict restoration of the explicitly authorised, zero-update head boundary.

Imports no Torch or model on the controller. The Book calls this with its
existing Torch runtime; head, optimiser, scheduler and all RNGs are restored.
"""
import random


def restore_zero_state(torch, load_head, head_state, optimizer, scheduler, state, head, set_mps_rng):
    required = {'head', 'optimizer', 'scheduler', 'updates', 'history',
                'torch_rng', 'mps_rng', 'python_rng'}
    if (set(state) != required or state['updates'] != 0 or state['history'] != [] or
            state['scheduler']['last_epoch'] != 0 or state['scheduler']['T_max'] != 56 or
            state['scheduler']['eta_min'] != .000001 or state['optimizer']['state'] or
            set(head) != set(state['head']) or
            any(not torch.equal(head[n], state['head'][n]) or
                not torch.isfinite(head[n]).all() for n in head)):
        raise ValueError('only the intact saved zero-update boundary may resume')
    groups = state['optimizer']['param_groups']
    if (len(groups) != 1 or groups[0]['lr'] != .001 or groups[0]['weight_decay'] != .01 or
            len(groups[0]['params']) != len(optimizer.param_groups[0]['params'])):
        raise ValueError('saved optimizer configuration differs')
    load_head(head)
    actual = head_state()
    if set(actual) != set(head) or any(not torch.equal(actual[n].cpu(), head[n]) for n in head):
        raise ValueError('restored head is not the actual saved head')
    optimizer.load_state_dict(state['optimizer'])
    scheduler.load_state_dict(state['scheduler'])
    torch.set_rng_state(state['torch_rng'])
    set_mps_rng(state['mps_rng'])
    random.setstate(state['python_rng'])
