"""Loopback HTTP Worker client, reached through an owner-managed encrypted tunnel.

Start intent is durable before transmission. A lost start response never causes
an automatic second launch. Secrets are read only at request time, never journaled.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from urllib.parse import urlsplit

OPERATIONS = frozenset({'create', 'start', 'inspect', 'cancel', 'stop', 'collect', 'cleanup'})
STATES = frozenset({'CREATED', 'STARTING', 'RUNNING', 'UNKNOWN', 'STOPPED', 'COMPLETED', 'FAILED', 'CLEANED'})
TERMINAL = frozenset({'STOPPED', 'COMPLETED', 'FAILED', 'CLEANED'})


class WorkerError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('Remote worker operation failed: ' + code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def run_id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise WorkerError('worker_invalid_run_id')
    return value


class RemoteWorkerClient:
    def __init__(self, url, auth_file, journal, timeout=10):
        parsed = urlsplit(url)
        try: port = parsed.port
        except ValueError: raise WorkerError('worker_configuration') from None
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
                parsed.username or parsed.password or parsed.path not in ('', '/') or
                parsed.query or parsed.fragment or port is None or not 1 <= port <= 65535 or
                not isinstance(timeout, (float, int)) or not 0 < timeout <= 60):
            raise WorkerError('worker_configuration')
        self.port, self.auth_file, self.timeout = port, Path(auth_file), timeout
        journal = Path(journal)
        if journal.is_symlink() or journal.parent.is_symlink(): raise WorkerError('worker_journal_boundary')
        journal.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(journal)
        os.chmod(journal, 0o600)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS registrations (
            id TEXT PRIMARY KEY, request TEXT NOT NULL, request_key TEXT UNIQUE NOT NULL,
            spec TEXT, started INTEGER NOT NULL DEFAULT 0, seq INTEGER NOT NULL DEFAULT 0,
            state TEXT);''')
        if 'event_digest' not in {row[1] for row in self.db.execute('PRAGMA table_info(registrations)')}:
            self.db.execute('ALTER TABLE registrations ADD COLUMN event_digest TEXT');self.db.commit()

    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

    def _token(self):
        try:
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            if self.auth_file.is_symlink(): raise ValueError()
            fd = os.open(self.auth_file, flags)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 4096: raise ValueError()
                if os.name == 'posix' and (info.st_mode & 0o077 or info.st_uid not in (0, os.getuid())): raise ValueError()
                token = stream.read(4097).strip().decode('ascii')
            if not 32 <= len(token) <= 4096 or not re.fullmatch('[!-~]+', token): raise ValueError()
            return token
        except (OSError, UnicodeError, ValueError):
            raise WorkerError('worker_authentication_configuration') from None

    def _request(self, operation, body):
        if operation not in OPERATIONS: raise WorkerError('worker_operation_denied')
        token = self._token()
        data = canonical(body).encode()
        if len(data) > 32768: raise WorkerError('worker_request_limit')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        try:
            connection.request('POST', '/v1/' + operation, body=data,
                headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status != 200:
                # The only echoed reason recognized is the fixed unauthorized code.
                raw = response.read(4097)
                code = 'worker_request_rejected'
                if response.status == 403 and raw == b'{"error":"unauthorized"}': code = 'worker_authentication_failed'
                raise WorkerError(code)
            if response.getheader('Content-Type', '').split(';')[0] != 'application/json': raise WorkerError('worker_invalid_response')
            raw = response.read(524289)
            if len(raw) > 524288: raise WorkerError('worker_response_limit')
            if token.encode() in raw: raise WorkerError('worker_secret_in_response')
            def unique(pairs):
                value = {}
                for key, item in pairs:
                    if key in value: raise ValueError()
                    value[key] = item
                return value
            result = json.loads(raw, object_pairs_hook=unique,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(result, dict): raise ValueError()
            return result
        except WorkerError: raise
        except (OSError, http.client.HTTPException): raise WorkerError('worker_disconnected') from None
        except (ValueError, UnicodeError, RecursionError): raise WorkerError('worker_invalid_response') from None
        finally: connection.close()

    def _row(self, rid):
        run_id(rid)
        row = self.db.execute('SELECT request, request_key, spec, started, seq, state, event_digest FROM registrations WHERE id=?', (rid,)).fetchone()
        if row is None: raise WorkerError('worker_unknown_task')
        return row

    def _record(self, rid, value):
        row = self._row(rid)
        if set(value) != {'spec', 'state', 'events'} or not isinstance(value['state'],str) or value['state'] not in STATES:
            raise WorkerError('worker_invalid_response')
        spec = value['spec']
        if not isinstance(spec, dict): raise WorkerError('worker_invalid_response')
        request = json.loads(row[0])
        derived = {'worktree', 'repository_url', 'toolchain_digest', 'spec_digest'}
        if 'previous_run_id' in request: derived.add('previous_commit')
        if set(spec) != set(request) | derived:
            raise WorkerError('worker_invalid_response')
        if 'previous_commit' in spec and not re.fullmatch('[a-f0-9]{40}',str(spec['previous_commit'])):
            raise WorkerError('worker_invalid_response')
        if any(spec.get(k) != v for k, v in request.items()) or spec.get('worktree') != '/srv/agentbridge/runs/' + rid + '/worktree':
            raise WorkerError('worker_stale_spec')
        unsigned = dict(spec); claimed = unsigned.pop('spec_digest')
        if digest(unsigned) != claimed or (row[2] is not None and canonical(spec) != row[2]):
            raise WorkerError('worker_stale_spec')
        events = value['events']
        if not isinstance(events, list) or not 1 <= len(events) <= 10000: raise WorkerError('worker_invalid_events')
        sequence = 0
        for event in events:
            if (not isinstance(event, dict) or set(event) != {'seq','state','at'} or
                    type(event['seq']) is not int or event['seq'] <= sequence or not isinstance(event['state'],str) or event['state'] not in STATES or
                    type(event['at']) not in (int, float)):
                raise WorkerError('worker_invalid_events')
            sequence = event['seq']
        if sequence < row[4] or events[-1]['state'] != value['state'] or (row[5] in TERMINAL and value['state'] not in {row[5], 'CLEANED'}):
            raise WorkerError('worker_stale_state')
        if row[6] is not None and digest([event for event in events if event['seq']<=row[4]]) != row[6]:
            raise WorkerError('worker_stale_state')
        with self.db:
            self.db.execute('UPDATE registrations SET spec=?,seq=?,state=?,event_digest=? WHERE id=?', (canonical(spec), sequence, value['state'],digest(events),rid))
        return value

    def create(self, spec, idempotency_key):
        required = {'run_id','repository','base_commit','agent','profile','task','limits'}
        if not isinstance(spec, dict) or set(spec) not in (required, required | {'previous_run_id'}): raise WorkerError('worker_invalid_spec')
        if 'previous_run_id' in spec: run_id(spec['previous_run_id'])
        rid = run_id(spec['run_id'])
        if not isinstance(idempotency_key, str) or not re.fullmatch('[A-Za-z0-9_-]{8,128}', idempotency_key): raise WorkerError('worker_invalid_key')
        encoded = canonical(spec)
        with self.db:
            old = self.db.execute('SELECT request,request_key FROM registrations WHERE id=?', (rid,)).fetchone()
            if old and old != (encoded, idempotency_key): raise WorkerError('worker_idempotency_conflict')
            if not old:
                try: self.db.execute('INSERT INTO registrations(id,request,request_key) VALUES(?,?,?)', (rid,encoded,idempotency_key))
                except sqlite3.IntegrityError: raise WorkerError('worker_idempotency_conflict') from None
        return self._record(rid, self._request('create', {'spec': spec, 'idempotency_key': idempotency_key}))

    def _operation(self, operation, rid):
        row = self._row(rid)
        if row[2] is None: raise WorkerError('worker_registration_unknown')
        body = {'run_id': rid, 'spec_digest': json.loads(row[2])['spec_digest']}
        value = self._request(operation, body)
        if operation == 'collect':
            if set(value) != {'registration','result','trusted'} or value['trusted'] is not False or not isinstance(value['result'], dict):
                raise WorkerError('worker_invalid_result')
            self._record(rid, value['registration'])
            if value['registration']['state'] not in {'COMPLETED','FAILED','STOPPED'}: raise WorkerError('worker_result_not_terminal')
            return value
        return self._record(rid, value)

    def start(self, rid):
        record = self.inspect(rid)
        if record['state'] in {'STARTING','RUNNING','COMPLETED','FAILED','UNKNOWN'}: return record
        if record['state'] != 'CREATED': raise WorkerError('worker_start_denied')
        with self.db:
            updated = self.db.execute('UPDATE registrations SET started=1 WHERE id=? AND started=0', (rid,))
            if updated.rowcount != 1: raise WorkerError('worker_start_reconciliation_required')
        return self._operation('start', rid)

    def inspect(self, rid): return self._operation('inspect', rid)
    def cancel(self, rid): return self._operation('cancel', rid)
    def stop(self, rid): return self._operation('stop', rid)
    def collect(self, rid): return self._operation('collect', rid)
    def cleanup(self, rid): return self._operation('cleanup', rid)
