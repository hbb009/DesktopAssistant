"""Conversation-first UI over the persistent workflow engine."""
import html,json,re,uuid
from pathlib import Path
from PyQt5.QtCore import Qt,QTimer
from PyQt5.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QPlainTextEdit,
    QTextBrowser,QDialog,QFileDialog,QComboBox)
from Agent.agent_core.conversation import inventory,interpret,interpret_feedback
from Agent.agent_core.models import ModelConfig
from Agent.agent_core.store import atomic_json,TaskStore,fingerprint
from Agent.agent_core.workflows.planner import create_task,WORKFLOWS,DOC_MODES
from Agent.agent_core.file_approval import move_plan,confirm_move
from styles.style_all import apply_btn_download
from .agent_task_panel import AgentTaskPanel
from .agent_chat_bubbles import BubbleTranscript
from utils.logger import get_logger

log=get_logger('agent.chat')

_PAUSE_COMMANDS = ('暂停', '停止', '停一下', '先停一下', '暂停任务', '停止任务', '先暂停', '暂停一下')

class Composer(QPlainTextEdit):
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Return,Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.parent().send_message(); event.accept()
        else: super().keyPressEvent(event)

class AgentChatPanel(AgentTaskPanel):
    def __init__(self,host,task_root=None,client_factory=None):
        self.messages=[]; self.display_from=0; self.pending=''; self.session_id=uuid.uuid4().hex
        self.announced=set(); self.pending_move=None; self.routing=False
        self._associating=False
        kwargs={'task_root':task_root}
        if client_factory is not None: kwargs['client_factory']=client_factory
        super().__init__(host,**kwargs)
        # Reuse the tested lifecycle/controller, but remove its form interface.
        layout=self.layout()
        while layout.count():
            item=layout.takeAt(0)
            if item.widget(): item.widget().hide()
        self.chat_root=self.task_root.parent/'agent_chat'
        header=QHBoxLayout()
        self.directory_button=QPushButton('选择目录')
        self.workdir_button=self.directory_button
        self.directory_button.clicked.connect(self.choose_directory)
        header.addWidget(self.directory_button)
        self.location=QLabel(); self.location.setTextFormat(Qt.PlainText)
        header.addWidget(self.location,1)
        self.button(header,'历史任务',self.show_history)
        self.new_chat_button=self.button(header,'新对话',self.new_chat)
        self.new_chat_button.setToolTip('开始新任务，清除当前上下文和附件；保留历史文件')
        self.clear_screen_button=self.button(header,'清屏',self.clear_screen)
        self.clear_screen_button.setToolTip('仅清空显示，保留当前任务和上下文')
        self.details_button=self.button(header,'详情',self.show_details)
        layout.addLayout(header)
        self.chat=BubbleTranscript(self)
        layout.addWidget(self.chat,1)
        self.chat_status=QLabel(''); self.chat_status.setWordWrap(True); layout.addWidget(self.chat_status)
        row=QHBoxLayout()
        self.attach_button=self.button(row,'添加文件',self.add_files)
        self.clear_button=self.button(row,'清除附件',self.clear_attachments)
        self.attachment_label=QLabel(); row.addWidget(self.attachment_label,1)
        self.stop_button=self.button(row,'暂停',self.pause)
        self.send_button=self.button(row,'发送',self.send_message)
        apply_btn_download(self.send_button)
        self.send_button.setCursor(Qt.PointingHandCursor)
        layout.addLayout(row)
        self.composer=Composer(self); self.composer.setMaximumHeight(110); self.composer.setMinimumHeight(75)
        self.composer.setPlaceholderText('直接告诉我想做什么，例如：把工作目录里的小说提炼成文风 Skill。\nEnter 发送 · Shift+Enter 换行')
        layout.addWidget(self.composer)
        self.directory.textChanged.connect(self.update_location)
        self.reload_from_disk()

    def reload_from_disk(self):
        """Refresh the active chat and task selection after a backup restore."""
        self.messages = []
        self.display_from = 0
        self.pending = ''
        self.announced = set()
        self.session_id = uuid.uuid4().hex
        self.store = None
        self.last_approval = None
        self.pending_move = None
        self.files.clear()
        self.composer.clear()
        self.preview.clear()
        self.feedback.clear()
        self.directory.setText(self.host._article_dir())
        self.refresh_history()

        saved_session = None
        try:
            settings = json.loads((self.chat_root / 'settings.json').read_text(encoding='utf-8'))
            directory = settings.get('directory')
            if directory and Path(directory).is_dir():
                self.directory.setText(directory)
            saved_session = settings.get('session')
        except (OSError, ValueError, AttributeError, TypeError):
            pass

        restored = False
        if isinstance(saved_session, str) and re.fullmatch(r'[a-f0-9]{32}', saved_session):
            try:
                data = json.loads((self.chat_root / (saved_session + '.json')).read_text(encoding='utf-8'))
                messages = data['messages']
                if not isinstance(messages, list):
                    raise ValueError('invalid chat transcript')
                self.session_id = saved_session
                self.messages = messages
                self.pending = data.get('pending', '')
                self.display_from = max(0, min(int(data.get('display_from', 0)), len(messages)))
                self.announced = set(data.get('announced', []))
                if data.get('task'):
                    task = self.task_root / Path(data['task']).name
                    if (task / 'task.json').is_file() and task.resolve().parent == self.task_root.resolve():
                        self.store = TaskStore(task)
                self.render_chat()
                restored = True
            except (OSError, ValueError, KeyError, TypeError):
                log.warning('无法恢复上次 Agent 对话')
        if not restored and self.history.count():
            self.select_task()
        if not self.messages:
            self.welcome()
        self.update_location()
        self.update_attachments()
        self.refresh_state()
        self.persist_chat()

    def welcome(self):
        self.add_message('assistant','直接说你想做的事，我会使用本地 Ollama 分步完成，并保存进度。\n\n例如：\n• 把这个网址的正文保存到工作目录。\n• 整理目录里的80章小说，制作作者文风 Skill。\n• 加载这个 Skill，按照大纲写第一章。\n\n运行中可以说“暂停”，之后说“继续”。小说每章写好后，我会先请你确认。')

    def update_location(self,*_):
        if not hasattr(self,'location'): return
        root=self.directory.text().strip()
        shown=root if len(root)<=55 else root[:12]+'…'+root[-40:]
        self.location.setText('工作目录：'+(shown or '未选择'))
        self.location.setToolTip(root)
        chosen=bool(root)
        self.directory_button.setText('更换目录' if chosen else '选择目录')
        self.directory_button.setToolTip('更换任务使用的工作目录' if chosen else '选择任务使用的工作目录')
        if root and Path(root).is_dir(): atomic_json(self.chat_root/'settings.json',{'directory':root,'session':self.session_id})

    def update_attachments(self):
        if not hasattr(self,'attachment_label'): return
        count=self.files.count()
        self.attachment_label.setText(f'已添加 {count} 个文件' if count else '未添加附件时，使用工作目录中的材料')
        self.attachment_label.setToolTip('\n'.join(self.files.item(i).text() for i in range(count)))
        self.clear_button.setVisible(bool(count))

    def append_files(self,paths):
        super().append_files(paths); self.update_attachments()
    def clear_attachments(self):
        self.files.clear(); self.update_attachments()
    def choose_directory(self):
        if self.busy: return self.add_message('assistant','任务正在运行，暂停后再切换目录。')
        old=self.directory.text(); super().choose_directory()
        if old!=self.directory.text():
            self.clear_attachments()
            if self.pending:
                self.composer.setPlainText('继续'); QTimer.singleShot(0,self.send_message)

    def persist_chat(self):
        if not hasattr(self,'chat_root'): return
        atomic_json(self.chat_root/(self.session_id+'.json'),{'session_id':self.session_id,'messages':self.messages,
            'display_from':self.display_from,'pending':self.pending,'announced':list(self.announced),'task':str(self.store.root) if self.store else None})
        atomic_json(self.chat_root/'settings.json',{'directory':self.directory.text(),'session':self.session_id})

    def add_message(self,role,text):
        self.messages.append({'role':role,'text':str(text)})
        if hasattr(self,'chat'): self.render_chat(); self.persist_chat()

    def render_chat(self):
        self.chat.set_messages(self.messages[self.display_from:])

    def clear_screen(self):
        self.display_from=len(self.messages)
        self.render_chat(); self.persist_chat()

    def recent_task(self):
        """A failed follow-up must not hide the last successful result in this chat."""
        if not self.store: return None
        candidates=[self.store]
        cutoff=self.store.task().get('created_at','')
        older=[]
        if self.task_root.exists():
            for root in sorted(self.task_root.iterdir(),reverse=True):
                if root==self.store.root or not (root/'task.json').is_file(): continue
                candidate=TaskStore(root)
                try:
                    meta=candidate.task()
                    if meta.get('chat_session')==self.session_id and meta.get('created_at','')<=cutoff:
                        older.append((meta.get('created_at',''),candidate))
                except (OSError,ValueError): continue
        candidates.extend(candidate for _,candidate in sorted(older,key=lambda item:item[0],reverse=True))
        for candidate in candidates:
            try:
                if candidate.read_json('state.json').get('outputs'): return candidate
            except (OSError,ValueError): continue
        return self.store

    def open_chat_link(self,url):
        if url.toString()=='agent:outputs': self.open_folder(True)
        elif url.toString()=='agent:details': self.show_details()

    def alert(self,message):
        if hasattr(self,'chat'):
            self.routing=False; self.add_message('assistant',str(message))
        else: super().alert(message)

    def new_chat(self):
        if self.busy: return self.add_message('assistant','先暂停当前任务，再开始新对话。')
        self.persist_chat(); self.store=None; self.messages=[]; self.display_from=0; self.pending=''
        self.session_id=uuid.uuid4().hex; self.announced=set(); self.pending_move=None
        self.last_approval=None; self.routing=False; self.after=None
        self.composer.clear(); self.feedback.clear(); self.preview.clear(); self.status.clear()
        self.skill.clear(); self.instruction.clear(); self.urls.clear()
        self.clear_attachments(); self.welcome(); self.refresh_state()

    def show_history(self):
        if self.busy: return self.add_message('assistant','先暂停当前任务，再切换历史任务。')
        dialog=QDialog(self); dialog.setWindowTitle('继续历史任务'); dialog.resize(620,120)
        layout=QVBoxLayout(dialog); choices=QComboBox(); layout.addWidget(choices)
        for i in range(self.history.count()): choices.addItem(self.history.itemText(i),self.history.itemData(i))
        button=QPushButton('打开对话'); layout.addWidget(button)
        def select():
            index=self.history.findData(choices.currentData())
            if index>=0:
                self.persist_chat(); self.history.setCurrentIndex(index); self.select_task()
            dialog.accept()
        button.clicked.connect(select); dialog.exec_()

    def show_details(self):
        dialog=QDialog(self); dialog.setWindowTitle('任务详情'); dialog.resize(700,430)
        layout=QVBoxLayout(dialog); view=QPlainTextEdit(); view.setReadOnly(True)
        model=self.host._model_name()
        text=f'本地 Ollama：{model}\n工作目录：{self.directory.text()}\n\n'+self.status.text()
        if self.store: text+='\n\n任务目录：'+str(self.store.root)+'\n\n'+self.preview.toPlainText()
        view.setPlainText(text); layout.addWidget(view)
        row=QHBoxLayout(); self.button(row,'打开成果',lambda:self.open_folder(True))
        self.button(row,'打开缓存和日志',lambda:self.open_folder(False)); layout.addLayout(row)
        dialog.exec_()

    def select_task(self):
        self._switching=True
        try: super().select_task()
        finally: self._switching=False
        if not hasattr(self,'chat') or self._associating or not self.store: return
        task=self.store.task(); session=task.get('chat_session')
        self.pending_move=None
        if session:
            try:
                data=json.loads((self.chat_root/(session+'.json')).read_text(encoding='utf-8'))
                self.session_id=session; self.messages=data['messages']; self.pending=data.get('pending','')
                self.display_from=max(0,min(int(data.get('display_from',0)),len(self.messages)))
                self.announced=set(data.get('announced',[])); self.render_chat(); self.refresh_state(); return
            except (OSError,ValueError,KeyError): pass
        self.session_id=uuid.uuid4().hex; self.messages=[]; self.display_from=0; self.pending=''; self.announced=set()
        self.add_message('assistant','已打开历史任务：'+WORKFLOWS.get(task.get('workflow'),'文档处理')+'。你可以说“继续”，或查看成果。')
        task['chat_session']=self.session_id; self.store.write_json('task.json',task); self.refresh_state()

    def send_message(self):
        text=self.composer.toPlainText().strip()
        if not text or self._closing:
            return
        command=re.sub(r'[\s，,。.!！?？]+','',text).lower()
        if self.busy and command not in _PAUSE_COMMANDS:
            return
        self.composer.clear(); self.add_message('user',text)
        if command=='清屏': self.clear_screen(); return
        if command in ('你好','您好','能做什么','你能做什么','你可以做什么','功能介绍','怎么用'):
            self.add_message('assistant','我可以保存网站正文、处理工作目录里的文档、提炼作者文风 Skill、编写或修改小说大纲，以及加载 Skill 写小说。直接描述要求即可；任务会逐步保存，小说每章先交给你确认。'); return
        if command in _PAUSE_COMMANDS:
            if self.busy: self.pause(); self.add_message('assistant','正在暂停，已经完成的步骤会保留。')
            else: self.add_message('assistant','当前没有正在运行的任务。')
            return
        if command in ('进度','进度怎么样','做到哪了','当前进度','任务状态'):
            self.add_message('assistant',self.status.text() if self.store else '还没有开始任务。'); return
        if command in ('新对话','新任务','开始新任务'):
            self.new_chat(); return
        if self.pending_move:
            if command in ('确认移动','确认执行移动'):
                try: confirm_move(self.store,self.pending_move); self.pending_move=None; self.resume()
                except Exception as exc: self.alert(exc)
            elif command in ('取消','取消移动','不要移动'):
                self.pending_move=None; self.add_message('assistant','已取消移动，源文件保留。')
            else: self.add_message('assistant','如需执行上面的移动清单，请明确回复“确认移动”；不执行就回复“取消移动”。')
            return
        self.refresh_state()
        if self.last_approval:
            if command in ('确认','确认本章','确认继续','确认本章继续','确认写下一章','这章可以','这章可以了','这章可以了接着写','通过','通过继续','满意继续') and not re.search(r'[?？]',text):
                self.add_message('assistant','已确认本章，继续保存正式章节和后续任务。'); self.approve()
            elif command in ('继续','继续任务','恢复','下一章'):
                self.add_message('assistant','本章还没确认。满意请说“确认本章”；需要修改，直接告诉我修改意见。')
            else:
                self.resolve_feedback(text)
            return
        if command in ('继续','继续任务','恢复','恢复任务','接着做','继续之前的任务'):
            if self.pending:
                text=self.pending; self.pending=''
            elif self.store:
                state=self.store.read_json('state.json')
                if state['status']=='completed': self.add_message('assistant','上一项任务已经完成。请描述接下来要做的事。')
                else: self.add_message('assistant','继续上次任务，已完成的步骤会复用。'); self.resume()
                return
            else:
                self.add_message('assistant','没有待恢复的任务，可以从“历史任务”选择，或者告诉我新任务。'); return
        # A directory can be supplied naturally; require a literal existing path.
        match=re.search(r'(?:工作目录(?:设为|是|改为)?|目录(?:设为|是|改为))\s*[:：]?\s*["“`]?([A-Za-z]:[\\/][^\r\n"”`]+)',text)
        if match:
            candidate=match.group(1).strip().rstrip('。')
            if Path(candidate).is_dir():
                self.directory.setText(candidate); self.clear_attachments()
                self.add_message('assistant','工作目录已设置。'+('我接着处理刚才的要求。' if self.pending else '告诉我要处理什么。'))
                if not self.pending: return
                text=self.pending; self.pending=''
        root=self.directory.text().strip()
        if not root or not Path(root).is_dir():
            self.pending=(self.pending+'\n'+text).strip(); self.persist_chat()
            self.add_message('assistant','先告诉我工作目录，例如“工作目录：D:\\小说”，或点击上方“选择目录”。材料会在本地读取，成果也保存到这个目录。'); return
        try:
            if self.host._using_api(): raise ValueError('请先在配置页选择本地 Ollama；这里会沿用该模型和服务地址。')
            if self.host._chat_worker is not None and self.host._chat_worker.isRunning(): raise ValueError('普通对话正在使用模型，请先停止那边的生成。')
            combined=(self.pending+'\n补充：'+text) if self.pending else text
            config=ModelConfig(self.host._model_name(),base_url=self.host._base(),num_ctx=8192,num_predict=1024,read_timeout=300)
            attachments=[self.files.item(i).text() for i in range(self.files.count())]
            recent_outputs=[]; recent_context=''
            recent=self.recent_task()
            if recent:
                previous=recent.task(); previous_state=recent.read_json('state.json')
                recent_context=json.dumps({'功能':previous.get('workflow'),'操作':previous.get('mode'),
                    '要求':previous.get('instruction',''),'状态':previous_state.get('status')},ensure_ascii=False)
                for relative in previous_state.get('outputs',[]):
                    if relative.startswith('outputs/'):
                        recent_outputs.append(recent.path(relative))
            attempt=self.chat_root/self.session_id/'routing'/uuid.uuid4().hex
            self.routing=True; self.pending=combined; self.persist_chat()
            self.chat_status.setText('正在理解你的要求…')
            self.launch(lambda token,event:interpret(combined,inventory(root,attachments,recent_outputs),config,self.client_factory(config),token,attempt,recent_context),self.routed)
        except Exception as exc: self.alert(exc)

    def resolve_feedback(self,text):
        values=dict(self.store.task()['config']); values['num_predict']=512
        config=ModelConfig(**values)
        attempt=self.chat_root/self.session_id/'feedback'/uuid.uuid4().hex
        expected=dict(self.last_approval); store=self.store
        self.routing=True
        def resolved(intent):
            self.routing=False
            if self.store is not store or self.last_approval!=expected:
                self.add_message('assistant','待审核稿件已变化，请重新确认当前章节。'); return
            action=intent['action']
            if action=='approve':
                self.add_message('assistant','收到，你已确认本章。我继续保存并处理下一步。'); self.after=self.approve
            elif action=='revise':
                self.feedback.setPlainText(text); self.add_message('assistant','我会按你的意见修改本章，再请你确认。'); self.after=self.revise
            elif action=='pause': self.add_message('assistant','好的，先停在本章；稿件和进度都已保留。')
            else: self.add_message('assistant',intent.get('message') or '满意请说“确认本章”；需要修改，直接告诉我修改要求。')
        self.launch(lambda token,event:interpret_feedback(text,config,self.client_factory(config),token,attempt),resolved)

    def routed(self,intent):
        self.routing=False
        if intent['action']!='task':
            if intent['action']=='reply': self.pending=''
            self.add_message('assistant',intent['message']); return
        self.pending=''; self.persist_chat()
        label=WORKFLOWS[intent['workflow']]
        count=len(intent['paths'])
        description=f'我来处理{label}。'+(f'本次读取 {count} 个文件。' if count else '')
        if intent['options'].get('skill'): description+='已加载：'+str(intent['options']['skill'])+'。'
        if intent['workflow'] in ('fiction','outline'):
            description+=f"从第 {intent['options']['start_chapter']} 章开始，共 {intent['options']['chapters']} 章。"
        if intent['workflow']=='web':
            description+=f"按 {intent['options'].get('format','')} 保存，文件名用网页标题。"
        if intent['workflow']=='fiction': description+='每章写好后先交给你确认。'
        if intent['options'].get('target_chars'): description+=f"每章正文目标约{intent['options']['target_chars']}字，会分段写完并合并。"
        self.add_message('assistant',description+'过程会自动保存，你可以随时说“暂停”。')
        def begin():
            config=ModelConfig(self.host._model_name(),base_url=self.host._base(),num_ctx=8192,num_predict=2048,read_timeout=300)
            self.launch(lambda token,event:create_task(self.task_root,intent['workflow'],intent['mode'],intent['paths'],
                intent['instruction'],config,intent['options'],token),self.created)
        self.after=begin

    def created(self,store):
        task=store.task(); task['chat_session']=self.session_id; store.write_json('task.json',task)
        self._associating=True
        try: super().created(store)
        finally: self._associating=False
        self.persist_chat()

    def resume(self):
        if self.busy or not self.store: return
        task=self.store.task()
        if task.get('mode')=='move' and not self.store.path('approvals/move.json').exists():
            plan=move_plan(self.store); self.pending_move=fingerprint(plan)
            self.add_message('assistant','准备移动以下文件；核验副本后才移除原件：\n\n'+
                '\n\n'.join(p['source']+'\n→ '+p['destination'] for p in plan)+'\n\n请回复“确认移动”或“取消移动”。')
            return
        super().resume()

    def launch(self, action, callback=None):
        super().launch(action, callback)
        self._sync_run_buttons()

    def _sync_run_buttons(self):
        self.send_button.setEnabled(not self.busy and not self._closing)
        self.stop_button.setEnabled(self.busy and not self._closing)

    def refresh_state(self):
        super().refresh_state()
        if not hasattr(self,'chat') or getattr(self,'_switching',False): return
        self.new_chat_button.setEnabled(not self.busy)
        self._sync_run_buttons()
        self.directory_button.setEnabled(not self.busy)
        self.attach_button.setEnabled(not self.busy); self.clear_button.setEnabled(not self.busy)
        if self.routing: self.chat_status.setText('正在理解你的要求…'); return
        self.chat_status.setText(self.status.text() if self.store else '本地 Ollama · 输入自然语言即可开始')
        if not self.store: return
        try:
            state=self.store.read_json('state.json'); status=state['status']
            approval=state.get('approval') or {}
            key=str(self.store.root)+'|'+status+'|'+str(approval.get('draft_hash',''))+'|'+str(state.get('run_id',''))
            if status=='awaiting_approval': key=str(self.store.root)+'|draft|'+approval.get('draft_hash','')+'|'+approval.get('input_hash','')
            if status not in ('awaiting_approval','completed','paused','failed','needs_input') or key in self.announced: return
            self.announced.add(key)
            if status=='awaiting_approval':
                body=self.store.path(approval['draft_path']).read_text(encoding='utf-8')
                report=approval.get('length_report') or {}
                note=''
                if report:
                    note=f'\n\n字数检查：正文 {report["chars"]} 字，目标约 {report["target"]} 字（不计空白和章标题）。'
                    if not report.get('within_tolerance'):
                        note+='篇幅偏差超过 10%，已保留完整草稿，尚未达到字数要求；可要求调整后再确认。'
                self.add_message('assistant',body+note+'\n\n—— 本章写好了。满意请说“确认本章”；需要修改，直接告诉我修改意见。')
            elif status=='completed':
                task=self.store.task(); root=task.get('options',{}).get('export_root')
                path=str(Path(root)/'Agent成果'/task['task_id']) if root else str(self.store.path('outputs'))
                self.add_message('assistant','已完成。成果保存在：\n'+path+'\n\n'+'\n'.join(str(Path(p).relative_to('outputs')) for p in state.get('outputs',[])))
            elif status=='paused': self.add_message('assistant','任务已暂停，已完成的步骤已保存。说“继续”即可恢复。')
            else: self.add_message('assistant','任务停在这里：'+str(state.get('error') or '未知错误')+'\n已完成的步骤仍保留；详情中可以打开日志。')
        except (OSError,ValueError,KeyError): pass
        except Exception:
            if not getattr(self,'_chat_fault_logged',False):
                self._chat_fault_logged=True
                log.exception('刷新对话状态失败')

    def shutdown(self):
        self.persist_chat(); super().shutdown()
