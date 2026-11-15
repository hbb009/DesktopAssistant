"""Small, non-user-data model capability probe. Opt-in network execution."""
import argparse
import json
from pathlib import Path
import platform
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from agent_core.llm_client import OllamaClient
from agent_core.models import Cancellation, ModelConfig
from agent_core.store import atomic_json, now


def api(base,path,payload=None):
    request=urllib.request.Request(base.rstrip('/')+path,
        data=json.dumps(payload).encode('utf-8') if payload is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=10) as response:
        return json.load(response)


def probe(config, output):
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    report={'at':now(),'platform':platform.platform(),'config':config.to_dict(),'checks':{}}
    report['loaded_before']=api(config.base_url,'/api/ps')
    shown=api(config.base_url,'/api/show',{'model':config.model})
    report['model_info']={k:v for k,v in shown.get('model_info',{}).items() if k.endswith(('context_length','parameter_count'))}
    client=OllamaClient(config)
    for name,user,fmt in (
        ('plain','请只写一条简短的项目检查建议，直接给出内容，不要调用工具。',None),
        ('json','只返回JSON对象，字段 count 的整数值为3。','json'),
    ):
        request={'model':config.model,'messages':[{'role':'user','content':user}],'stream':True,'think':False,
                 'options':{'num_ctx':config.num_ctx,'num_predict':config.num_predict,'temperature':0},'keep_alive':'5m'}
        if fmt:
            request['format']=fmt
        try:
            text=client.generate(request,output/name,Cancellation())
            valid=bool(text.strip()) if name=='plain' else json.loads(text).get('count')==3
            report['checks'][name]={'passed':valid,'response_chars':len(text)}
        except Exception as exc:
            report['checks'][name]={'passed':False,'error':str(exc)}
        atomic_json(output/'probe-report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',default='qwen3.6-jailbreak:q6')
    parser.add_argument('--base-url',default='http://127.0.0.1:11434')
    parser.add_argument('--num-ctx',type=int,default=8192)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=probe(ModelConfig(model=args.model,base_url=args.base_url,num_ctx=args.num_ctx,num_predict=256),args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if all(v['passed'] for v in result['checks'].values()) else 2


if __name__=='__main__':
    raise SystemExit(main())
