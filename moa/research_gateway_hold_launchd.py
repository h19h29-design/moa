"""Reversible hold of exactly two approved Book LaunchAgents, not their watchdog."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

GATEWAYS = {
    'ai.openclaw.gateway': 'c9a8402b54e092331f281993a4f6c5bc367572ea6311db072a68ee84b7889f19',
    'ai.openclaw.h19h19': 'cf0945ddb483d7080eee0d1f85bf40b81c08d7ed216f7c2655441b044bd4c4fc'}
PORTS = {'ai.openclaw.gateway': {18789, 18791}, 'ai.openclaw.h19h19': {18790, 18792}}
PLISTS = Path('/Users/jungwoohwang/Library/LaunchAgents')


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def healthy(name, pid):
    p = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid), '-iTCP', '-sTCP:LISTEN', '-Fn'],
                       capture_output=True, text=True)
    actual = {int(x) for x in re.findall(r'^n(?:127\.0\.0\.1|\[::1\]):(\d+)$', p.stdout, re.M)}
    if not PORTS[name].issubset(actual): return False
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/' % min(PORTS[name]), timeout=3) as r:
            return r.status == 200
    except OSError: return False


class GatewayHold:
    def __init__(self, path, *, run=subprocess.run, sha=digest, health=healthy, wait=time.sleep):
        self.path=Path(path); self.run=run; self.sha=sha; self.health=health; self.wait=wait

    def command(self, *args):
        return self.run(['/bin/launchctl', *args], capture_output=True, text=True, timeout=15)

    def read(self): return json.loads(self.path.read_bytes())

    def write(self, value, *, once=False):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target=self.path if once else self.path.with_suffix('.tmp')
        with target.open('x' if once else 'w') as f:
            os.chmod(target,0o600); json.dump(value,f,sort_keys=True); f.flush(); os.fsync(f.fileno())
        if not once: os.replace(target,self.path)

    def inspect(self):
        disabled=self.command('print-disabled','gui/501')
        if disabled.returncode: raise ValueError('cannot inspect original service enablement')
        out={}
        for name,wanted in GATEWAYS.items():
            if self.sha(PLISTS/(name+'.plist')) != wanted: raise ValueError('gateway plist changed')
            found=re.search(r'"'+re.escape(name)+r'"\s*=>\s*([^\s,;]+)',disabled.stdout)
            if found and found[1] not in ('true','false','enabled','disabled'):
                raise ValueError('unknown gateway enablement token')
            p=self.command('print','gui/501/'+name)
            pid=re.search(r'^\s*pid = (\d+)',p.stdout,re.M)
            running=p.returncode==0 and bool(re.search(r'^\s*state = running\s*$',p.stdout,re.M))
            out[name]=dict(disabled=bool(found and found[1] in ('true','disabled')),registered=p.returncode==0,
                running=running,pid=int(pid[1]) if pid else None,
                healthy=bool(running and pid and self.health(name,int(pid[1]))),plist_sha256=wanted)
        return out

    def pause(self, *, on_journal=None):
        if self.path.exists():
            prior=self.read()
            if prior['status']=='PAUSED': return prior
            raise ValueError('gateway hold already used; do not rearm')
        before=self.inspect()
        if any(s['disabled'] or not s['registered'] or not s['running'] or not s['healthy'] for s in before.values()):
            raise ValueError('both original services must be enabled and healthy')
        state=dict(status='PREPARING',recovery_required=True,original=before,started_at=time.time())
        self.write(state,once=True)
        try:
            if on_journal: on_journal()
            for name in GATEWAYS:
                if self.command('disable','gui/501/'+name).returncode: raise ValueError('gateway disable failed')
            for name in GATEWAYS: self.command('bootout','gui/501/'+name)
            after=self.inspect()
            if any(not s['disabled'] or s['registered'] for s in after.values()):
                raise ValueError('gateway hold failed')
            state.update(status='PAUSED',held=after); self.write(state)
            return state
        except BaseException:
            self.restore(); raise

    def restore(self):
        state=self.read()
        if set(state['original'])!=set(GATEWAYS) or any(s['disabled'] for s in state['original'].values()):
            raise ValueError('unexpected original state, no broad restore')
        state.update(status='RECOVERY_REQUIRED',recovery_required=True); self.write(state)
        before=self.inspect()
        for name in GATEWAYS:
            if before[name]['disabled'] and self.command('enable','gui/501/'+name).returncode:
                raise ValueError('gateway re-enable failed')
            if not before[name]['registered']:
                self.command('bootstrap','gui/501',str(PLISTS/(name+'.plist')))
            elif not before[name]['running']:
                self.command('kickstart','gui/501/'+name)
        for _ in range(20):
            after=self.inspect()
            if all(not s['disabled'] and s['running'] and s['healthy'] for s in after.values()):
                state.update(status='RESTORED_VERIFIED',recovery_required=False,restored=after,restored_at=time.time())
                self.write(state); return state
            self.wait(1)
        raise ValueError('gateway health not restored; recovery obligation retained')
