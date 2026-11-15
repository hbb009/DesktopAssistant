"""Deterministic prose-length allocation, independent of story subject matter."""
import math
import re

def prose_chars(text):
    return len(re.sub(r'\s','',str(text)))

def requested_chars(text):
    match=re.search(r'(?<!\d)(\d+(?:\.\d+)?)\s*([千百kK万]?)\s*[字詞词]',text)
    if match:
        scale={'':1,'百':100,'千':1000,'k':1000,'K':1000,'万':10000}[match[2]]
        return int(float(match[1])*scale)
    match=re.search(r'([一二两三四五六七八九])([千百])字',text)
    if match:
        return {'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9}[match[1]]*(1000 if match[2]=='千' else 100)
    return None

def apply_length(options,text):
    value=requested_chars(text) or options.get('target_chars')
    if value is None: return
    if type(value) is not int or not 300<=value<=20000:
        raise ValueError('正文目标字数须在300～20000之间；更长内容请按章节拆分。')
    count=options.get('chapters',1)
    if count>1 and not re.search(r'每[章篇]|每一章|各[章篇]',text):
        value=math.ceil(value/count)
        if value<300: raise ValueError('每章篇幅不足300字，请减少章数或增加总字数。')
    options['target_chars']=value
    options['scenes']=max(1,math.ceil(value/600))

def scene_lengths(target,scenes,index):
    # Exact allocation sum; validation uses non-whitespace characters incl. punctuation.
    size=target//scenes+(1 if index<=target%scenes else 0)
    return {'target_chars':size,'min_chars':math.ceil(size*.9),'max_chars':math.ceil(size*1.1)}
