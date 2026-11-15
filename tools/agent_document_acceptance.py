"""Real-model document extraction, replacement and split recovery on ordinary records."""
import json,re,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_core.engine import Engine
from agent_core.errors import Cancelled
from agent_core.models import ModelConfig
from agent_core.llm_client import OllamaClient
from agent_core.store import atomic_json
from agent_core.workflows.planner import create_task

root=Path(sys.argv[1]).resolve();root.mkdir(parents=True,exist_ok=True)
source=root/'设备维护记录.txt'
rows=[];expected=[];excluded=[]
for i in range(1,31):
    tag=f'记录{i:03d}'
    urgent=i%3!=0
    if urgent:
        expected.append(tag)
        rows.append(f'{tag}：紧急检修。负责人王工检查编号{i:03d}水泵，发现接头松动，完成紧固后运行正常。紧急检修验收要求：保留测试数据，次日复核压力。')
    else:
        excluded.append(tag)
        rows.append(f'{tag}：日常巡检。负责人王工擦拭设备外壳，记录仪表读数，所有设备正常，不涉及维修。')
source.write_text('\n'.join(rows),encoding='utf-8')
instruction='逐字摘取所有“紧急检修”记录的完整内容，保留记录编号、所有事实和格式，只将“王工”替换为“李明”。不摘取日常巡检，不摘要、不改写，不输出解释。'
report={'source_chars':len(source.read_text(encoding='utf-8')),'cases':[]};started=time.monotonic()
for predict in [256,2048]:
    config=ModelConfig('qwen3.6-jailbreak:q6',num_ctx=8192,num_predict=predict,read_timeout=300)
    store=create_task(root/'tasks','documents','extract',[source],instruction,config)
    client=OllamaClient(config);original=store.event;paused=False
    if predict==256:
        def pause(name,**fields):
            original(name,**fields)
            if name=='document_substep_completed' and fields.get('node','').startswith('root.'):
                raise Cancelled('验收：文档子结果提交后暂停')
        store.event=pause
        state=Engine(store,client).run()
        assert state['status']=='paused',state
        paused=True;store.event=original
    state=Engine(store,client).run()
    output='\n'.join(store.path(path).read_text(encoding='utf-8') for path in state.get('outputs',[]))
    found=re.findall(r'记录\d{3}',output)
    normalized=re.sub(r'\s','',output)
    mismatches=[row.split('：',1)[0] for row in rows if '紧急检修' in row and re.sub(r'\s','',row.replace('王工','李明')) not in normalized]
    result={'task':str(store.root),'status':state['status'],'num_ctx':config.num_ctx,'num_predict':predict,
            'paused_and_resumed':paused,'output_chars':len(output),'missing':[tag for tag in expected if tag not in found],
            'unexpected':[tag for tag in excluded if tag in found],'duplicates':[tag for tag in expected if found.count(tag)>1],
            'replacement_applied':'李明' in output and '王工' not in output,'content_mismatches':mismatches,
            'split_count':len(list(store.root.rglob('split.json'))),
            'cache_reused':'document_substep_reused' in (store.root/'logs/events.jsonl').read_text(encoding='utf-8')}
    report['cases'].append(result);atomic_json(root/'report.json',report)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    assert state['status']=='completed',state
    assert not result['missing'] and not result['unexpected'] and not result['duplicates'],result
    assert result['replacement_applied'] and not result['content_mismatches'],result
    if predict==256:assert result['split_count'] and result['cache_reused'],result
report['elapsed_seconds']=round(time.monotonic()-started,2)
atomic_json(root/'report.json',report)
