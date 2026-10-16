#!/usr/bin/env python3
"""Create fictional Work and Inbox data in a new, explicitly chosen directory."""
from pathlib import Path
import argparse
import subprocess
import sys

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('destination',type=Path)
args=parser.parse_args()
root=args.destination.resolve()
if root.exists() and any(root.iterdir()):
    parser.error('Destination must be empty; existing data is never overwritten.')
root.mkdir(parents=True,exist_ok=True)
cli=Path(__file__).resolve().parents[1]/'bin/work'
for name,summary in [('示例项目/整理读书会安排','虚构练习：整理材料并保留待确认信息，不发送邀请。'),('示例项目/核对场地清单','虚构练习：比较两个场地的条件，不进行预订。')]:
    subprocess.run([sys.executable,str(cli),'--root',str(root),'start',name,'--summary',summary],check=True)
print('Fictional demo created:',root)
