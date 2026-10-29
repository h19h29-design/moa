"""Explicit school-service administration. No NAS deployment or live crawling.

Always pass --data; no implicit directory, migration or school provisioning on
server startup. Use dev-storage on the Mac to keep all generated files external.
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from . import school_service as svc
from .core import Store


def _json(path):
    path = Path(path)
    if path.stat().st_size > 2_000_000: raise ValueError('Configuration too large')
    return json.loads(path.read_text())


def conversion_worker(root, stop):
    """Own SQLite connection; never hold the review server lock while parsing."""
    with Store(root) as store:
        while not stop.is_set():
            svc.process_conversions(store)
            stop.wait(1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('migrate', help='Explicit additive schema and consistent pre-migration backup')
    config = commands.add_parser('configure', help='Trusted local administrator configuration, never staff HTTP')
    config.add_argument('--config', type=Path, required=True)
    receive = commands.add_parser('receive-fixture', help='Fixture records only; no remote fetching')
    for flag in ('school', 'subject', 'source', 'fixture'): receive.add_argument('--'+flag, required=True)
    commands.add_parser('drain', help='Process one school conversion, never research/model jobs')
    commands.add_parser('recover-stale', help='Recover only school jobs with a one-hour expired lease')
    serve = commands.add_parser('serve', help='Private loopback staff UI; does not apply migrations')
    serve.add_argument('--credentials', type=Path, required=True)
    serve.add_argument('--port', type=int, default=8791)
    args = parser.parse_args(argv)
    with Store(args.data, thread_safe=args.command == 'serve') as store:
        if args.command == 'migrate':
            backup = svc.migrate(store)
            version = store.db.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]
            print('school_schema=' + str(version) + '; backup=' + (str(backup) if backup else 'already_applied'))
        elif args.command == 'configure':
            config = _json(args.config)
            for item in config['schools']:
                svc.configure_school(store, item['code'], item['name'],
                    enabled=item.get('enabled', True), upload_enabled=item.get('upload_enabled', True))
                for member in item.get('members', []):
                    svc.grant_member(store, item['code'], member['subject'], member['role'], active=member.get('active', True))
                for source in item.get('sources', []):
                    svc.add_source(store, item['code'], source['id'], kind=source['kind'],
                        allowed_url=source.get('allowed_url', ''), enabled=source.get('enabled', True))
            print('school_configuration_applied')
        elif args.command == 'receive-fixture':
            fixture = _json(args.fixture)
            posts = tuple(svc.IncomingPost(p['key'], p['title'], p.get('body_html', ''),
                tuple(svc.IncomingFile(f['filename'], f['text'].encode(), f.get('role', 'attachment')) for f in p.get('files', [])),
                p['source_url']) for p in fixture['posts'])
            principal = svc.Principal(args.school, args.subject)
            ident = svc.request_receive(store, principal, args.source)
            result = svc.process_receiving(store, {(args.school, args.source): svc.FixtureAdapter(posts, fixture.get('cursor', ''))}, job_id=ident)
            print('fixture_receive=' + ','.join(result))
            return 0 if result == ['done'] else 1
        elif args.command == 'drain':
            print('school_conversion=' + ','.join(svc.process_conversions(store)))
        elif args.command == 'recover-stale':
            print('school_jobs_recovered=' + str(svc.recover_stale(store)))
        elif args.command == 'serve':
            from .web_school import load_credentials, make_handler
            server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(store, load_credentials(args.credentials)))
            stop = threading.Event()
            worker = threading.Thread(target=conversion_worker, args=(args.data, stop), daemon=True); worker.start()
            try:
                print('Private school UI on loopback port ' + str(server.server_port))
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close(); stop.set(); worker.join()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
