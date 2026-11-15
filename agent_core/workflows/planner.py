"""Deterministic workflow plans. The model produces content, not filesystem actions."""
import hashlib
import re
from pathlib import Path

from ..adapters.documents import read_document, chunks
from ..errors import NeedsInput
from ..skill_loader import skill_files
from ..store import TaskStore, atomic_bytes, fingerprint, now
from ..lengths import apply_length, scene_lengths


WORKFLOWS={'web':'网站助手','documents':'文档处理','style':'作者风格Skill','outline':'小说大纲','fiction':'小说写作'}
DOC_MODES={'summary':'摘要','rewrite':'改写','proofread':'校对','translate':'翻译','extract':'内容提取','compare':'文档比较',
           'search':'正文搜索','stats':'文件统计','replace':'批量替换','merge':'合并','split':'分章拆分','convert':'格式转换',
           'rename':'批量改名副本','classify':'分类副本','duplicates':'重复文件识别','copy':'复制','move':'移动'}


class Builder:
    def __init__(self,store,sources,config):
        self.store,self.sources,self.config=store,sources,config
        self.steps=[]
        self.serial=0

    def add(self,kind,dependencies=(),**fields):
        self.serial+=1
        sid=f'step-{self.serial:05d}'
        self.steps.append({'id':sid,'kind':kind,'dependencies':list(dependencies),**fields})
        return sid

    def generate(self,prompt,dependencies=(),**fields):
        return self.add('generate',dependencies,prompt=prompt,**fields)

    def reduce(self,items,prompt='reduce'):
        items=list(items)
        while len(items)>1:
            items=[self.generate(prompt,items[i:i+2],max_chars=700) for i in range(0,len(items),2)]
        return items[0] if items else None

    def analyze(self,source_ids,prompt='facts'):
        documents=[]
        for source in self.sources:
            if source['id'] not in source_ids:
                continue
            text=self.store.path(source['snapshot']).read_bytes().decode('utf-8')
            parts=[]
            byte_start=0
            for start,end in chunks(text,min(self.config.chunk_chars,1600)):
                byte_end=byte_start+len(text[start:end].encode('utf-8'))
                parts.append(self.generate(prompt,document=source['id'],start=start,end=end,
                                           byte_start=byte_start,byte_end=byte_end,max_chars=700,
                                           evidence=f"{source['id']}:{start}-{end}"))
                byte_start=byte_end
            chapter=self.reduce(parts)
            documents.append(chapter)
        return self.reduce(documents),documents


def create_task(parent,workflow,mode,paths,instruction,config,options=None,cancellation=None):
    if workflow not in WORKFLOWS:
        raise NeedsInput('未知任务类型。')
    options=dict(options or {})
    if workflow=='fiction' and not options.get('target_chars'):
        apply_length(options,instruction)
    if workflow=='fiction' and options.get('target_chars'):
        import math
        target=options['target_chars']
        if type(target) is not int or not 300<=target<=20000:
            raise NeedsInput('正文目标字数须在300～20000之间。')
        options['scenes']=max(1,math.ceil(target/600))
    if not instruction.strip() and workflow not in ('web','documents'):
        raise NeedsInput('请填写具体创作要求。')
    if workflow in ('style','documents') and not paths:
        raise NeedsInput('请选择需要处理的文件。')
    if workflow=='outline' and mode in ('modify','continue') and not paths:
        raise NeedsInput('修改或续篇大纲需要选择已有大纲/小说。')
    if workflow=='fiction' and mode in ('continue','revise','rewrite') and not paths:
        raise NeedsInput('续写、修改、重写需要选择已有正文或大纲。')
    count=int(options.get('chapters',3))
    first=int(options.get('start_chapter',1))
    if not 1<=first<=9999:
        raise NeedsInput('起始章号须在1～9999之间。')
    if not 1<=count<=80:
        raise NeedsInput('章节数量须在1～80之间。')
    store=TaskStore.create(parent)
    store.write_json('state.json',{'status':'planning','steps':{},'run_id':None})
    sources=[]
    main_ids=[]
    skill_ids=[]
    entries=[(Path(p),'source') for p in paths]+[(p,'skill') for p in skill_files(options.get('skill'))]
    seen={}
    try:
        for path,role in entries:
            if cancellation:
                cancellation.check()
            path=path.resolve()
            if path in seen:
                (skill_ids if role=='skill' else main_ids).append(seen[path])
                continue
            resolved,raw,text=read_document(path,options.get('encoding','utf-8-sig'),store.path(f'inputs/ocr/doc-{len(sources)+1:04d}'),cancellation)
            sid=f'doc-{len(sources)+1:04d}'
            seen[path]=sid
            rel=f'inputs/{sid}.txt'
            atomic_bytes(store.path(rel),text.encode('utf-8'))
            # Original bytes are needed for byte-exact copy/move, never regenerate them via LLM.
            original=f'inputs/{sid}.original'
            atomic_bytes(store.path(original),raw)
            sources.append({'id':sid,'original':str(resolved),'original_sha256':hashlib.sha256(raw).hexdigest(),
                            'snapshot':rel,'snapshot_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
                            'raw_snapshot':original,'name':resolved.name,'chars':len(text),'role':role})
            (skill_ids if role=='skill' else main_ids).append(sid)
        b=Builder(store,sources,config)
        if workflow=='web':
            urls=[u.strip() for u in options.get('urls',instruction).splitlines() if u.strip()]
            if not urls or any(not re.match(r'^https?://\S+$',u) for u in urls):
                raise NeedsInput('每行填写一个有效的http或https网址。')
            for i,url in enumerate(dict.fromkeys(urls),1):
                node=b.add('web_fetch',url=url,catalog=mode=='catalog')
                b.add('web_export',[node],publish=f'网页/{i:03d}.md')
        elif workflow=='documents':
            if mode not in DOC_MODES:
                raise NeedsInput('请选择支持的文档操作。')
            if mode in ('summary','rewrite','proofread','translate','extract'):
                for source in sources:
                    text=store.path(source['snapshot']).read_bytes().decode('utf-8')
                    parts=[]
                    pos=0
                    for start,end in chunks(text,min(config.chunk_chars,1600)):
                        tail=pos+len(text[start:end].encode('utf-8'))
                        parts.append(b.generate('doc_'+mode,document=source['id'],start=start,end=end,
                                                byte_start=pos,byte_end=tail,max_chars=2200))
                        pos=tail
                    b.add('join',parts,title=f'{source["name"]} · {DOC_MODES[mode]}',publish=f'{source["id"]}-{Path(source["name"]).stem}.md')
            elif mode=='compare':
                if len(main_ids)<2:
                    raise NeedsInput('文档比较至少需要两份文件。')
                overview,chapters=b.analyze(main_ids)
                b.generate('compare',[overview],max_chars=1000,publish='文档比较.md')
            else:
                b.add('document_operation',operation=mode,publish='处理报告.md')
        elif workflow=='style':
            root,chapters=b.analyze(main_ids,'style_observation')
            core=b.generate('style_rules',[root],max_chars=900,publish='author-style/references/style-guide.md')
            b.generate('style_scenes',[root,core],max_chars=900,publish='author-style/references/scene-patterns.md')
            examples=b.generate('style_examples',[core],max_chars=800,publish='author-style/references/examples.md')
            evaluation=b.generate('style_evaluation',[core,examples],max_chars=800,publish='author-style/references/evaluation.md')
            b.generate('style_entry',[core,evaluation],max_chars=800,publish='author-style/SKILL.md')
            b.add('style_evidence',chapters,publish='author-style/analysis/evidence.md')
        elif workflow=='outline':
            root,_=b.analyze(main_ids)
            deps=[root] if root else []
            premise=b.generate('outline_'+mode,deps,max_chars=900,publish='大纲/故事总纲.md')
            characters=b.generate('characters',[premise],max_chars=800,publish='大纲/人物与设定.md')
            prior=None
            for chapter in range(first,first+count):
                prior=b.generate('chapter_outline',[premise,characters]+([prior] if prior else []),chapter=chapter,
                                 max_chars=800,publish=f'大纲/第{chapter:03d}章细纲.md')
            b.generate('outline_check',[premise,characters,prior],max_chars=800,publish='大纲/一致性检查与修改说明.md')
        elif workflow=='fiction':
            root,_=b.analyze(main_ids)
            skill,_=b.analyze(skill_ids,'skill_rules')
            base=[x for x in (root,skill) if x]
            previous=None
            for chapter in range(first,first+count):
                deps=base+([previous] if previous else [])
                sceneplan=b.generate('fiction_plan',deps,chapter=chapter,max_chars=700)
                scenes=[]
                events=[]
                for scene in range(1,int(options.get('scenes',3))+1):
                    lengths=scene_lengths(options['target_chars'],options['scenes'],scene) if options.get('target_chars') else {'max_chars':1000}
                    if options.get('target_chars'): lengths['length_dependencies']=list(scenes)
                    scene_deps=[sceneplan]+([skill] if skill else [])+([previous] if previous else [])+scenes[-1:]
                    draft=b.generate('fiction_scene',scene_deps,chapter=chapter,scene=scene,**lengths)
                    check=b.generate('fiction_check',[sceneplan,draft]+([skill] if skill else []),chapter=chapter,scene=scene,max_chars=500)
                    scene_final=b.generate('fiction_polish',[draft,check],chapter=chapter,scene=scene,**lengths)
                    scenes.append(scene_final)
                    events.append(b.generate('fiction_events',[scene_final],chapter=chapter,scene=scene,max_chars=500))
                candidate=b.reduce(events)
                draft=b.add('join',scenes,title=f'第{chapter}章',chapter=chapter)
                approval=b.add('approval',[draft],chapter=chapter,draft_step=draft)
                b.add('approved_copy',[draft,approval],chapter=chapter,publish=f'小说/第{chapter:03d}章.md')
                previous=b.generate('story_state',[candidate,approval]+([previous] if previous else []),chapter=chapter,max_chars=700,
                                    publish=f'小说/剧情记录/第{chapter:03d}章.md')
        plan={'workflow':workflow,'workflow_version':2,'steps':b.steps}
        store.write_json('manifests/sources.json',sources)
        store.write_json('plan.json',plan)
        store.write_json('task.json',{'schema_version':1,'task_id':store.root.name,'created_at':now(),
            'workflow':workflow,'mode':mode,'instruction':instruction,'options':options,'config':config.to_dict(),
            'main_source_ids':main_ids,'skill_source_ids':skill_ids,'plan_hash':fingerprint(plan),'sources_hash':fingerprint(sources)})
        store.write_json('state.json',{'status':'ready','steps':{s['id']:'pending' for s in b.steps},'run_id':None})
        store.event('task_created',workflow=workflow,steps=len(b.steps),sources=len(sources))
        return store
    except Exception as exc:
        store.write_json('state.json',{'status':'failed','error':str(exc),'steps':{},'run_id':None})
        raise
