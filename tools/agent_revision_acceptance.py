"""Real local-model revision and substep-recovery acceptance on synthetic fiction."""
import json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from Agent.agent_core.models import ModelConfig,Cancellation
from Agent.agent_core.store import atomic_json
from Agent.agent_core.workflows.planner import create_task
from Agent.agent_core.engine import Engine
from Agent.agent_core.approvals import revise_chapter,confirm_chapter
from Agent.agent_core.llm_client import OllamaClient
from Agent.agent_core.summary_steps import summarize_in_steps
from Agent.agent_core.errors import Cancelled,NeedsInput

root=Path(sys.argv[1]).resolve();root.mkdir(parents=True,exist_ok=True)
source=root/'普通悬疑大纲.txt'
source.write_text('雨夜，年轻邮差林舟发现父亲留下的旧信，走访修钟老人，调查废弃车站，得知父亲正在保护旧档案。最后父子重逢，一起修复老车站。保持日常悬疑和亲情主题。',encoding='utf-8')
config=ModelConfig('qwen3.6-jailbreak:q6',num_predict=2048,read_timeout=300)
client=OllamaClient(config)
store=create_task(root/'tasks','fiction','new',[source],'按照大纲写3000字普通悬疑小说，分五个场景，强调线索和亲情。',config,{'chapters':1})
def event(e):print(e['event'],e.get('step_id',''),e.get('error',''),flush=True)
report={'task':str(store.root)};started=time.monotonic()
state=Engine(store,client).run(on_event=event)
assert state['status']=='awaiting_approval',state
old=state['approval']['draft_hash'];report['initial_length']=state['approval']['length_report']
print('REVISION',flush=True)
revise_chapter(store,'把故事天气改为晴天，删除雨夜和下雨的描写。保留林舟寻父、旧信和车站的主线，结尾增加父子一起修复车站的承诺。',old)
paused=Engine(store,client).run(max_steps=3,on_event=event)
assert paused['status']=='paused',paused
events=[]
def resumed_event(e):events.append(e);event(e)
state=Engine(store,client).run(on_event=resumed_event)
assert state['status']=='awaiting_approval',state
assert state['approval']['draft_hash']!=old
draft=store.path(state['approval']['draft_path']).read_text(encoding='utf-8')
assert '晴' in draft,draft[:100]
report['revision_length']=state['approval']['length_report']
report['revision_resume_reused']=sum(e['event']=='step_reused' for e in events)
try:confirm_chapter(store,old)
except NeedsInput:report['stale_confirmation_rejected']=True
else:raise AssertionError('old draft confirmation accepted')
confirm_chapter(store,state['approval']['draft_hash'])
state=Engine(store,client).run(on_event=event)
assert state['status']=='completed',state
report['status']=state['status'];atomic_json(root/'report.json',report)
print('SUMMARY_RECOVERY',flush=True)
material='\n'.join(f'材料记录{i+1}：林舟在邮局发现旧信，信上的邮戳指向废弃车站。修钟老人提供旧钥匙。父亲保护档案并留下线索，林舟沿线索寻找父亲。父子见面后约定一起修复车站。' for i in range(65))
step={'id':'summary-recovery-check','max_chars':700}
store.write_json('steps/summary-recovery-check/input.json',{'input_hash':'synthetic-summary-v1'})
original_event=store.event
def pause_after_child(name,**fields):
    original_event(name,**fields)
    if name=='summary_substep_completed' and fields.get('node')=='root.1':raise Cancelled('验收：子摘要完成后暂停')
store.event=pause_after_child
try:summarize_in_steps(store,step,material,config,client,Cancellation(),store.new_attempt(step['id']))
except Cancelled:report['summary_paused_after_commit']=True
finally:store.event=original_event
body=summarize_in_steps(store,step,material,config,client,Cancellation(),store.new_attempt(step['id']))
report['summary_chars']=len(body)
report['summary_reused']='summary_substep_reused' in (store.root/'logs/events.jsonl').read_text(encoding='utf-8')
assert report.get('summary_paused_after_commit') and report['summary_reused']
assert 0<len(body)<=700
report['elapsed_seconds']=round(time.monotonic()-started,2)
atomic_json(root/'report.json',report);print(json.dumps(report,ensure_ascii=False),flush=True)
