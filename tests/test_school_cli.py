"""No NAS/production paths. Administrative configuration uses synthetic JSON."""
import importlib
import importlib.util
import json

import pytest

from moa.core import Store


def test_explicit_cli_migration_configuration_and_fixture_receiving(tmp_path, capsys):
    assert importlib.util.find_spec('moa.school_cli') is not None, 'School CLI is missing'
    cli = importlib.import_module('moa.school_cli')
    root = tmp_path/'isolated'
    config = tmp_path/'configuration.json'
    config.write_text(json.dumps({'schools': [{'code':'A','name':'합성 A학교',
        'members':[{'subject':'teacher','role':'uploader'}],
        'sources':[{'id':'fixture','kind':'test_adapter','allowed_url':'https://a.example.test/notices/'}]}]}))
    assert cli.main(['--data',str(root),'migrate']) == 0
    migration_output = capsys.readouterr().out
    with Store(root) as s:
        version = s.db.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]
    assert version == 13 and migration_output.startswith('school_schema=13; backup=')
    assert 'already_applied' not in migration_output
    assert cli.main(['--data',str(root),'configure','--config',str(config)]) == 0
    fixture = tmp_path/'source-fixture.json'
    fixture.write_text(json.dumps({'posts': [{'key':'1','title':'합성','body_html':'<p>본문 12,000원</p>',
        'source_url':'https://a.example.test/notices/1'}], 'cursor':'done'}))
    assert cli.main(['--data',str(root),'receive-fixture','--school','A','--subject','teacher','--source','fixture','--fixture',str(fixture)]) == 0
    with Store(root) as s:
        assert s.db.execute('SELECT count(*) FROM school_service_versions').fetchone()[0] == 1
        assert s.db.execute('SELECT count(*) FROM notices').fetchone()[0] == 0
        assert s.db.execute('SELECT count(*) FROM usage').fetchone()[0] == 0


def test_cli_repeat_migration_reports_installed_version_without_new_backup(tmp_path, capsys):
    from moa.school_cli import main
    root = tmp_path/'isolated'
    assert main(['--data',str(root),'migrate']) == 0
    capsys.readouterr()
    with Store(root) as s:
        before = list(s.db.iterdump())
    backups = sorted((root/'db'/'backups').glob('*.sqlite3'))
    assert main(['--data',str(root),'migrate']) == 0
    assert capsys.readouterr().out == 'school_schema=13; backup=already_applied\n'
    assert sorted((root/'db'/'backups').glob('*.sqlite3')) == backups
    with Store(root) as s:
        assert list(s.db.iterdump()) == before


def test_cli_unknown_schema_does_not_report_migration_success(tmp_path, capsys):
    from moa.school_cli import main
    root = tmp_path/'isolated'
    assert main(['--data',str(root),'migrate']) == 0
    capsys.readouterr()
    with Store(root) as s:
        s.db.execute("INSERT INTO school_service_migrations VALUES(14,'synthetic-future')")
        s.db.commit()
        before = list(s.db.iterdump())
    with pytest.raises(RuntimeError, match='Unknown school schema'):
        main(['--data',str(root),'migrate'])
    assert capsys.readouterr().out == ''
    with Store(root) as s:
        assert list(s.db.iterdump()) == before
