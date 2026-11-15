"""Non-explicit synthetic prose acceptance, never resumes user writing jobs."""
import json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_core.conversation import inventory,interpret
from agent_core.models import ModelConfig,Cancellation
from agent_core.llm_client import OllamaClient
from agent_core.workflows.planner import create_task
from agent_core.engine import Engine
from agent_core.lengths import prose_chars
from agent_core.store import atomic_json
root=Path(sys.argv[1]).resolve(); root.mkdir(parents=True,exist_ok=True)
material=root/'materials'; material.mkdir(exist_ok=True)
for i in range(30): (material/f'无关材料{i:02d}.txt').write_text('这份材料与当前任务无关，不需要读取。',encoding='utf-8')
previous=root/'previous'; previous.mkdir(exist_ok=True)
outline=previous/'故事大纲.md'; outline.write_text('普通悬疑故事大纲：年轻邮差林舟发现父亲留下的旧信，沿线索寻找失踪的父亲。第一场在邮局发现邮戳；第二场走访修钟老人；第三场调查废弃车站；第四场发现父亲保护旧档案的真相；第五场父子重逢并决定修复老车站。全篇无色情内容，重点是日常细节、线索和亲情。',encoding='utf-8')
config=ModelConfig('qwen3.6-jailbreak:q6',num_predict=1024,read_timeout=300)
intent=interpret('好，下面将这个故事大纲，写成3000字的正文',inventory(material,recent_outputs=[outline]),config,OllamaClient(config),Cancellation(),root/'routing',
                 '最近已完成普通悬疑小说大纲，邮差寻找失踪父亲；成果R1。')
atomic_json(root/'intent.json',intent)
assert intent['action']=='task' and intent['workflow']=='fiction',intent
assert intent['paths']==[str(outline)] and intent['options']['target_chars']==3000,intent
config=ModelConfig('qwen3.6-jailbreak:q6',num_predict=2048,read_timeout=300)
store=create_task(root/'tasks',intent['workflow'],intent['mode'],intent['paths'],intent['instruction'],config,intent['options'])
print('TASK',store.root,'STEPS',len(store.plan()['steps']),flush=True)
started=time.monotonic()
state=Engine(store,OllamaClient(config)).run(on_event=lambda e:print(e['event'],e.get('step_id',''),e.get('error',''),flush=True))
report={'task':str(store.root),'state':state,'elapsed_seconds':round(time.monotonic()-started,2),'source_count':len(intent['paths'])}
if state.get('approval'):
    draft=store.path(state['approval']['draft_path']).read_text(encoding='utf-8')
    report['draft_chars']=prose_chars(draft)
atomic_json(root/'report.json',report)
print(json.dumps(report,ensure_ascii=False),flush=True)
assert state['status']=='awaiting_approval',state
assert 2700<=report['draft_chars']<=3320,report
