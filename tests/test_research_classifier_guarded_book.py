"""Review regressions: guardian readiness and PID identity before service mutation."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

BOOK_SCRIPT='guarded'


@pytest.fixture(autouse=True,params=['guarded','final','launchd'])
def each_staged_implementation(request,monkeypatch):
    monkeypatch.setitem(globals(),'BOOK_SCRIPT',request.param)


def book():
    spec=importlib.util.spec_from_file_location('guarded_book',
        Path(__file__).parents[1]/('scripts/moa-research-book-classifier-resume8-'+BOOK_SCRIPT+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_pid_identity_uses_preserved_argv_not_shell_reparsed_ps_text(tmp_path,monkeypatch):
    m=book(); runner=tmp_path/'Application Support'/'worker.py'
    train=['/Python',str(runner),'{"action": "resume", "run_id": "same"}']
    guardian=['/Python',str(runner),'{"action": "gateway_guard", "run_id": "same"}']
    monkeypatch.setattr(m,'process_argv',lambda pid: train if pid==42 else guardian)
    assert m.model_process(42,str(runner),'resume')
    assert not m.model_process(43,str(runner),'resume')
    assert not m.model_process(42,str(runner),'evaluate')


def test_kernel_argv_parser_preserves_spaces_and_json_without_shell_quotes():
    m=book()
    args=['/Application Support/Python','/Application Support/worker.py',
          '{"action": "resume", "run_id": "same"}']
    raw=m.struct.pack('i',3)+b'/Application Support/Python\0\0\0'
    raw+=b'\0'.join(x.encode() for x in args)+b'\0ENV=value\0'
    assert m.parse_procargs(raw)==args
    assert m.parse_procargs(b'bad')==[]


def test_guardian_readiness_requires_byte_and_live_child():
    m=book();read_fd,write_fd=m.os.pipe()
    try:
        m.os.write(write_fd,b'R')
        m.await_guardian_ready(SimpleNamespace(poll=lambda:None),read_fd)
        m.os.write(write_fd,b'X')
        with pytest.raises(ValueError):m.await_guardian_ready(SimpleNamespace(poll=lambda:None),read_fd)
    finally:m.os.close(read_fd);m.os.close(write_fd)


def test_guardian_setup_failure_does_not_ack_and_retains_recovery_finally(tmp_path,monkeypatch):
    m=book(); events=[]
    lease=SimpleNamespace(path=tmp_path/'RECOVERY.json',read=lambda:dict(recovery_required=True))
    lease.path.touch()
    monkeypatch.setattr(m,'gateway_lease',lambda **kw:lease)
    monkeypatch.setattr(m,'context',lambda *_: (_ for _ in ()).throw(ValueError('setup failed')))
    monkeypatch.setattr(m,'gateway_restore',lambda:events.append('restored'))
    monkeypatch.setattr(m.os,'write',lambda *_:events.append('ready'))
    monkeypatch.setattr(m.os,'close',lambda *_:None)
    with pytest.raises(ValueError):m.gateway_guard(dict(ready_fd=9))
    assert events==['restored']


def test_guardian_ack_precedes_parent_service_changes(tmp_path,monkeypatch):
    m=book(); events=[]
    monkeypatch.setattr(m,'context',lambda *_:(None,None,{}))
    monkeypatch.setattr(m,'output',lambda:tmp_path)
    monkeypatch.setattr(m,'incoming',lambda name:tmp_path/name)
    lease=SimpleNamespace(pause=lambda on_journal:(on_journal(),events.append('services_changed')))
    monkeypatch.setattr(m,'gateway_lease',lambda **kw:lease)
    monkeypatch.setattr(m.subprocess,'Popen',lambda *a,**kw:SimpleNamespace(pid=123,poll=lambda:None))
    monkeypatch.setattr(m,'await_guardian_ready',lambda *a,**kw:events.append('ready_ack'))
    m.pause_gateways({})
    assert events==['ready_ack','services_changed']


def test_guardian_missing_ack_refuses_before_any_service_change(tmp_path,monkeypatch):
    m=book(); events=[]
    monkeypatch.setattr(m,'context',lambda *_:(None,None,{}))
    monkeypatch.setattr(m,'output',lambda:tmp_path)
    monkeypatch.setattr(m,'incoming',lambda name:tmp_path/name)
    def pause(on_journal):on_journal();events.append('services_changed')
    monkeypatch.setattr(m,'gateway_lease',lambda **kw:SimpleNamespace(pause=pause))
    monkeypatch.setattr(m.subprocess,'Popen',lambda *a,**kw:SimpleNamespace(pid=123,poll=lambda:None))
    monkeypatch.setattr(m,'await_guardian_ready',lambda *a,**kw:(_ for _ in ()).throw(ValueError('no ack')))
    with pytest.raises(ValueError):m.pause_gateways({})
    assert events==[]


def test_runtime_gateway_check_never_performs_health_http(tmp_path,monkeypatch):
    m=book(); got=[]
    path=tmp_path/'RECOVERY.json';path.touch()
    def lease(**kw):
        got.append(kw)
        return SimpleNamespace(path=path,read=lambda:dict(recovery_required=True),
            inspect=lambda:{'x':dict(disabled=True,registered=False)})
    monkeypatch.setattr(m,'gateway_lease',lease)
    assert m.gateways_held()
    assert got==[dict(health_checks=False)]
