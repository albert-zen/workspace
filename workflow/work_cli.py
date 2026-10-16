"""Command line interface. All mutations share the workspace WorkStore."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from . import WORKSPACE_ROOT
from .inbox_model import InboxError
from .work_store import WorkStore
from .work_resources import ResourceError
from .work_interface import entry, error_detail, render, install


def parser():
    p = argparse.ArgumentParser(prog='work', description='通过语义名称记录、接手和收尾工作。')
    p.add_argument('--root', type=Path, default=WORKSPACE_ROOT, help='保存记录的 workspace')
    p.add_argument('--json', action='store_true', help='输出可编程读取的 JSON')
    sub = p.add_subparsers(dest='command')
    q = sub.add_parser('add', help='记录事项，无需预建 Work')
    q.add_argument('name'); q.add_argument('--summary')
    q = sub.add_parser('list', help='查看事项与状态'); q.add_argument('--all', action='store_true')
    q = sub.add_parser('resume', help='读取本项上下文；显式历史按需展开'); q.add_argument('name')
    q.add_argument('--full', action='store_true', help='包括明确标记的历史记录，读取完整 Work')
    q = sub.add_parser('start', help='建立或复用 Work，可一起创建工作树'); q.add_argument('name')
    q.add_argument('--summary', help='新事项的目标；提供后可一次登记并开始，已有事项保留原上下文')
    q.add_argument('--worktree', action='store_true')
    q.add_argument('--plan-file', type=Path, help='可选的初始 Markdown 计划')
    for option in ('repo', 'path', 'branch', 'base', 'work'):
        q.add_argument('--' + option)
    q = sub.add_parser('add-worktree', help='为事项创建并关联命名工作树；同名复用')
    q.add_argument('name', help='项目/事项')
    q.add_argument('--name', dest='tree_name', required=True, help='工作树用途，如开发、验收')
    q.add_argument('--repo', help='Git 仓库；省略时沿用事项唯一关联仓库')
    q.add_argument('--at', help='新分支的起始提交；省略时从基线分支开始')
    q.add_argument('--base', help='收尾时核对是否已合入的基线分支，默认 main/master')
    q.add_argument('--path', help='可选工作树目录，默认自动生成')
    q.add_argument('--branch', help='可选新分支名，默认自动生成')
    q = sub.add_parser('edit', help='取得安全编辑副本，版本与保存指令自动提供'); q.add_argument('name'); q.add_argument('--output', type=Path, help='可选的副本位置；默认使用独立临时目录')
    q = sub.add_parser('save', help='保存编辑副本；发生冲突时保留双方内容'); q.add_argument('file', type=Path)
    q = sub.add_parser('update', help='按读到的版本更新摘要或 Work 章节'); q.add_argument('name')
    q.add_argument('--summary'); q.add_argument('--description-file', type=Path); q.add_argument('--progress-file', type=Path)
    q.add_argument('--expected-revision', required=True)
    q = sub.add_parser('rename', help='改名并保留身份与旧名定位'); q.add_argument('name'); q.add_argument('new_name')
    for command in ('pause', 'cancel', 'reopen'):
        q = sub.add_parser(command); q.add_argument('name')
        q.add_argument('--reason', help='本次状态变化的说明')
        if command == 'pause':
            q.add_argument('--needs-user', action='store_true', help='进入需要我处理，而非等待中')
    q = sub.add_parser('record', help='替换当前推进并追加历史，不修改事项说明', description='替换当前推进并追加历史，不修改事项说明；持久决定用 edit/save 更新正文，计划用 plan edit/save。')
    q.add_argument('name'); q.add_argument('--text', required=True)
    q.add_argument('--stage'); q.add_argument('--step')
    q.add_argument('--status', choices=['active', 'done', 'blocked', 'pending'])
    q.add_argument('--summary', help='需要时同时更新 Inbox 摘要')
    q.add_argument('--request-id', help=argparse.SUPPRESS)
    q = sub.add_parser('history', help='按需读取本事项的执行记录')
    q.add_argument('name'); q.add_argument('--limit', type=int, default=20)
    q = sub.add_parser('plan', help='编写版本化计划、查看步骤依赖图')
    plans = q.add_subparsers(dest='plan_action', required=True)
    pedit = plans.add_parser('edit', help='取得带版本的计划副本')
    pedit.add_argument('name'); pedit.add_argument('--output', type=Path)
    psave = plans.add_parser('save', help='保存新版本及变更记录')
    psave.add_argument('file', type=Path); psave.add_argument('--summary')
    psave.add_argument('--reason', default='计划已更新。')
    psave.add_argument('--rename-step', nargs=2, action='append', metavar=('OLD', 'NEW'), help='草稿中步骤改名时保留其身份，可重复')
    for action in ('show', 'graph', 'history'):
        pq = plans.add_parser(action); pq.add_argument('name')
        if action != 'history':
            pq.add_argument('--version', type=int)
    q = sub.add_parser('handoff', help='登记承接者；不创建或发送平台任务'); q.add_argument('name'); q.add_argument('--owner', required=True)
    q = sub.add_parser('arrange', help='调整个人关注，不改变共享成果状态'); q.add_argument('name')
    q.add_argument('--status', choices=['Needs you', 'In progress', 'Waiting', 'Done'])
    q.add_argument('--before'); q.add_argument('--note'); q.add_argument('--hide', action='store_true')
    q = sub.add_parser('link', help='登记已有资源，仅关联，不取得删除所有权'); q.add_argument('name')
    q.add_argument('--kind', choices=['worktree', 'branch', 'thread', 'pr', 'material'], required=True)
    for option in ('path', 'repo', 'branch', 'url'):
        q.add_argument('--' + option)
    q = sub.add_parser('release', help='登记资源已不被本事项使用，不删除资源'); q.add_argument('name'); q.add_argument('target')
    q = sub.add_parser('finish', help='记录结果，归档并安全收尾'); q.add_argument('name')
    q.add_argument('--result'); q.add_argument('--dry-run', action='store_true'); q.add_argument('--expected-revision')
    sub.add_parser('recover', help='继续中断的文件操作，冲突时保留外部修改')
    q = sub.add_parser('install', help='安装本机命令入口，不覆盖已有程序或修改 shell 配置')
    q.add_argument('--bin-dir', type=Path, default=Path.home() / '.local/bin')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    store = WorkStore(args.root)
    try:
        command = args.command
        if command is None:
            result = entry(store)
        elif command == 'install':
            result = install(store, args.bin_dir)
        elif command == 'add':
            result = store.add(args.name, args.summary)
        elif command == 'list':
            result = store.list(args.all)
        elif command == 'resume':
            result = store.resume(args.name)
        elif command == 'start':
            result = store.start(args.name, plan_text=args.plan_file.read_text(encoding='utf-8') if args.plan_file else None,
                                 **{k: getattr(args, k) for k in ('worktree', 'repo', 'path', 'branch', 'base', 'work', 'summary')})
        elif command == 'add-worktree':
            result = store.add_worktree(args.name, **{k: getattr(args, k) for k in ('tree_name', 'repo', 'path', 'branch', 'base', 'at')})
        elif command == 'record':
            result = store.record_event(args.name, args.text, args.stage, args.step, args.status, args.summary, args.request_id)
        elif command == 'history':
            result = store.history(args.name, args.limit)
        elif command == 'plan':
            if args.plan_action == 'edit':
                result = store.plan_edit(args.name, args.output)
            elif args.plan_action == 'save':
                result = store.plan_save(args.file, args.summary, args.reason, dict(args.rename_step) if args.rename_step else None)
            else:
                result = store.plan_show(args.name, getattr(args, 'version', None),
                                         graph=args.plan_action == 'graph', history=args.plan_action == 'history')
        elif command == 'edit':
            result = store.edit(args.name, args.output)
        elif command == 'save':
            result = store.save(args.file)
        elif command == 'update':
            result = store.update(args.name, args.summary,
                                  args.description_file.read_text(encoding='utf-8') if args.description_file else None,
                                  args.progress_file.read_text(encoding='utf-8') if args.progress_file else None,
                                  args.expected_revision)
        elif command == 'rename':
            result = store.rename(args.name, args.new_name)
        elif command == 'arrange':
            result = store.arrange(args.name, args.status, args.before, args.note, args.hide)
        elif command == 'link':
            result = store.link(args.name, **{k: getattr(args, k) for k in ('kind', 'path', 'repo', 'branch', 'url')})
        elif command == 'release':
            result = store.release(args.name, args.target)
        elif command == 'finish':
            result = store.finish(args.name, args.result, args.dry_run, args.expected_revision)
        elif command == 'recover':
            result = store.recover()
        else:
            result = store.transition(args.name, command, getattr(args, 'owner', None),
                                      getattr(args, 'reason', None), getattr(args, 'needs_user', False))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(render(store, args, result))
        return 0
    except (InboxError, ResourceError, OSError, ValueError, RuntimeError) as exc:
        detail = error_detail(store, args, exc)
        if args.json:
            print(json.dumps(detail, ensure_ascii=False), file=sys.stderr)
        else:
            print('无法完成：' + detail['message'], file=sys.stderr)
            for line in detail['recovery']:
                print(line, file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
