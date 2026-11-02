"""Port health checks must request stable numeric loopback endpoints on Darwin."""
import importlib
import pytest
from types import SimpleNamespace


def test_health_requests_numeric_host_and_port_without_touching_services(monkeypatch):
    m=importlib.import_module('moa.research_gateway_hold_launchd')
    calls=[]
    def run(cmd,**kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0,stdout='n127.0.0.1:18789\nn[::1]:18789\nn127.0.0.1:18791\n' if '-nP' in cmd else 'nlocalhost:18789\n')
    class HTTP:
        status=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr(m.subprocess,'run',run)
    monkeypatch.setattr(m.urllib.request,'urlopen',lambda *a,**kw:HTTP())
    assert m.healthy('ai.openclaw.gateway',42)
    assert calls==[['/usr/sbin/lsof','-nP','-a','-p','42','-iTCP','-sTCP:LISTEN','-Fn']]


@pytest.mark.parametrize('token,disabled',[('disabled',True),('enabled',False),('true',True),('false',False)])
def test_actual_launchctl_enablement_tokens_are_not_silently_treated_as_enabled(token,disabled):
    m=importlib.import_module('moa.research_gateway_hold_launchd')
    state='disabled services = {\n "ai.openclaw.gateway" => '+token+'\n}'
    m.subprocess  # The existing inspector must parse the exact native response.
    def run(cmd,**kw):
        if cmd[1]=='print-disabled':return SimpleNamespace(returncode=0,stdout=state)
        return SimpleNamespace(returncode=1,stdout='')
    x=m.GatewayHold('/not-used',run=run,sha=lambda p:m.GATEWAYS[p.stem],health=lambda *a:True)
    assert x.inspect()['ai.openclaw.gateway']['disabled'] is disabled
