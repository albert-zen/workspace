"""Shared local work maintenance: readable Markdown, identities and recoverable writes."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from . import WORKSPACE_ROOT
import re
import tempfile
import uuid

from .inbox_model import (Entry, InboxDocument, InboxError, StaleSourceError, SECTION_NAMES,
                         document_from_payload, document_to_api, parse_inbox_text,
                         revision_for, serialize_inbox, ordered_entries)
from .work_documents import WorkDocuments
from .work_flow import WorkFlowMixin, validate_document, section


_UNCHECKED = object()


def fail(message, code='invalid_request'):
    raise InboxError(message, code=code, action='核对目标与当前记录后重试；中断操作使用 work recover。')


def now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def file_fingerprint(text):
    if text is None:
        return None
    raw = text.encode('utf-8')
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def normalized(value):
    return ' '.join(value.casefold().split())


def section_replace(text, heading, content):
    # Ignore Markdown headings inside fences, matching the existing reader contract.
    lines = text.splitlines(keepends=True)
    start = end = None
    fence = None
    for i, line in enumerate(lines):
        mark = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if mark:
            token = mark.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[mark.end():].strip():
                fence = None
            continue
        if fence:
            continue
        if start is not None and re.match(r'^#{1,2}\s', line):
            end = i
            break
        if line.strip() == '## ' + heading:
            start = i
    block = f'## {heading}\n\n{content.strip()}\n\n'
    if start is None:
        return text.rstrip() + '\n\n' + block
    return ''.join(lines[:start]) + block + ''.join(lines[end if end is not None else len(lines):])


class WorkStore(WorkFlowMixin):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.inbox = self.root / 'personal/inbox/inbox.md'
        self.internal = self.root / 'agent-data/workflow'
        self.state_path = self.internal / 'state.json'
        self.pending = self.internal / 'pending.json'
        self.documents = WorkDocuments(self.root)
        self.launcher = WORKSPACE_ROOT / 'bin/work'
        self.baseline = {}

    def _path(self, relative):
        path = self.root / relative
        if not path.is_relative_to(self.root) or '..' in path.parts:
            fail('路径必须位于当前工作空间。', 'forbidden_path')
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.is_symlink():
                fail('维护路径不能经过符号链接。', 'forbidden_path')
        return path

    @contextmanager
    def _locked(self, recovering=False):
        self._path('agent-data/workflow').mkdir(parents=True, exist_ok=True)
        lock = self._path('agent-data/workflow/lock')
        with lock.open('a+b') as handle:
            if os.name == 'nt':
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b'0'); handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                if self.pending.exists() and not recovering:
                    fail('存在尚未完成的文件操作，请先执行 work recover。', 'pending_operation')
                yield
            finally:
                if os.name == 'nt':
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self, path):
        self._path(path.relative_to(self.root))
        return path.read_bytes().decode('utf-8') if path.exists() else None

    def _write(self, path, value, *, expected=_UNCHECKED):
        self._path(path.relative_to(self.root))
        path.parent.mkdir(parents=True, exist_ok=True)
        if value is None:
            if expected is not _UNCHECKED and self._read(path) != expected:
                fail('文件在保存期间改变，已保留：' + str(path), 'recovery_conflict')
            path.unlink(missing_ok=True)
        else:
            fd, temporary = tempfile.mkstemp(prefix='.work-', dir=path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
                    f.write(value); f.flush(); os.fsync(f.fileno())
                if path.exists():
                    os.chmod(temporary, path.stat().st_mode & 0o777)
                if expected is not _UNCHECKED and self._read(path) != expected:
                    fail('文件在保存期间改变，已保留：' + str(path), 'recovery_conflict')
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _apply(self, operation):
        # Check the write set, then check each target again immediately before replacement.
        for change in operation['writes']:
            current = self._read(self._path(change['path']))
            if current not in (change['before'], change['after']):
                fail(f"操作与外部修改冲突，保留当前文件：{change['path']}", 'recovery_conflict')
        for change in operation['writes']:
            path = self._path(change['path'])
            current = self._read(path)
            if current == change['after']:
                continue
            if current != change['before']:
                fail(f"文件在保存期间改变：{change['path']}", 'recovery_conflict')
            self._write(path, change['after'], expected=change['before'])
        receipt = self.internal / 'operations' / (operation['id'] + '.json')
        # Recovery payloads live only in pending.json; business history is
        # already stored in Work documents, events and plan versions.
        audit = {'version': 2, 'id': operation['id'], 'createdAt': operation['createdAt'],
                 'completedAt': now(),
                 'writes': [{'path': c['path'], 'before': file_fingerprint(c['before']),
                             'after': file_fingerprint(c['after'])} for c in operation['writes']]}
        self._write(receipt, encoded(audit))
        self._write(self.pending, None)

    def _commit(self, state, doc, writes=None, expected=None):
        writes = dict(writes or {})
        rendered = serialize_inbox(doc, self.inbox, self.root)
        parse_inbox_text(rendered, self.inbox, self.root, self.documents.allowed_paths())
        writes['personal/inbox/inbox.md'] = rendered
        writes['agent-data/workflow/state.json'] = encoded(state)
        changes = []
        for relative, after in writes.items():
            before = self._read(self._path(relative))
            observed = {**self.baseline, **(expected or {})}
            if relative in observed and before != observed[relative]:
                raise StaleSourceError()
            if before != after:
                changes.append({'path': str(relative), 'before': before, 'after': after})
        if not changes:
            return
        operation = {'id': uuid.uuid4().hex, 'createdAt': now(), 'writes': changes}
        self._write(self.pending, encoded(operation))
        self._apply(operation)
        self.baseline.update(writes)

    def _empty(self):
        return InboxDocument({x: [] for x in SECTION_NAMES})

    def _semantic(self, title, work_path=None):
        if '/' in title and len(title.split('/')) == 2:
            return title
        project = Path(work_path).parts[2] if work_path else 'inbox'
        return f'{project}/{title}'

    def _validate_name(self, name):
        if not isinstance(name, str) or name != name.strip() or len(name.split('/')) != 2:
            fail('使用“项目/事项名”，例如“示例项目/读取团队文档”。')
        for part in name.split('/'):
            if not part.strip() or part in {'.', '..'} or any(c in part for c in '\n\r\x00\\<>:|?*[]'):
                fail('项目与事项名必须是明确的单行语义名称，不能含路径保留字符。')
        if ' — ' in name or ' · [' in name or '<!-- work:' in name:
            fail('名称含 Inbox 保留分隔符，请调整名称。')
        return name

    def _check_name(self, state, name, identity=None):
        for key, record in state['items'].items():
            if key != identity and normalized(name) in {normalized(n) for n in [record['name'], *record.get('aliases', [])]}:
                fail(f'名称或旧名已被使用：{name}。请重新命名。', 'duplicate_name')

    def _adopt(self, state, doc):
        sections = {x: [] for x in SECTION_NAMES}
        for record in state['items'].values():
            record['inbox'] = False
        for status, entries in doc.sections.items():
            for entry in entries:
                identity = entry.item_id
                if not identity:
                    matches = [r for r in state['items'].values() if normalized(r['title']) == normalized(entry.title)]
                    identity = matches[0]['id'] if len(matches) == 1 else uuid.uuid4().hex
                record = state['items'].get(identity)
                name = self._semantic(entry.title, entry.work_path)
                if record is None:
                    self._check_name(state, name)
                    record = {'id': identity, 'name': name, 'aliases': [], 'lifecycleStatus': 'unverified',
                              'resources': [], 'closeoutPending': [], 'owner': None, 'createdAt': now()}
                    state['items'][identity] = record
                elif record['title'] != entry.title:
                    self._check_name(state, name, identity)
                    record['aliases'] = list(dict.fromkeys([*record.get('aliases', []), record['name']]))
                    record['name'] = name
                record.update(title=entry.title, summary=entry.summary, workPath=entry.work_path or '',
                              issueUrl=entry.issue_url or '', inbox=True, attention=status)
                if entry.pinned:
                    record['pinned'] = True
                else:
                    record.pop('pinned', None)
                sections[status].append(replace(entry, item_id=identity))
            sections[status] = ordered_entries(sections[status])
        return InboxDocument(sections, doc.newline, doc.final_newline)

    def _load(self):
        raw_state = self._read(self.state_path)
        try:
            state = json.loads(raw_state) if raw_state else {'version': 1, 'items': {}}
        except (ValueError, UnicodeError):
            fail('工作索引无法解析，原文件已保留；请检查 agent-data/workflow/state.json。', 'invalid_state')
        if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('items'), dict):
            fail('工作索引格式不受支持，原件已保留。', 'invalid_state')
        text = self._read(self.inbox)
        doc = parse_inbox_text(text, self.inbox, self.root, self.documents.allowed_paths()) if text is not None else self._empty()
        self.baseline = {'personal/inbox/inbox.md': text, 'agent-data/workflow/state.json': raw_state}
        doc = self._adopt(state, doc)
        self._commit(state, doc, expected={'personal/inbox/inbox.md': text, 'agent-data/workflow/state.json': raw_state})
        return state, doc

    def _resolve(self, state, name):
        matches = [r for r in state['items'].values() if name == r['id'] or normalized(name) in
                   {normalized(n) for n in [r['name'], *r.get('aliases', [])]}]
        if not matches and '/' not in name:
            matches = [r for r in state['items'].values() if normalized(name) in
                       {normalized(r['title']), normalized(r['name'].split('/', 1)[-1])}]
        if len(matches) != 1:
            fail('事项不明确：' + ('、'.join(r['name'] for r in matches) if matches else name), 'ambiguous_name' if matches else 'not_found')
        return matches[0]

    def _snapshot(self, state, doc):
        from .work_interface import command
        sections = document_to_api(doc)
        for section in sections:
            for item in section['items']:
                record = state['items'][item['itemId']]
                item.update(semanticName=record['name'], lifecycleStatus=record['lifecycleStatus'],
                            arrangementNote=record.get('arrangement', ''),
                            closeoutPending=record.get('closeoutPending', []),
                            handoffCommand=command(self, 'resume', record['name']))
        return {'sourcePath': 'personal/inbox/inbox.md', 'absoluteSourcePath': str(self.inbox),
                'revision': revision_for(self.inbox.read_bytes()), 'sections': sections}

    def snapshot(self):
        with self._locked():
            state, doc = self._load()
            return self._snapshot(state, doc)

    def _view_versions(self):
        # Metadata only: unchanged polls never parse Inbox or Work content.
        paths = self.documents.allowed_paths() | {'personal/inbox/inbox.md',
                                                  'agent-data/workflow/state.json'}
        versions = {}
        for relative in sorted(paths):
            path = self.root / relative
            try:
                stat = path.stat()
                versions[relative] = f'{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}'
            except FileNotFoundError:
                versions[relative] = 'missing'
        return versions

    def live_snapshot(self, version=''):
        with self._locked():
            before = self._view_versions()
            current = revision_for(json.dumps(before, sort_keys=True).encode())
            if version == current:
                return {'version': current, 'unchanged': True}
            state, doc = self._load()
            result = {'version': current, 'inbox': self._snapshot(state, doc),
                      'documents': self.documents.list_documents(),
                      'workVersions': {p: v for p, v in before.items() if p.startswith('agent-data/projects/')}}
            # Editors outside the CLI lock may write during a read. Retry later.
            if before != self._view_versions():
                fail('数据正在更新，稍后自动重试。', 'source_changing')
            return result

    def save_inbox(self, payload):
        with self._locked():
            state, old = self._load()
            if not isinstance(payload, dict) or set(payload) != {'expectedRevision', 'sections'}:
                fail('请求必须且只能包含 expectedRevision 与 sections。')
            if revision_for(self.inbox.read_bytes()) != payload['expectedRevision']:
                raise StaleSourceError()
            doc = document_from_payload({'sections': payload['sections']}, self.inbox, self.root, self.documents.allowed_paths())
            # Older clients omit the optional pin field: preserve the existing state.
            old_pins = {entry.item_id: entry.pinned for entries in old.sections.values() for entry in entries}
            old_titles = {entry.title: entry.pinned for entries in old.sections.values() for entry in entries}
            for raw_section in payload['sections']:
                status = raw_section['status']
                doc.sections[status] = [replace(entry, pinned=old_pins.get(entry.item_id, old_titles.get(entry.title, False)))
                                        if 'pinned' not in raw else entry
                                        for entry, raw in zip(doc.sections[status], raw_section['items'])]
            existing = {entry.item_id for entries in old.sections.values() for entry in entries}
            for entries in doc.sections.values():
                for entry in entries:
                    if entry.item_id and entry.item_id not in existing:
                        fail('未知事项标识，不能覆盖其他事项。')
            before = self._read(self.inbox)
            known = set(state['items'])
            doc = self._adopt(state, doc)
            for identity in set(state['items']) - known:
                state['items'][identity]['lifecycleStatus'] = 'planned'
            self._commit(state, doc, expected={'personal/inbox/inbox.md': before})
            return self._snapshot(state, doc)

    def _put_entry(self, doc, record, status=None, before=None, include=True):
        sections = {s: [e for e in entries if e.item_id != record['id']] for s, entries in doc.sections.items()}
        target = status or record.get('attention', 'Needs you')
        if target not in SECTION_NAMES:
            fail('个人分类无效。')
        if include:
            entry = Entry(record['title'], record['summary'], record.get('issueUrl') or None,
                          record.get('workPath') or None, item_id=record['id'], pinned=record.get('pinned', False))
            old_index = next((i for i, e in enumerate(doc.sections[target]) if e.item_id == record['id']), len(sections[target]))
            index = next((i for i,e in enumerate(sections[target]) if e.item_id == before), old_index) if before else old_index
            sections[target].insert(min(index, len(sections[target])), entry)
            sections[target] = ordered_entries(sections[target])
        record.update(attention=target, inbox=include)
        return InboxDocument(sections, doc.newline, doc.final_newline)

    def add(self, name, summary=None):
        self._validate_name(name)
        with self._locked():
            state, doc = self._load()
            record = self._new_record(state, name, summary)
            doc = self._put_entry(doc, record, 'Waiting')
            # Validate the canonical text before any content write.
            parse_inbox_text(serialize_inbox(doc, self.inbox, self.root), self.inbox, self.root, self.documents.allowed_paths())
            self._commit(state, doc)
            return self._view(record)

    def _new_record(self, state, name, summary):
        self._validate_name(name)
        self._check_name(state, name)
        record = {'id': uuid.uuid4().hex, 'name': name, 'title': name, 'summary': summary or name.split('/', 1)[1],
                  'aliases': [], 'workPath': '', 'issueUrl': '', 'lifecycleStatus': 'planned',
                  'resources': [], 'closeoutPending': [], 'owner': None, 'createdAt': now()}
        state['items'][record['id']] = record
        return record

    def _view(self, record, document=False):
        result = json.loads(encoded(record))
        path = self._path(record['workPath']) if record.get('workPath') else None
        result['absoluteWorkPath'] = str(path) if path else None
        result['revision'] = revision_for(encoded(record).encode() + (path.read_bytes() if path and path.exists() else b''))
        if document:
            result['document'] = path.read_text(encoding='utf-8') if path and path.is_file() else ''
            result['documentMissing'] = bool(path and not path.is_file())
        return result

    def list(self, include_hidden=False):
        with self._locked():
            state, _ = self._load()
            return [self._view(r) for r in state['items'].values() if include_hidden or r.get('inbox')]

    def resume(self, name):
        with self._locked():
            state, _ = self._load()
            record = self._resolve(state, name)
            result = self._view(record, document=True)
            from .work_resources import inspect_resource
            result['resourceObservations'] = [inspect_resource(r) for r in record['resources'] if r['kind'] == 'worktree']
            return result

    def _plan_named_worktree(self, record, tree_name, repo=None, path=None, branch=None, base=None, at=None):
        from .work_resources import plan_worktree, repository_root, resolve_commit, resolve_base
        if not isinstance(tree_name, str) or not tree_name.strip() or any(c in tree_name for c in '\n\r\0'):
            fail('工作树名称必须为非空单行。')
        tree_name = ' '.join(tree_name.split())
        trees = [r for r in record['resources'] if r['kind'] == 'worktree']
        # Before named trees existed, start created at most one managed tree.
        for tree in trees:
            if not tree.get('name') and tree.get('ownership_token'):
                tree['name'] = '开发'
        matches = [r for r in trees if normalized(r.get('name', '')) == normalized(tree_name)]
        if len(matches) > 1:
            fail('工作树名称不唯一，请先核对关联记录。')
        if matches:
            resource = matches[0]
            if resource.get('released') or resource.get('status') == 'cleaned':
                fail('同名工作树已清理或解除关联，请使用新名称。')
            checks = ((repo is not None, lambda: str(repository_root(repo)) == resource['repo']),
                      (path is not None, lambda: str(Path(path).expanduser().resolve()) == resource['path']),
                      (branch is not None, lambda: branch == resource['branch']),
                      (base is not None, lambda: resolve_base(Path(resource['repo']), base) == resource['base']),
                      (at is not None, lambda: resolve_commit(Path(resource['repo']), at) == resource['base_commit']))
            if any(provided and not check() for provided, check in checks):
                fail('同名工作树已存在，但参数不同；请沿用原配置或使用新名称。')
            return resource
        if repo is None:
            repos = {str(repository_root(r['repo'])) for r in record['resources'] if r.get('repo')}
            if len(repos) != 1:
                fail('无法唯一确定仓库，请用 --repo 指定。')
            repo = repos.pop()
        repo = repository_root(repo)
        slug = re.sub(r'[^\w.-]+', '-', record['name'] + '/' + tree_name).strip('-')
        tree_path = Path(path).expanduser().absolute() if path else self.root / 'dev' / 'worktrees' / repo.name / slug
        resource = plan_worktree(repo, tree_path, branch or 'codex/' + slug, base, at)
        resource['name'] = tree_name
        record['resources'].append(resource)
        return resource

    def add_worktree(self, name, tree_name, repo=None, path=None, branch=None, base=None, at=None):
        from .work_resources import ensure_worktree
        with self._locked():
            state, doc = self._load()
            record = self._resolve(state, name)
            if record['lifecycleStatus'] in {'completed', 'cancelled'}:
                fail('事项已结束；继续工作请先执行 reopen。', 'work_ended')
            resource = self._plan_named_worktree(record, tree_name, repo, path, branch, base, at)
            # Register intent first; a retry uses the same creation receipt.
            self._commit(state, doc)
            resource.update(ensure_worktree(resource))
            self._commit(state, doc)
            return self._view(record)

    def start(self, name, worktree=False, repo=None, path=None, branch=None, base=None, work=None, summary=None, plan_text=None):
        with self._locked():
            state, doc = self._load()
            try:
                record = self._resolve(state, name)
            except InboxError as exc:
                # Creating a missing item requires an explicit goal and a full semantic name.
                # Delay its commit until all Work/Git preparation has been validated.
                if exc.code != 'not_found' or summary is None or not summary.strip():
                    raise
                record = self._new_record(state, name, summary)
            if record['lifecycleStatus'] in {'completed', 'cancelled'}:
                fail('事项已结束；继续工作请先执行 reopen。', 'work_ended')
            writes = {}
            if work:
                value = Path(work)
                if value.is_absolute():
                    value = value.relative_to(self.root)
                if value.as_posix() not in self.documents.allowed_paths():
                    fail('请选择允许的 active/archive Work 文档。', 'invalid_work')
                if record.get('workPath') and record['workPath'] != value.as_posix():
                    fail('事项已关联另一份 Work；请明确处理现有材料。')
                record['workPath'] = value.as_posix()
            if not record.get('workPath'):
                project, title = record['name'].split('/', 1)
                self._validate_name(record['name'])
                filename = re.sub(r'[^\w.-]+', '-', title).strip('-') or 'work'
                relative = f'agent-data/projects/{project}/work/active/{filename}.md'
                if self._path(relative).exists():
                    fail('同名 Work 已存在；用 --work 显式关联，或调整名称。', 'duplicate_name')
                record['workPath'] = relative
                writes[relative] = f"# {title}\n\n## 事项说明\n\n{record['summary']}\n\n## 当前推进\n\n已开始。\n"
            elif not self._path(record['workPath']).is_file():
                fail('关联 Work 不存在，请先核对材料位置。', 'work_not_found')
            was_active = record['lifecycleStatus'] == 'active'
            record['lifecycleStatus'] = 'active'
            from .work_resources import ensure_worktree
            resource = self._plan_named_worktree(record, '开发', repo, path, branch, base) if worktree else None
            if plan_text is not None:
                if record.get('planVersion'):
                    if self._plan(record)['markdown'] != plan_text:
                        fail('已有计划；使用 plan edit 修改，不通过 start 覆盖。')
                else:
                    self._set_plan(record, plan_text, writes, '保存初始计划。')
            if not was_active:
                self._event(record, 'start', '已开始。', writes)
            doc = self._put_entry(doc, record, 'In progress')
            self._commit(state, doc, writes)
            if resource:
                try:
                    result = ensure_worktree(resource)
                    resource.update(result)
                    self._commit(state, doc)
                except Exception:
                    # The creation intent was recorded before touching Git. Repeating start resumes it.
                    raise
            return self._view(record, document=True)

    def update(self, name, summary=None, description=None, progress=None, expected_revision=None):
        with self._locked():
            state, doc = self._load()
            record = self._resolve(state, name)
            if expected_revision is None:
                fail('更新需要 --expected-revision（从 resume 取得），避免覆盖他人进展。')
            if self._view(record)['revision'] != expected_revision:
                raise StaleSourceError()
            writes, expected = {}, {}
            if summary is not None:
                record['summary'] = summary
            if description is not None or progress is not None:
                if not record.get('workPath'):
                    fail('先 start 建立 Work，再更新说明或推进。')
                path = self._path(record['workPath'])
                original = self._read(path)
                if original is None:
                    fail('关联 Work 不存在。')
                text = original
                validate_document(text)
                if description is not None:
                    text = section_replace(text, '事项说明', description)
                if progress is not None:
                    text = section_replace(text, '当前推进', progress)
                writes[record['workPath']] = text
                expected[record['workPath']] = original
                validate_document(text)
                if section(text, '当前计划', raw=True) != section(original, '当前计划', raw=True):
                    fail('修改计划请使用 plan edit / plan save，以保留版本。')
                if section(text, '历史记录', raw=True) != section(original, '历史记录', raw=True):
                    fail('执行历史只追加；请用 record 添加更正，不能改写旧记录。')
                if progress is not None:
                    self._event(record, 'update', progress, writes, prior_document=original)
            doc = self._put_entry(doc, record, include=record.get('inbox', True))
            parse_inbox_text(serialize_inbox(doc, self.inbox, self.root), self.inbox, self.root, self.documents.allowed_paths())
            self._commit(state, doc, writes, expected)
            return self._view(record, document=True)

    def rename(self, name, new_name):
        self._validate_name(new_name)
        with self._locked():
            state, doc = self._load()
            record = self._resolve(state, name)
            if record['name'].split('/')[0] != new_name.split('/')[0]:
                fail('rename 只修改同一项目内的名称；跨项目迁移需同时核对材料归属。')
            self._check_name(state, new_name, record['id'])
            record['aliases'] = list(dict.fromkeys([*record['aliases'], record['name']]))
            record['name'] = record['title'] = new_name
            self._commit(state, self._put_entry(doc, record, include=record.get('inbox', True)))
            return self._view(record)

    def arrange(self, name, status=None, before=None, note=None, hide=False):
        with self._locked():
            state, doc = self._load()
            record = self._resolve(state, name)
            preceding = self._resolve(state, before) if before else None
            if preceding and not preceding.get('inbox'):
                fail('排序目标不在 Inbox 中。')
            if preceding and status and status != preceding['attention']:
                fail('排序目标必须在同一个人分类中。')
            if note is not None:
                record['arrangement'] = note
            doc = self._put_entry(doc, record, status or (preceding['attention'] if preceding else None),
                                  preceding['id'] if preceding else None, include=not hide)
            self._commit(state, doc)
            return self._view(record)

    def transition(self, name, action, owner=None, reason=None, needs_user=False):
        with self._locked():
            state, doc = self._load()
            record = self._resolve(state, name)
            old_status = record['lifecycleStatus']
            if action == 'handoff':
                if not owner or not owner.strip():
                    fail('交接需要 --owner 指明承接者。')
                record['owner'] = owner
            elif action in {'pause', 'cancel', 'reopen'}:
                if action in {'pause', 'cancel'} and record['lifecycleStatus'] == 'completed':
                    fail('事项已经完成；请先 reopen 再改变执行状态。')
                record['lifecycleStatus'] = {'pause': 'paused', 'cancel': 'cancelled', 'reopen': 'active' if record['workPath'] else 'planned'}[action]
                if action == 'reopen':
                    record['closeoutPending'] = []
                    record.pop('result', None)
                    record.pop('completedAt', None)
                    if old_status in {'completed', 'cancelled'}:
                        record['run'] = record.get('run', 1) + 1
            else:
                fail('未知状态动作。')
            writes, expected = {}, {}
            if action == 'reopen':
                old = record.get('workPath')
                if old and '/work/archive/' in old:
                    new = old.replace('/work/archive/', '/work/active/', 1)
                    if self._path(new).exists():
                        fail('active 下已有同名 Work，未覆盖。')
                    doc, writes, expected = self._move_work(state, doc, old, new)
                doc = self._put_entry(doc, record, 'In progress' if record.get('workPath') else 'Needs you')
            elif action == 'pause':
                doc = self._put_entry(doc, record, 'Needs you' if needs_user else 'Waiting')
            elif action == 'cancel':
                doc = self._put_entry(doc, record, include=False)
            labels = {'pause': '已暂停。', 'cancel': '已取消。', 'reopen': '已恢复工作。', 'handoff': '负责人已更新：' + (owner or '')}
            self._event(record, action, reason or labels[action], writes)
            self._commit(state, doc, writes, expected)
            return self._view(record)

    def link(self, name, kind, path=None, repo=None, branch=None, url=None, owned=False):
        if kind not in {'worktree', 'branch', 'thread', 'pr', 'material'}:
            fail('资源类型无效。')
        if owned:
            fail('已有资源不凭声明取得删除权限；由 start 创建的工作树才自动拥有。')
        with self._locked():
            state, doc = self._load()
            record = self._resolve(state, name)
            resource = {'kind': kind, 'owned': False}
            if path:
                resource['path'] = str(Path(path).resolve())
            if repo:
                resource['repo'] = str(Path(repo).resolve())
            if branch:
                resource['branch'] = branch
            if url:
                resource['url'] = url
            if kind == 'worktree' and not (path and repo and branch):
                fail('工作树关联需要 path、repo、branch。')
            if kind == 'branch' and not (repo and branch):
                fail('分支关联需要 repo、branch。')
            if kind in {'thread', 'pr'} and not url:
                fail('平台资源需要 --url 精确定位。')
            if kind == 'material' and not (path or url):
                fail('材料需要 path 或 url。')
            if resource not in record['resources']:
                record['resources'].append(resource)
            self._commit(state, doc)
            return self._view(record)

    def recover(self):
        with self._locked(recovering=True):
            if self.pending.exists():
                self._apply(json.loads(self.pending.read_text(encoding='utf-8')))
            state, doc = self._load()
            return self._snapshot(state, doc)

    def _archive(self, state, doc, record):
        old = record.get('workPath')
        if not old or '/work/archive/' in old:
            return doc, {}, {}
        if '/work/active/' not in old:
            fail('Work 不在可归档目录中。', 'invalid_work')
        sharing = [r['name'] for r in state['items'].values() if r['id'] != record['id']
                   and r.get('workPath') == old and r['lifecycleStatus'] not in {'completed', 'cancelled'}]
        if sharing:
            record['closeoutPending'].append('Work 仍被其他事项使用：' + '、'.join(sharing))
            return doc, {}, {}
        new = old.replace('/work/active/', '/work/archive/', 1)
        old_path, new_path = self._path(old), self._path(new)
        if not old_path.is_file():
            record['closeoutPending'].append('关联 Work 缺失，归档待核实：' + old)
            return doc, {}, {}
        if new_path.exists():
            record['closeoutPending'].append('归档目标已经存在，未覆盖：' + new)
            return doc, {}, {}
        return self._move_work(state, doc, old, new)

    def _move_work(self, state, doc, old, new):
        from urllib.parse import urlsplit, unquote, quote
        old_path, new_path = self._path(old), self._path(new)
        writes, expected = {}, {}
        paths = self.documents.allowed_paths()
        for relative in paths:
            source = self._path(relative)
            original = self._read(source)
            destination = new_path if relative == old else source
            def relocate(match):
                raw = match.group(1)
                parsed = urlsplit(raw)
                if parsed.scheme or parsed.netloc or raw.startswith(('/', '#')) or not parsed.path:
                    return match.group(0)
                target = (source.parent / unquote(parsed.path)).resolve()
                if target == old_path:
                    target = new_path
                elif relative != old:
                    return match.group(0)
                value = os.path.relpath(target, destination.parent).replace(os.sep, '/')
                value = quote(value, safe="/.-_~") if '%' in raw or ' ' in value else value
                return '](' + value + ('?' + parsed.query if parsed.query else '') + ('#' + parsed.fragment if parsed.fragment else '') + ')'
            # Rebase real Markdown links, preserving literal examples and fenced evidence.
            rebased = []
            fence = None
            for line in original.splitlines(keepends=True):
                marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
                if marker:
                    token = marker.group(1)
                    if fence is None:
                        fence = token
                    elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                        fence = None
                    rebased.append(line); continue
                if fence:
                    rebased.append(line); continue
                pieces = re.split(r'(`+[^`]*`+)', line)
                for index in range(0, len(pieces), 2):
                    pieces[index] = re.sub(r'\]\(([^\s)]+)\)', relocate, pieces[index])
                line = ''.join(pieces)
                reference = re.match(r'^( {0,3}\[[^\]]+\]:\s*)([^\s]+)', line)
                if reference:
                    replacement = re.sub(r'\]\(([^\s)]+)\)', relocate, '](' + reference.group(2) + ')')[2:-1]
                    line = reference.group(1) + replacement + line[reference.end():]
                rebased.append(line)
            updated = ''.join(rebased)
            if relative == old:
                writes[new] = updated
                writes[old] = None
                expected[old], expected[new] = original, None
            elif updated != original:
                writes[relative], expected[relative] = updated, original
        for other in state['items'].values():
            if other.get('workPath') == old:
                other['workPath'] = new
            for resource in other['resources']:
                if resource['kind'] == 'material' and resource.get('path') == str(old_path):
                    resource['path'] = str(new_path)
        sections = {s: [replace(e, work_path=new) if e.work_path == old else e for e in entries]
                    for s, entries in doc.sections.items()}
        return InboxDocument(sections, doc.newline, doc.final_newline), writes, expected

    def finish(self, name, result=None, dry_run=False, expected_revision=None):
        with self._locked():
            state, doc = self._load()
            record = self._resolve(state, name)
            if expected_revision and self._view(record)['revision'] != expected_revision:
                raise StaleSourceError()
            if not dry_run and not result and not record.get('result'):
                fail('首次完成需要 --result 保存成果与证据入口。')
            from .work_resources import inspect_resource, cleanup_resource
            if dry_run:
                return {'name': record['name'], 'dryRun': True, 'workPath': record.get('workPath'),
                        'absoluteWorkPath': str(self._path(record['workPath'])) if record.get('workPath') else None,
                        'resources': record['resources'],
                        'resourceObservations': [inspect_resource(r) for r in record['resources'] if r['kind'] == 'worktree'],
                        'result': result or record.get('result')}
            new_completion = record['lifecycleStatus'] != 'completed'
            changed_result = result is not None and result != record.get('result')
            if new_completion and record.get('planVersion'):
                from .work_plan import node_states
                unfinished = [n['name'] for n in node_states(self._plan(record), record.get('stepStates')) if n['state'] != 'done']
                if unfinished:
                    fail('计划仍有未完成步骤：' + '、'.join(unfinished) + '。记录步骤完成，或修订计划说明剩余工作的去向。')
            record['lifecycleStatus'] = 'completed'
            record['result'] = result or record['result']
            record['closeoutPending'] = []
            if new_completion or not record.get('completedAt'):
                record['completedAt'] = now()
            # Record completion before external cleanup. Retrying finish is safe.
            record['closeoutPending'] = ['收尾执行中；中断后再次执行 finish 继续。']
            completion_writes = {}
            if new_completion or changed_result or record.get('outcomeHeading'):
                self._event(record, 'finish' if new_completion else 'result-correction', record['result'], completion_writes)
            record['summary'] = re.sub(r'\s+', ' ', record['result']).replace(' — ', '；').replace(' · [', ' [')
            doc = self._put_entry(doc, record, 'Done', include=record.get('inbox', True))
            self._commit(state, doc, completion_writes)
            record['closeoutPending'] = []
            for resource in record['resources']:
                if resource.get('released'):
                    continue
                if resource['kind'] == 'worktree':
                    sharing = any(r['id'] != record['id']
                                  and any(not x.get('released') and (x.get('path') == resource.get('path') or
                                          (x.get('repo') == resource.get('repo') and x.get('branch') == resource.get('branch')))
                                          for x in r['resources'])
                                  for r in state['items'].values())
                    # Active platform execution cannot be inferred from a URL.
                    platform_pending = any(r['kind'] == 'thread' and not r.get('released') for r in record['resources'])
                    response = cleanup_resource(resource, shared=sharing or platform_pending,
                                                archive_root=self.root / 'agent-data/worktree-recovery')
                    resource['cleanup'] = response
                    if response.get('status') == 'cleaned':
                        resource['status'] = 'cleaned'
                    else:
                        record['closeoutPending'].append(response.get('reason', '工作树尚未清理。'))
                elif resource['kind'] in {'thread', 'branch', 'pr'} and not resource.get('released'):
                    record['closeoutPending'].append(f"{resource['kind']} 需通过原平台核实并解除关联：{resource.get('url') or resource.get('branch')}")
                self._commit(state, doc)
            doc, writes, expected = self._archive(state, doc, record)
            if record.get('workPath'):
                target = record['workPath']
                current = writes.get(target, self._read(self._path(target)))
                if current is not None:
                    # Current result is a projection; previous rounds remain in event history.
                    outcome = record['result']
                    if record['closeoutPending']:
                        outcome += '\n\n待收尾：\n' + '\n'.join('- ' + x for x in record['closeoutPending'])
                    progress = '状态：已完成\n\n' + outcome
                    writes[target] = section_replace(current, '当前推进', progress)
                    record['lastProgress'] = progress
                    if target not in expected:
                        expected[target] = self._read(self._path(target))
            record['summary'] = re.sub(r'\s+', ' ', record['result']).replace(' — ', '；').replace(' · [', ' [')
            doc = self._put_entry(doc, record, 'Done', include=record.get('inbox', True))
            self._commit(state, doc, writes, expected)
            return self._view(record, document=True)

    def release(self, name, target):
        """Record that a referenced platform/shared resource is no longer used by this work."""
        with self._locked():
            state, doc = self._load()
            record = self._resolve(state, name)
            matches = [r for r in record['resources'] if target in (r.get('path'), r.get('url'), r.get('branch'))]
            if len(matches) != 1:
                fail('资源定位不唯一或不存在。')
            matches[0]['released'] = True
            resource = matches[0]
            locator = resource.get('url') or resource.get('branch')
            pending = f"{resource['kind']} 需通过原平台核实并解除关联：{locator}"
            record['closeoutPending'] = [x for x in record.get('closeoutPending', []) if x != pending]
            writes = {}
            if record['lifecycleStatus'] == 'completed' and record.get('workPath'):
                document = self._work_text(record, writes)
                progress = '状态：已完成\n\n' + record['result']
                if record['closeoutPending']:
                    progress += '\n\n待收尾：\n' + '\n'.join('- ' + x for x in record['closeoutPending'])
                writes[record['workPath']] = section_replace(document, '当前推进', progress)
                record['lastProgress'] = progress
            self._commit(state, doc, writes)
            return self._view(record)

    def edit(self, name, output=None):
        """Create a readable working copy; its version travels inside the file."""
        import base64
        with self._locked():
            state, _ = self._load()
            record = self._resolve(state, name)
            view = self._view(record, document=True)
            if not record.get('workPath'):
                fail('先 start 建立 Work，再编辑。', 'work_not_started')
            if view['documentMissing']:
                fail('关联 Work 缺失，先核对材料位置。', 'work_not_found')
            output = Path(output).resolve() if output else Path(tempfile.mkdtemp(prefix='work-')) / 'context.md'
            if output.exists():
                fail('编辑副本目标已存在，未覆盖；请选择新路径。')
            if output.is_relative_to(self.root / 'agent-data/projects') or output == self.inbox or output.is_relative_to(self.internal):
                fail('编辑副本不能覆盖工作记录目录；请选择临时路径。')
            metadata = base64.urlsafe_b64encode(encoded({'root': str(self.root), 'id': record['id'], 'revision': view['revision']}).encode()).decode()
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('x', encoding='utf-8') as stream:
                stream.write(f'<!-- work-draft:{metadata} -->\n' + view['document'])
            return {'name': record['name'], 'lifecycleStatus': record['lifecycleStatus'], 'draftPath': str(output)}

    def save(self, draft):
        import base64
        raw = Path(draft).read_text(encoding='utf-8')
        match = re.match(r'^<!-- work-draft:([A-Za-z0-9_=\-]+) -->\n', raw)
        if not match:
            fail('文件缺少编辑来源；请用 work edit 创建工作副本。')
        metadata = json.loads(base64.urlsafe_b64decode(match.group(1)).decode())
        if metadata['root'] != str(self.root):
            fail('编辑副本属于另一工作空间，请核对 --root。')
        text = raw[match.end():]
        receipt = hashlib.sha256(raw.encode()).hexdigest()
        if not text.strip():
            fail('Work 不能保存为空文档。')
        validate_document(text)
        with self._locked():
            state, doc = self._load()
            record = self._resolve(state, metadata['id'])
            if record.get('lastWorkDraft') == receipt:
                return self._view(record, document=True)
            path = self._path(record['workPath'])
            current = self._read(path)
            # Editors may convert LF/CRLF. Preserve protected sections byte-for-byte
            # when that is their only change; content/whitespace edits still fail.
            from .work_documents import split_work_sections
            parts = split_work_sections(text)
            originals = {p['title']: p for p in split_work_sections(current)
                         if p['level'] == 2 and p['title'] in ('当前计划', '历史记录')}
            for part in parts:
                original = originals.get(part['title']) if part['level'] == 2 else None
                if original and part['markdown'].replace('\r\n', '\n') == original['markdown'].replace('\r\n', '\n'):
                    part['markdown'] = original['markdown']
            text = ''.join(p['markdown'] for p in parts)
            if current != text:
                try:
                    if self._view(record)['revision'] != metadata['revision']:
                        raise StaleSourceError()
                    if section(text, '当前计划', raw=True) != section(current, '当前计划', raw=True):
                        fail('修改计划请使用 plan edit / plan save，以保留版本。')
                    if section(text, '历史记录', raw=True) != section(current, '历史记录', raw=True):
                        fail('执行历史只追加；请用 record 添加更正，不能改写旧记录。')
                    writes = {record['workPath']: text}
                    progress = section(text, '当前推进')
                    if progress != section(current, '当前推进'):
                        self._event(record, 'update', progress, writes, prior_document=current)
                    else:
                        self._event(record, 'edit', '更新事项说明或补充材料。', writes, current=False)
                    record['lastWorkDraft'] = receipt
                    self._commit(state, doc, writes, {record['workPath']: current})
                except StaleSourceError as error:
                    error.work_name = record['name']
                    raise
            return self._view(record, document=True)
