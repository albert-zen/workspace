"""Plan versions and immutable work events behind the semantic operations."""
import base64
import json
import hashlib
import re
import tempfile
from pathlib import Path

from .inbox_model import InboxError, StaleSourceError
from .work_documents import split_work_sections


def reject(message):
    raise InboxError(message, code='invalid_request')


def section(text, title, raw=False):
    found = [s for s in split_work_sections(text) if s['level'] == 2 and s['title'] == title]
    if len(found) > 1:
        reject('章节重复：' + title + '。请保留一个当前入口。')
    content = found[0]['markdown'].partition('\n')[2] if found else ''
    return content if raw else content.strip()


def append_history(document, block):
    """Append without normalizing any previously recorded Markdown."""
    from .work_store import section_replace
    sections = split_work_sections(document)
    for part in sections:
        if part['level'] == 2 and part['title'] == '历史记录':
            old = part['markdown']
            separator = '' if old.endswith('\n\n') else '\n' if old.endswith('\n') else '\n\n'
            part['markdown'] = old + separator + block + '\n\n'
            return ''.join(p['markdown'] for p in sections)
    return section_replace(document, '历史记录', block)


def validate_document(text):
    for heading in ('事项说明', '当前推进', '当前计划', '历史记录'):
        section(text, heading)


class WorkFlowMixin:
    def _flow_path(self, record, tail):
        return 'agent-data/workflow/items/' + record['id'] + '/' + tail

    def _work_text(self, record, writes):
        path = record.get('workPath')
        if not path:
            return None
        if path in writes:
            return writes[path]
        text = self._read(self._path(path))
        if text is None:
            reject('关联 Work 缺失，未修改记录。')
        self.baseline[path] = text
        validate_document(text)
        return text

    def _event(self, record, kind, text, writes, *, current=True, step=None, prior_document=None):
        from .work_store import now, encoded, section_replace
        document = self._work_text(record, writes)
        sequence = record.get('eventCount', 0) + 1
        event = {'sequence': sequence, 'time': now(), 'kind': kind, 'text': text,
                 'planVersion': record.get('planVersion', 0), 'run': record.get('run', 1),
                 'lifecycleStatus': record['lifecycleStatus'], 'stage': record.get('stage')}
        if step:
            event['step'] = step
            node = next(n for n in self._plan(record)['nodes'] if n['name'] == step)
            event['stepId'] = node['id']
            event['stepStatus'] = record.get('stepStates', {}).get(node['id'], 'pending')
        legacy = record.pop('outcomeHeading', None)
        if legacy and document is not None:
            event['previousOutcome'] = section(document, legacy)
            document = ''.join(s['markdown'] for s in split_work_sections(document)
                               if not (s['level'] == 2 and s['title'] == legacy))
        before = prior_document if prior_document is not None else document
        old = section(before, '当前推进') if before is not None else ''
        if old and old != record.get('lastProgress') and old.strip() != text.strip():
            event['previousProgress'] = old
        path = self._flow_path(record, f'events/{sequence:08d}.json')
        if self._path(path).exists():
            reject('历史序号冲突，原记录已保留。')
        self.baseline[path] = None
        writes[path] = encoded(event)
        record['eventCount'] = sequence
        if document is not None:
            block = f"### {sequence} · {event['time']} · {kind}\n\n"
            if event.get('planVersion'):
                block += f"计划 v{event['planVersion']}\n\n"
            if event.get('previousProgress'):
                block += '此前推进：\n\n' + '\n'.join('> ' + line for line in old.splitlines()) + '\n\n'
            if event.get('previousOutcome'):
                block += '此前交付：\n\n' + '\n'.join('> ' + line for line in event['previousOutcome'].splitlines()) + '\n\n'
            block += '\n'.join('> ' + line for line in text.splitlines())
            document = append_history(document, block)
            if current:
                # Business content is supplied once; status/stage are program-owned projections.
                labels = {'active': '进行中', 'paused': '已暂停', 'cancelled': '已取消', 'completed': '已完成', 'planned': '待开始'}
                progress = '状态：' + labels.get(record['lifecycleStatus'], record['lifecycleStatus'])
                if record.get('stage'):
                    progress += ' · 阶段：' + record['stage']
                progress += '\n\n' + text.strip()
                # Keep caller text inside its section, including Markdown headings.
                progress = re.sub(r'(?m)^(#{1,2}) ', r'### ', progress)
                document = section_replace(document, '当前推进', progress)
                record['lastProgress'] = progress
            else:
                # A migration snapshot is already in this event; don't capture it again.
                record['lastProgress'] = old
            writes[record['workPath']] = document
        return event

    def history(self, name, limit=20):
        if limit < 1 or limit > 10000:
            reject('limit 应在 1 到 10000 之间。')
        with self._locked():
            state, _ = self._load()
            item = self._resolve(state, name)
            count = item.get('eventCount', 0)
            events = [json.loads(self._read(self._path(self._flow_path(item, f'events/{i:08d}.json'))))
                      for i in range(max(1, count - limit + 1), count + 1)]
            return {'name': item['name'], 'events': events, 'total': count}

    def _plan(self, record, version=None):
        version = record.get('planVersion', 0) if version is None else version
        if not isinstance(version, int) or version < 1 or version > record.get('planVersion', 0):
            reject('没有该计划版本；使用 plan edit 编写计划。')
        return json.loads(self._read(self._path(self._flow_path(record, f'plans/{version:06d}.json'))))

    def _set_plan(self, record, markdown, writes, reason='计划已更新。', renames=None):
        from .work_store import now, encoded, section_replace
        from .work_plan import parse_plan, render_plan, validate_completion
        if not markdown.strip():
            reject('计划不能为空。')
        document = self._work_text(record, writes)
        if document is None:
            reject('请先 start 建立 Work。')
        old = self._plan(record) if record.get('planVersion') else None
        legacy = section(document, '当前计划', raw=True) if old is None else ''
        if legacy.strip():
            # Unindexed Markdown is evidence, not an already-validated dependency graph.
            old = {'markdown': legacy, 'nodes': [], 'states': {}, 'version': 1,
                   'createdAt': now(), 'reason': '保留既有计划原文。', 'legacySnapshot': True}
            path = self._flow_path(record, 'plans/000001.json')
            if self._path(path).exists():
                reject('计划版本冲突，原版本已保留。')
            self.baseline[path] = None
            writes[path] = encoded(old)
            record['planVersion'] = 1
            self._event(record, 'plan-import', old['reason'], writes, current=False)
        plan = parse_plan(markdown, previous=old, renames=renames)
        prior = record.get('stepStates', {})
        statuses = {n['id']: 'done' if n['done'] else
                    prior[n['id']] if prior.get(n['id']) in {'active', 'blocked'} else 'pending'
                    for n in plan['nodes']}
        validate_completion(plan, statuses)
        version = record.get('planVersion', 0) + 1
        plan.update(version=version, createdAt=now(), reason=reason, states=statuses)
        path = self._flow_path(record, f'plans/{version:06d}.json')
        if self._path(path).exists():
            reject('计划版本冲突，原版本已保留。')
        self.baseline[path] = None
        writes[path] = encoded(plan)
        record['planVersion'] = version
        record['stepStates'] = statuses
        document = self._work_text(record, writes)
        rendered = re.sub(r'(?m)^#{1,2} ', '### ', render_plan(plan))
        writes[record['workPath']] = section_replace(document, '当前计划', f'版本：v{version}\n\n' + rendered)
        self._event(record, 'plan', reason, writes, current=False)

    def plan_edit(self, name, output=None):
        from .work_store import encoded
        from .work_plan import render_plan
        with self._locked():
            state, _ = self._load()
            item = self._resolve(state, name)
            if not item.get('workPath'):
                reject('请先 start 建立 Work。')
            view = self._view(item)
            plan = self._plan(item) if item.get('planVersion') else None
            legacy = section(self._work_text(item, {}), '当前计划', raw=True)
            body = render_plan(plan, item.get('stepStates')) if plan else legacy or '# 计划\n\n写下目标、约束和步骤；复杂依赖在步骤后写“依赖：步骤名”。\n'
            target = Path(output).resolve() if output else Path(tempfile.mkdtemp(prefix='work-plan-')) / 'plan.md'
            if target.is_relative_to(self.root / 'agent-data') or target == self.inbox:
                reject('编辑副本不能覆盖工作记录。')
            metadata = {'root': str(self.root), 'id': item['id'], 'revision': view['revision']}
            token = base64.urlsafe_b64encode(encoded(metadata).encode()).decode()
            with target.open('x', encoding='utf-8') as f:
                f.write('<!-- work-plan:' + token + ' -->\n' + body)
            return {'name': item['name'], 'draftPath': str(target), 'planVersion': item.get('planVersion', 0)}

    def plan_save(self, file, summary=None, reason='计划已更新。', renames=None):
        raw = Path(file).read_text(encoding='utf-8')
        match = re.match(r'^<!-- work-plan:([A-Za-z0-9_=\-]+) -->\n', raw)
        if not match:
            reject('请用 plan edit 取得带版本的编辑副本。')
        metadata = json.loads(base64.urlsafe_b64decode(match[1]))
        if metadata['root'] != str(self.root):
            reject('编辑副本属于另一工作空间。')
        markdown = raw[match.end():]
        receipt = hashlib.sha256(json.dumps([raw, summary, reason, renames], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self._locked():
            state, doc = self._load()
            item = self._resolve(state, metadata['id'])
            if item.get('lastPlanDraft') == receipt:
                return self._view(item, document=True)
            if self._view(item)['revision'] != metadata['revision']:
                error = StaleSourceError()
                error.work_name = item['name']
                raise error
            if item['lifecycleStatus'] in {'completed', 'cancelled'}:
                reject('事项已结束；先 reopen 再修改计划。')
            writes = {}
            self._set_plan(item, markdown, writes, reason, renames)
            item['lastPlanDraft'] = receipt
            if summary is not None:
                item['summary'] = summary
            doc = self._put_entry(doc, item, include=item.get('inbox', True))
            self._commit(state, doc, writes)
            return self._view(item, document=True)

    def plan_show(self, name, version=None, graph=False, history=False):
        from .work_plan import render_plan, graph as render_graph, node_states
        with self._locked():
            state, _ = self._load()
            item = self._resolve(state, name)
            if history:
                versions = [self._plan(item, i) for i in range(1, item.get('planVersion', 0) + 1)]
                return {'name': item['name'], 'versions': [{k: p[k] for k in ('version', 'createdAt', 'reason')} for p in versions]}
            plan = self._plan(item, version)
            if graph and plan.get('legacySnapshot'):
                reject('此版本只保留导入前的计划原文，没有解析依赖图；可用 plan show 查看。')
            statuses = item.get('stepStates') if version is None or version == item.get('planVersion') else plan.get('states')
            return {'name': item['name'], 'planVersion': plan['version'], 'plan': render_plan(plan, statuses),
                    'nodes': node_states(plan, statuses), 'legacySnapshot': bool(plan.get('legacySnapshot')),
                    **({'graph': render_graph(plan, statuses)} if graph else {})}

    def record_event(self, name, text, stage=None, step=None, status=None, summary=None, request_id=None):
        from .work_store import section_replace
        if not text or not text.strip():
            reject('请提供这次进展或结果。')
        if status and not step:
            reject('步骤状态需要 --step 指明步骤。工作状态使用 pause/reopen/finish。')
        with self._locked():
            state, doc = self._load()
            item = self._resolve(state, name)
            fingerprint = hashlib.sha256(json.dumps([text, stage, step, status, summary], ensure_ascii=False).encode()).hexdigest()
            if request_id and request_id in item.get('recordRequests', {}):
                if item['recordRequests'][request_id] != fingerprint:
                    reject('同一请求标识对应不同内容，未覆盖原记录。')
                return self._view(item, document=True)
            if not item.get('workPath'):
                reject('请先 start 建立 Work，再记录执行进展。')
            if item['lifecycleStatus'] in {'completed', 'cancelled'}:
                reject('事项已结束；先 reopen 再继续执行。')
            writes = {}
            if step:
                from .work_plan import render_plan, node_states, validate_completion
                plan = self._plan(item)
                matches = [n for n in plan['nodes'] if n['name'] == step]
                if len(matches) != 1:
                    reject('步骤不存在或不唯一，请使用计划中的完整名称。')
                node = matches[0]
                if status not in {'active', 'done', 'blocked', 'pending', None}:
                    reject('步骤状态使用 active/done/blocked/pending。')
                if status:
                    if status in {'active', 'done'} and any(item.get('stepStates', {}).get(dep) != 'done' for dep in node['after']):
                        reject('前置步骤尚未完成，不能开始或完成此步骤。')
                    item.setdefault('stepStates', {})[node['id']] = status
                    validate_completion(plan, item['stepStates'])
                document = self._work_text(item, writes)
                rendered = re.sub(r'(?m)^#{1,2} ', '### ', render_plan(plan, item.get('stepStates')))
                writes[item['workPath']] = section_replace(document, '当前计划', f"版本：v{item['planVersion']}\n\n" + rendered)
            if stage is not None:
                if '\n' in stage or '\r' in stage:
                    reject('阶段应为单行名称。')
                item['stage'] = stage
            self._event(item, 'record', text, writes, step=step)
            if request_id:
                item.setdefault('recordRequests', {})[request_id] = fingerprint
            if summary is not None:
                item['summary'] = summary
            self._commit(state, self._put_entry(doc, item, include=item.get('inbox', True)), writes)
            return self._view(item, document=True)
