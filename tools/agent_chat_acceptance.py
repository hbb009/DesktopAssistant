import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent_core.conversation import interpret,inventory
from agent_core.models import ModelConfig,Cancellation
from agent_core.llm_client import OllamaClient
from agent_core.store import atomic_json
root=Path(sys.argv[1]).resolve(); root.mkdir(parents=True,exist_ok=True)
material=root/'materials'; material.mkdir(exist_ok=True)
(material/'第01章.txt').write_text('夜雨。邮差收到一封旧信，决定寻找失踪的父亲。',encoding='utf-8')
(material/'故事大纲.md').write_text('邮差寻找失踪父亲，在线索与选择中改变自己。',encoding='utf-8')
skill=material/'我的文风'; skill.mkdir(exist_ok=True); (skill/'SKILL.md').write_text('使用简短句子，动作与对话推动故事，不借用原作人物。',encoding='utf-8')
config=ModelConfig('qwen3.6-jailbreak:q6',num_ctx=8192,num_predict=1024,read_timeout=300)
cases=[
 ('把 https://example.com/book/ 这个小说目录里的章节都保存到工作目录','web','catalog',{}),
 ('把目录里的两个文档合并成一个TXT文件','documents','merge',{}),
 ('根据目录里的小说材料提炼作者文风，制作可用于其他故事的Skill','style','build',{}),
 ('修改现有故事大纲，让主角寻找父亲的动机更可信，安排两章','outline','modify',{'chapters':2}),
 ('加载我的文风Skill，承接已有小说，从第81章开始续写两章，每章等我确认','fiction','continue',{'start_chapter':81,'chapters':2}),
 ('把工作目录的文档里的“夜雨”全部替换为“清晨”，保留原文件','documents','replace',{'find':'夜雨','replace':'清晨'}),
 ('写一个邮差寻找失踪父亲的悬疑小说大纲，共5章','outline','new',{'chapters':5}),
]
results=[]
for i,(text,workflow,mode,options) in enumerate(cases,1):
    print('START',i,text,flush=True); started=time.monotonic()
    try:
        intent=interpret(text,inventory(material),config,OllamaClient(config),Cancellation(),root/f'case-{i}')
        ok=intent.get('workflow')==workflow and intent.get('mode')==mode and all(intent.get('options',{}).get(k)==v for k,v in options.items())
        results.append({'text':text,'passed':ok,'intent':intent,'seconds':round(time.monotonic()-started,2)})
    except Exception as exc: results.append({'text':text,'passed':False,'error':str(exc)})
    atomic_json(root/'report.json',results); print('RESULT',json.dumps(results[-1],ensure_ascii=False),flush=True)
assert all(r['passed'] for r in results),results
