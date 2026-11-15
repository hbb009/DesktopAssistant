"""Real-model document acceptance using two synthetic source documents only."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from agent_core.engine import Engine
from agent_core.llm_client import OllamaClient
from agent_core.models import ModelConfig
from agent_core.store import TaskStore, atomic_json, now
from agent_core.workflows.documents import create_document_task


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=['documents'],default='documents')
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--model',default='qwen3.6-jailbreak:q6')
    parser.add_argument('--base-url',default='http://127.0.0.1:11434')
    parser.add_argument('--num-ctx',type=int,default=8192)
    parser.add_argument('--read-timeout',type=float,default=120)
    args=parser.parse_args()
    if args.workspace.exists():
        raise ValueError('验收目录必须是尚未存在的新目录，避免覆盖已有材料。')
    args.workspace.mkdir(parents=True)
    texts={
        'project.txt':'项目执行记录：周一整理需求并建立材料清单；周二制作初稿；周三由两人核对数字和来源。发现错误时记录原因，修订后保留旧版本。每次保存后检查文件能够重新打开。发布之前确认检查清单全部完成。',
        'garden.md':'阳台植物观察：每天早晨查看土壤湿度，土壤表层干燥后再浇水。连续阴天减少浇水频次。每周记录新叶数量和叶片颜色，把异常变化和处理方法写入观察表。不同植物分别记录，不能将一盆植物的观察结果作为全部植物的结论。',
    }
    for name,text in texts.items():
        (args.workspace/name).write_text(text,encoding='utf-8')
    config=ModelConfig(model=args.model,base_url=args.base_url,num_ctx=args.num_ctx,num_predict=512,chunk_chars=400,read_timeout=args.read_timeout)
    store=create_document_task(args.workspace/'tasks',[args.workspace/name for name in texts],
                               '用中文写出80到150字的摘要，保留材料中的主要事实，直接输出正文。',config)
    report={'started_at':now(),'task':str(store.root),'config':config.to_dict()}
    started=time.monotonic()
    def event(value):
        if value['event'] in ('step_started','step_succeeded','step_attempt_failed','run_failed','run_completed'):
            print(json.dumps(value,ensure_ascii=False),flush=True)
    client=OllamaClient(config)
    first=Engine(store,client).run(max_steps=1,on_event=event)
    report['paused_after_one_step']=first['status']=='paused'
    if first['status']!='paused':
        report['error']=first
    else:
        before={str(p.relative_to(store.root)):p.read_bytes() for p in (store.root/'steps').glob('*/commit.json')}
        second=Engine(TaskStore(store.root),client).run(on_event=event)
        report['final_status']=second['status']
        report['prior_commits_unchanged']=all((store.root/p).read_bytes()==body for p,body in before.items())
        report['outputs']=second.get('outputs',[])
        report['source_unchanged']=all((args.workspace/name).read_text(encoding='utf-8')==text for name,text in texts.items())
        if second['status']!='completed':
            report['error']=second
    report['elapsed_seconds']=time.monotonic()-started
    report['passed']=bool(report.get('paused_after_one_step') and report.get('final_status')=='completed' and report.get('prior_commits_unchanged') and report.get('source_unchanged') and len(report.get('outputs',[]))==2)
    atomic_json(args.workspace/'acceptance-report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['passed'] else 2


if __name__=='__main__':
    raise SystemExit(main())
