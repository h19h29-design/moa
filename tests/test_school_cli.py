"""No NAS/production paths. Administrative configuration uses synthetic JSON."""
import importlib
import importlib.util
import json

from moa.core import Store


def test_explicit_cli_migration_configuration_and_fixture_receiving(tmp_path):
    assert importlib.util.find_spec('moa.school_cli') is not None, 'School CLI is missing'
    cli = importlib.import_module('moa.school_cli')
    root = tmp_path/'isolated'
    config = tmp_path/'configuration.json'
    config.write_text(json.dumps({'schools': [{'code':'A','name':'합성 A학교',
        'members':[{'subject':'teacher','role':'uploader'}],
        'sources':[{'id':'fixture','kind':'test_adapter','allowed_url':'https://a.example.test/notices/'}]}]}))
    assert cli.main(['--data',str(root),'migrate']) == 0
    assert cli.main(['--data',str(root),'configure','--config',str(config)]) == 0
    fixture = tmp_path/'source-fixture.json'
    fixture.write_text(json.dumps({'posts': [{'key':'1','title':'합성','body_html':'<p>본문 12,000원</p>',
        'source_url':'https://a.example.test/notices/1'}], 'cursor':'done'}))
    assert cli.main(['--data',str(root),'receive-fixture','--school','A','--subject','teacher','--source','fixture','--fixture',str(fixture)]) == 0
    with Store(root) as s:
        assert s.db.execute('SELECT count(*) FROM school_service_versions').fetchone()[0] == 1
        assert s.db.execute('SELECT count(*) FROM notices').fetchone()[0] == 0
        assert s.db.execute('SELECT count(*) FROM usage').fetchone()[0] == 0
