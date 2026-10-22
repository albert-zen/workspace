"""Human-facing operations and contextual receipts, without workflow prompt configuration."""
from __future__ import annotations

import os
import re
from pathlib import Path
import shlex
import shutil
import sys

from .inbox_model import InboxError
from .work_documents import split_work_sections


LABELS = {'unverified': '待核实', 'planned': '待开始', 'active': '进行中',
          'paused': '已暂停', 'completed': '已完成', 'cancelled': '已取消'}


def command(store, *parts):
    if os.name == 'nt':
        # PowerShell needs an invocation operator for a quoted executable path.
        values = [sys.executable, '-X', 'utf8', str(store.launcher), '--root', str(store.root), *map(str, parts)]
        def quote(value):
            return value if re.fullmatch(r'[a-zA-Z0-9_./-]+', value) else "'" + value.replace("'", "''") + "'"
        return '& ' + ' '.join(quote(value) for value in values)
    installed = shutil.which('work')
    executable = 'work' if installed and Path(installed).resolve() == store.launcher else str(store.launcher)
    root = [] if store.root == store.launcher.parent.parent else ['--root', str(store.root)]
    return shlex.join([executable, *root, *map(str, parts)])


def document_link(store, relative):
    local = store.root / relative
    return str(local if local.is_file() else store.launcher.parent.parent / relative)


def entry(store):
    return {'workspace': str(store.root), 'actions': [
        ['查看事项', command(store, 'list')],
        ['只记下来', command(store, 'add', '项目/事项', '--summary', '想做什么')],
        ['开始新工作', command(store, 'start', '项目/事项', '--summary', '目标和范围')],
        ['添加工作树', command(store, 'add-worktree', '项目/事项', '--name', '开发', '--repo', '/仓库路径')],
        ['接手已有工作', command(store, 'resume', '项目/事项')],
        ['编辑当前上下文', command(store, 'edit', '项目/事项')],
        ['编写或修改计划', command(store, 'plan', 'edit', '项目/事项')],
        ['记录进展', command(store, 'record', '项目/事项', '--text', '当前进展与证据')],
        ['完成并收尾', command(store, 'finish', '项目/事项', '--result', '实际结果与证据入口')],
    ], 'guide': document_link(store, 'practices/collaboration/workflow.md')}


def install(store, directory):
    directory = Path(directory).expanduser().absolute()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ('work.cmd' if os.name == 'nt' else 'work')
    if os.name == 'nt':
        # No developer-mode symlink privilege or PowerShell execution policy needed.
        python = sys.executable.replace('%', '%%')
        launcher = str(store.launcher).replace('%', '%%')
        # cmd decodes batch files using the active code page, not their encoding.
        # Switch before the first non-ASCII line and restore the caller's page.
        wrapper = ('@echo off\nsetlocal DisableDelayedExpansion\n'
                   'for /f "tokens=2 delims=:" %%G in (\'chcp\') do set "_work_cp=%%G"\n'
                   'chcp 65001 >nul\n'
                   f'"{python}" -X utf8 "{launcher}" %*\n'
                   'set "_work_exit=%errorlevel%"\n'
                   'chcp %_work_cp% >nul\n'
                   'exit /b %_work_exit%\n')
        if target.is_symlink() or (target.exists() and target.read_text(encoding='utf-8') != wrapper):
            raise InboxError('命令位置已有其他文件，未覆盖：' + str(target), code='entry_exists')
        if not target.exists():
            with target.open('x', encoding='utf-8', newline='\r\n') as stream:
                stream.write(wrapper)
        on_path = any(Path(p).resolve() == directory.resolve() for p in os.environ.get('PATH', '').split(os.pathsep) if p)
        return {'installedPath': str(target), 'onPath': on_path,
                'defaultWorkspace': str(store.launcher.parent.parent)}
    if target.is_symlink() and target.resolve() == store.launcher:
        pass
    elif target.exists() or target.is_symlink():
        raise InboxError('命令位置已有其他文件，未覆盖：' + str(target), code='entry_exists')
    else:
        target.symlink_to(store.launcher)
    on_path = any(Path(p).resolve() == directory.resolve() for p in os.environ.get('PATH', '').split(os.pathsep) if p)
    return {'installedPath': str(target), 'onPath': on_path,
            'defaultWorkspace': str(store.launcher.parent.parent)}


def context_text(text, full=False):
    """Only explicitly historical sections may be hidden; legacy/unknown context stays visible."""
    sections = split_work_sections(text)
    titles = [s['title'] for s in sections if s['level'] == 2]
    if full or titles.count('事项说明') != 1 or titles.count('当前推进') != 1:
        return text, []
    visible, omitted = [], []
    for section in sections:
        if section['level'] == 2 and section['title'] == '历史记录':
            omitted.append(section['line'])
        else:
            visible.append(section['markdown'])
    return ''.join(visible), omitted


def render(store, args, result):
    if args.command is None:
        return '\n'.join(['工作空间：' + result['workspace'],
            '按现在要做的事选择；简单问答不用登记 Work。',
            *[label + '：' + cmd for label, cmd in result['actions']],
            '操作参数：' + command(store, 'COMMAND', '--help'),
            '流程含义：' + result['guide']])
    if args.command == 'install':
        return '\n'.join(['命令入口：' + result['installedPath'],
                          '默认工作空间：' + result['defaultWorkspace'],
                          '已在 PATH 中，可从其他目录运行 work。' if result['onPath'] else
                          '当前 PATH 不含该目录；可直接运行上面的绝对路径，或将该目录加入 PATH。'])
    if isinstance(result, list):
        return '\n'.join(f"{r['name']}  [{LABELS.get(r['lifecycleStatus'], r['lifecycleStatus'])}]  {r.get('summary', '')}" for r in result) or '当前没有事项。'
    if 'name' not in result:
        return '文件操作已恢复。\n查看事项：' + command(store, 'list')

    name = result['name']
    if args.command == 'history':
        blocks = []
        for event in result['events']:
            block = f"{event['sequence']} · {event['time']} · {event['kind']}"
            if event['planVersion']:
                block += f" · 计划 v{event['planVersion']}"
            if event.get('step'):
                block += f" · {event['step']}：{event.get('stepStatus', '')}"
            blocks.append(block + '\n' + event['text'])
        return '\n'.join([name, *blocks])
    if args.command == 'plan':
        if args.plan_action == 'edit':
            return '\n'.join([name, '计划副本：' + result['draftPath'],
                              '编辑后保存：' + command(store, 'plan', 'save', result['draftPath']),
                              '可用普通 Markdown；步骤：- [ ] 名称；下一行缩进写依赖：步骤名、步骤名。'])
        if 'versions' in result:
            return '\n'.join([name, *[f"v{p['version']} · {p['createdAt']} · {p['reason']}" for p in result['versions']]])
        if 'plan' in result:
            return '\n'.join([name + ' · 计划 v' + str(result['planVersion']), result.get('graph', result['plan']),
                              *(['此版本保留导入前的原文，未解析步骤。'] if result.get('legacySnapshot') else []),
                              *[f"{n['name']}：{n['state']}" for n in result['nodes']]])
        return name + '：已保存计划 v' + str(result['planVersion'])
    lines = [name, '工作状态：' + LABELS.get(result.get('lifecycleStatus'), '预览')]
    for key, label in [('attention', '个人关注'), ('arrangement', '个人安排'), ('owner', '负责人'),
                       ('absoluteWorkPath', 'Work'), ('draftPath', '编辑副本')]:
        if result.get(key):
            lines.append(label + '：' + str(result[key]))
    if result.get('stage'):
        lines.append('阶段：' + result['stage'])
    if result.get('documentMissing'):
        lines.append('关联 Work 缺失，请核对材料位置；当前仅有登记信息。')
    for resource in result.get('resources', []):
        target = resource.get('path') or resource.get('url') or resource.get('branch', '')
        lines.append('关联' + resource['kind'] + ('（' + resource['name'] + '）' if resource.get('name') else '') + '：' + str(target))
        if resource.get('status') == 'cleaned':
            lines.append('资源已清理。')
        elif resource.get('released'):
            lines.append('本事项已解除占用。')
        for archive in resource.get('cleanup', {}).get('archives', []):
            lines.append('额外文件可恢复归档：' + archive)
    for observation in result.get('resourceObservations', []):
        if observation.get('reason'):
            lines.append('资源状态：' + observation['reason'])
    for reason in result.get('closeoutPending', []):
        lines.append('待收尾：' + reason)

    if args.command in {'start', 'resume'} and result.get('document'):
        body, omitted = context_text(result['document'], getattr(args, 'full', False))
        lines.extend(['', body.rstrip()])
        for line in omitted:
            lines.append(f"历史记录未展开：{result['absoluteWorkPath']}:{line}")
        if omitted:
            lines.append('完整正文：' + command(store, 'resume', name, '--full'))
    elif result.get('summary'):
        lines.append(result['summary'])

    if args.command in {'start', 'resume', 'add-worktree'}:
        rules = set()
        for resource in result.get('resources', []):
            if resource['kind'] == 'worktree' and not resource.get('released') and resource.get('status') != 'cleaned':
                for directory in (resource.get('repo'), resource.get('path')):
                    if directory:
                        path = Path(directory) / 'AGENTS.md'
                        if path.is_file():
                            rules.add(str(path))
        lines.extend('项目入口：' + path for path in sorted(rules))
        if result.get('workPath') and not result.get('documentMissing'):
            lines.append('需要更新上下文时：' + command(store, 'edit', name))
        elif not result.get('workPath') and result.get('lifecycleStatus') not in {'completed', 'cancelled'}:
            lines.append('开始持续工作：' + command(store, 'start', name))
        if args.command == 'start' and args.summary:
            lines.append('重复开始会复用已有事项及其上下文；--summary 仅用于新建。')
    elif args.command == 'edit':
        lines.append('编辑上面的副本后保存：' + command(store, 'save', result['draftPath']))
        lines.append('需要整理上下文时：' + document_link(store, 'practices/collaboration/context.md'))
    elif args.command == 'add':
        lines.append('需要开始时：' + command(store, 'start', name))
    elif args.command == 'finish':
        if result.get('dryRun'):
            lines.append('计划：保存结果、核对共享关系后归档 Work，逐项检查临时资源。')
            for observation in result.get('resourceObservations', []):
                if observation.get('merged') and observation.get('only_ignored'):
                    lines.append('计划：先将 ignored 文件移至可恢复归档，再清理工作树；不按目录名猜测可删除内容。')
                elif observation.get('dirty'):
                    lines.append('保留工作树：含未提交或未跟踪文件。')
                elif observation.get('merged') is False:
                    lines.append('保留工作树：存在尚未进入基线分支的提交。')
            lines.append('仅预览，未执行收尾。')
        elif result.get('closeoutPending'):
            lines.append('上述原因解除后继续收尾：' + command(store, 'finish', name))
        else:
            lines.append('已保存完成结果，本机收尾已完成。')
    elif args.command == 'save':
        lines.append('已保存当前上下文。')
        lines.append('继续编辑时取得新副本：' + command(store, 'edit', name))
    return '\n'.join(lines)


def error_detail(store, args, exc):
    code = getattr(exc, 'code', 'operation_failed')
    recovery = []
    name = getattr(exc, 'work_name', None) or getattr(args, 'name', None)
    message = str(exc)
    if code in {'not_found', 'ambiguous_name', 'duplicate_name'}:
        recovery.append('核对已有事项：' + command(store, 'list', '--all'))
        if code == 'not_found' and args.command == 'start':
            recovery.append('明确新建时提供目标：' + command(store, 'start', args.name, '--summary', '目标和范围'))
    elif code == 'work_ended':
        recovery.append('确认要继续此事项后：' + command(store, 'reopen', name))
    elif code == 'work_not_started':
        recovery.append('建立上下文：' + command(store, 'start', name))
    elif code == 'pending_operation':
        recovery.append('继续中断的文件操作：' + command(store, 'recover'))
    elif code == 'stale_source':
        message = '事项或 Work 已改变，未覆盖最新内容；请先核对当前记录。'
        if args.command == 'save' or args.command == 'plan' and args.plan_action == 'save':
            recovery.append('保留的副本：' + str(args.file.resolve()))
        if name:
            recovery.append('查看最新上下文：' + command(store, 'resume', name))
            if args.command in {'save', 'update'}:
                recovery.extend(['需要编辑正文时：' + command(store, 'edit', name),
                                 '将需要保留的修改合入新副本，再用返回的保存命令写回。'])
            if args.command in {'update', 'finish'}:
                recovery.extend(['读取当前版本：' + command(store, '--json', 'resume', name),
                                 '核对目标与当前内容后，用新的 revision 重试原操作。'])
            if args.command == 'plan':
                recovery.append('取得最新计划副本：' + command(store, 'plan', 'edit', name))
        else:
            recovery.append('核对所属事项：' + command(store, 'list', '--all'))
    else:
        recovery.append('操作说明：' + command(store, args.command, '--help'))
    return {'error': code, 'message': message, 'recovery': recovery}
