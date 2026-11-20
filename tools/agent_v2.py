"""Agent v2 phase-one CLI. No Qt or legacy Agent dependencies."""
import argparse
import json
from pathlib import Path
import signal
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from Agent.agent_core.engine import Engine
from Agent.agent_core.llm_client import OllamaClient
from Agent.agent_core.models import Cancellation, ModelConfig
from Agent.agent_core.store import TaskStore
from Agent.agent_core.workflows.documents import create_document_task


def main():
    parser=argparse.ArgumentParser(description='Agent v2：可恢复的TXT/Markdown分段摘要')
    subs=parser.add_subparsers(dest='command',required=True)
    create=subs.add_parser('create')
    create.add_argument('--input',nargs='+',required=True)
    create.add_argument('--instruction',default='提取重要事实与要点，不添加原文没有的信息。')
    create.add_argument('--data-dir',type=Path,default=ROOT/'userdata/agent_tasks')
    create.add_argument('--model',default='qwen3.6-jailbreak:q6')
    create.add_argument('--base-url',default='http://127.0.0.1:11434')
    create.add_argument('--num-ctx',type=int,default=8192)
    create.add_argument('--num-predict',type=int,default=1024)
    create.add_argument('--chunk-chars',type=int,default=1600)
    create.add_argument('--encoding',default='utf-8-sig')
    create.add_argument('--read-timeout',type=float,default=120)
    for name in ('run','inspect','pause'):
        item=subs.add_parser(name)
        item.add_argument('task',type=Path)
        if name=='run':
            item.add_argument('--max-steps',type=int)
    args=parser.parse_args()
    if args.command=='create':
        config=ModelConfig(model=args.model,base_url=args.base_url,num_ctx=args.num_ctx,num_predict=args.num_predict,chunk_chars=args.chunk_chars,read_timeout=args.read_timeout)
        store=create_document_task(args.data_dir,args.input,args.instruction,config,encoding=args.encoding)
        print(str(store.root),flush=True)
        return 0
    store=TaskStore(args.task)
    if args.command=='inspect':
        state=store.read_json('state.json')
    elif args.command=='pause':
        state={'pause_requested_for_run':store.request_pause()}
    else:
        token=Cancellation()
        signal.signal(signal.SIGINT,lambda *_:token.cancel())
        config=ModelConfig(**store.task()['config'])
        def event(value):
            if value['event'] in ('step_started','step_succeeded','step_attempt_failed','run_paused','run_failed','run_completed'):
                print(json.dumps(value,ensure_ascii=False),flush=True)
        state=Engine(store,OllamaClient(config)).run(token,max_steps=args.max_steps,on_event=event)
    print(json.dumps(state,ensure_ascii=False,indent=2),flush=True)
    return 0 if state.get('status') not in ('failed','needs_input') else 2


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr)
        raise SystemExit(2)
