"""Small synthetic acceptance against the user's actual local Ollama model."""
import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from Agent.agent_core.models import ModelConfig
from Agent.agent_core.workflows.planner import create_task
from Agent.agent_core.engine import Engine
from Agent.agent_core.llm_client import OllamaClient
from Agent.agent_core.store import atomic_json

root=Path(sys.argv[1]).resolve(); root.mkdir(parents=True,exist_ok=True)
source=root/'sample.txt'
source.write_text('第一章 夜雨\n雨水顺着屋檐滴落。林舟推开门，把那封旧信放在桌上。\n“谁送来的？”阿澄问。\n他没有回答，只把信翻到背面。那枚蓝色邮戳，他曾在父亲的笔记里见过。\n第二章 车站\n清早，他们来到废弃的车站。长椅上压着一张纸条，上面只有一个日期。林舟握紧信封，决定去旧邮局寻找答案。',encoding='utf-8')
config=ModelConfig('qwen3.6-jailbreak:q6',num_predict=2048,read_timeout=300)
report=[]
for workflow,mode in [('documents','summary'),('style','build'),('outline','new'),('fiction','new')]:
    options={'chapters':1,'scenes':1,'export_root':str(root)}
    if workflow=='fiction':
        styles=list((root/'tasks').glob('*/outputs/author-style/SKILL.md'))
        if styles: options['skill']=str(styles[-1].parent)
    store=create_task(root/'tasks',workflow,mode,[source] if workflow in ('documents','style','fiction') else [],
                      '写原创悬疑故事：主角寻找失踪亲人。保持简洁、动作与对话推动情节，不照抄材料。',config,options)
    print('START',workflow,str(store.root),flush=True)
    started=time.monotonic()
    state=Engine(store,OllamaClient(config)).run(on_event=lambda e:print(e['event'],e.get('step_id',''),e.get('error',''),flush=True))
    report.append({'workflow':workflow,'task':str(store.root),'seconds':round(time.monotonic()-started,2),'state':state})
    atomic_json(root/'report.json',report)
    print('RESULT',workflow,state['status'],state.get('error',''),flush=True)
