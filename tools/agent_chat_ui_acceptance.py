import os,sys,time,json
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0,str(Path(__file__).resolve().parents[1])); sys.path.append(r'D:\Desktop Assistant')
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont,QFontDatabase
from pages.page_ollama_chat import PageOllamaChat
from styles.style_all import apply_theme_palette
from Agent.agent_core.store import atomic_json
root=Path(sys.argv[1]).resolve(); root.mkdir(parents=True,exist_ok=True)
materials=root/'materials'; materials.mkdir(exist_ok=True)
(materials/'第1章.txt').write_text('林舟在雨夜收到旧信。',encoding='utf-8')
(materials/'第2章.txt').write_text('清晨，他前往废弃车站寻找父亲。',encoding='utf-8')
app=QApplication([]); QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\msyh.ttc'); app.setFont(QFont('Microsoft YaHei',10)); apply_theme_palette(app)
host=PageOllamaChat(); host._model_name=lambda:'qwen3.6-jailbreak:q6'; host._using_api=lambda:False; host._base=lambda:'http://127.0.0.1:11434'
panel=host.agent_panel; panel.task_root=root/'tasks'; panel.chat_root=root/'chats'; panel.store=None; panel.new_chat(); panel.directory.setText(str(materials))
host.resize(1100,820); host.tabs.setCurrentWidget(panel); host.show(); app.processEvents()
def send(text):
    previous=str(panel.store.root) if panel.store else None
    panel.composer.setPlainText(text); panel.send_button.click(); deadline=time.monotonic()+360
    while time.monotonic()<deadline:
        app.processEvents(); time.sleep(.03)
        if panel.store and str(panel.store.root)!=previous and not panel.busy and not panel.after:
            state=panel.store.read_json('state.json')
            if state['status']=='completed': return panel.store
            if state['status'] in ('failed','needs_input'): raise AssertionError(state)
    raise AssertionError(panel.messages[-4:])
try:
    first=send('把工作目录里的两个文件合并成一个TXT文件')
    merged=next(first.path('outputs').rglob('合并结果.txt')); body=merged.read_text(encoding='utf-8')
    assert '旧信' in body and '车站' in body
    second=send('把刚才合并的文件转换成DOCX')
    sources=second.read_json('manifests/sources.json')
    assert len(sources)==1 and Path(sources[0]['original'])==merged,sources
    assert list(second.path('outputs').rglob('*.docx'))
    atomic_json(root/'report.json',{'status':'passed','merge_task':str(first.root),'followup_task':str(second.root),
        'followup_uses_previous_output':True,'messages':panel.messages})
    app.processEvents(); host.grab().save(str(root/'chat-result.png'))
    print('Real-model chat creation and follow-up passed',flush=True)
finally: host.shutdown(); host.close()
