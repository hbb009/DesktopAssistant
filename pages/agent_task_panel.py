"""Persistent workflow UI. Workers never access Qt widgets."""
from pathlib import Path
import re
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,
    QTextEdit,QPlainTextEdit,QComboBox,QSpinBox,QPushButton,QListWidget,QAbstractItemView,
    QFileDialog,QMessageBox,QTabWidget,QScrollArea,QProgressBar)
from agent_core.models import ModelConfig, Cancellation
from agent_core.store import TaskStore, fingerprint
from agent_core.workflows.planner import create_task, WORKFLOWS, DOC_MODES
from agent_core.engine import Engine
from agent_core.llm_client import OllamaClient
from agent_core.approvals import confirm_chapter, revise_chapter
from agent_core.file_approval import move_plan, confirm_move

MODES={'web':{'page':'网页/分页正文','catalog':'小说目录与章节'},'documents':DOC_MODES,
       'style':{'build':'完整材料 → 风格 Skill'},'outline':{'new':'新建','modify':'修改','continue':'续篇'},
       'fiction':{'new':'新写','continue':'续写','revise':'修改','rewrite':'重写'}}
STATUS={'ready':'就绪','planning':'整理输入','running':'执行中','validating':'校验成果','paused':'已暂停',
        'awaiting_approval':'等待确认本章','completed':'已完成','failed':'失败，可查看日志','needs_input':'需要处理提示后恢复'}

class TaskWorker(QThread):
    result=pyqtSignal(object)
    error=pyqtSignal(str)
    event=pyqtSignal(object)
    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.token=Cancellation()
        self.action=action
    def run(self):
        try:
            self.result.emit(self.action(self.token,self.event.emit))
        except Exception as exc:
            self.error.emit(str(exc))

class AgentTaskPanel(QWidget):
    def __init__(self, host, task_root=None, client_factory=OllamaClient):
        super().__init__(host)
        from utils.app_paths import app_root
        self.host=host
        self.task_root=Path(task_root or Path(app_root())/'userdata'/'agent_tasks')
        self.client_factory=client_factory
        self.worker=None
        self._closing=False
        self.store=None
        self.last_approval=None
        self.after=None
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('新版 Agent · 每步保存 · 可恢复 · 章稿确认后继续'))
        self.tabs=QTabWidget(); layout.addWidget(self.tabs)
        form_widget=QWidget(); form=QFormLayout(form_widget); self.form=form
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(form_widget)
        self.tabs.addTab(scroll,'新建任务')
        self.workflow=QComboBox()
        for key,label in WORKFLOWS.items(): self.workflow.addItem(label,key)
        self.mode=QComboBox()
        form.addRow('功能',self.workflow); form.addRow('操作',self.mode)
        self.directory=QLineEdit(host._article_dir())
        row=QHBoxLayout(); row.addWidget(self.directory)
        self.button(row,'选择目录',self.choose_directory); form.addRow('工作目录 / 成果位置',row)
        self.files=QListWidget(); self.files.setMinimumHeight(110)
        self.files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files.setDragDropMode(QAbstractItemView.InternalMove)
        form.addRow('输入文件（拖动调整顺序）',self.files)
        row=QHBoxLayout()
        self.button(row,'添加文件',self.add_files); self.button(row,'载入工作目录',self.load_directory)
        self.button(row,'移除选中',self.remove_files); self.button(row,'清空',self.files.clear)
        form.addRow(row)
        self.urls=QPlainTextEdit(); self.urls.setMaximumHeight(85); self.urls.setPlaceholderText('每行一个 http/https 网址')
        form.addRow('网站地址',self.urls)
        self.skill=QLineEdit(); row=QHBoxLayout(); row.addWidget(self.skill)
        self.button(row,'选择 Skill 目录',self.choose_skill)
        form.addRow('写作 Skill（目录或 .md）',row)
        self.instruction=QPlainTextEdit(); self.instruction.setMinimumHeight(90)
        self.instruction.setPlaceholderText('填写创作要求、目标语言、提取字段等；已有大纲和正文请添加到输入文件。')
        form.addRow('任务要求',self.instruction)
        self.chapters=QSpinBox(); self.chapters.setRange(1,80); self.chapters.setValue(3)
        self.first_chapter=QSpinBox(); self.first_chapter.setRange(1,9999)
        form.addRow('起始章号（续写填下一章）',self.first_chapter)
        self.scenes=QSpinBox(); self.scenes.setRange(1,6); self.scenes.setValue(3)
        form.addRow('章节数量',self.chapters); form.addRow('每章场景数（约400～650字/场景）',self.scenes)
        self.find=QLineEdit(); self.replace=QLineEdit(); self.prefix=QLineEdit('文档')
        form.addRow('搜索 / 替换原文',self.find); form.addRow('替换为（可为空）',self.replace); form.addRow('改名副本前缀',self.prefix)
        self.format=QComboBox(); self.format.addItems(['md','txt','docx','pdf']); form.addRow('转换格式',self.format)
        self.encoding=QComboBox(); self.encoding.addItems(['utf-8-sig','gb18030']); form.addRow('文本编码',self.encoding)
        self.context=QComboBox(); self.context.addItems(['8192','4096','16384','32768']); form.addRow('固定上下文（不会自动升档）',self.context)
        self.create_button=QPushButton('创建并执行'); self.create_button.clicked.connect(self.create)
        form.addRow(self.create_button)
        result=QWidget(); r=QVBoxLayout(result); self.tabs.addTab(result,'执行与结果')
        row=QHBoxLayout(); self.history=QComboBox(); row.addWidget(self.history,1)
        self.button(row,'刷新任务',self.refresh_history); r.addLayout(row)
        self.status=QLabel('尚未选择任务'); self.status.setWordWrap(True); r.addWidget(self.status)
        self.progress=QProgressBar(); r.addWidget(self.progress)
        row=QHBoxLayout(); self.run_button=self.button(row,'执行 / 恢复',self.resume)
        self.pause_button=self.button(row,'暂停',self.pause)
        self.button(row,'打开任务与日志',lambda:self.open_folder(False))
        self.button(row,'打开成果目录',lambda:self.open_folder(True)); r.addLayout(row)
        self.preview=QPlainTextEdit(); self.preview.setReadOnly(True); r.addWidget(self.preview,1)
        self.feedback=QPlainTextEdit(); self.feedback.setMaximumHeight(75); self.feedback.setPlaceholderText('本章修改意见；重写时请明确新的方向')
        r.addWidget(self.feedback)
        row=QHBoxLayout(); self.approve_button=self.button(row,'确认本章，继续',self.approve)
        self.revise_button=self.button(row,'按意见修改 / 重写本章',self.revise); r.addLayout(row)
        self.workflow.currentIndexChanged.connect(self.change_workflow)
        self.history.currentIndexChanged.connect(self.select_task)
        self.timer=QTimer(self); self.timer.setInterval(600); self.timer.timeout.connect(self.refresh_state); self.timer.start()
        self.change_workflow(); self.refresh_history()

    @staticmethod
    def button(layout,label,callback):
        button=QPushButton(label); button.clicked.connect(callback); layout.addWidget(button); return button

    @property
    def busy(self): return self.worker is not None

    def change_workflow(self):
        key=self.workflow.currentData(); self.mode.clear()
        for mode,label in MODES[key].items(): self.mode.addItem(label,mode)
        for widget,enabled in ((self.urls,key=='web'),(self.files,key!='web'),(self.skill,key=='fiction'),
            (self.chapters,key in ('fiction','outline')),(self.scenes,key=='fiction'),
            (self.find,key=='documents'),(self.replace,key=='documents'),(self.prefix,key=='documents'),(self.format,key=='documents')):
            widget.setEnabled(enabled)
        visible={'网站地址':key=='web','写作 Skill（目录或 .md）':key=='fiction',
                 '章节数量':key in ('fiction','outline'),'每章场景数（约400～650字/场景）':key=='fiction',
                 '起始章号（续写填下一章）':key in ('fiction','outline'),
                 '搜索 / 替换原文':key=='documents','替换为（可为空）':key=='documents',
                 '改名副本前缀':key=='documents','转换格式':key=='documents',
                 '输入文件（拖动调整顺序）':key!='web'}
        def show_item(item,show):
            if item is None: return
            if item.widget(): item.widget().setVisible(show)
            elif item.layout():
                for i in range(item.layout().count()): show_item(item.layout().itemAt(i),show)
        for row in range(self.form.rowCount()):
            label=self.form.itemAt(row,QFormLayout.LabelRole)
            if label and label.widget() and label.widget().text() in visible:
                show=visible[label.widget().text()]
                show_item(label,show); show_item(self.form.itemAt(row,QFormLayout.FieldRole),show)

    def choose_directory(self):
        path=QFileDialog.getExistingDirectory(self,'选择工作目录',self.directory.text())
        if path: self.directory.setText(path)
    def choose_skill(self):
        path=QFileDialog.getExistingDirectory(self,'选择包含 SKILL.md 的目录',self.directory.text())
        if path: self.skill.setText(path)
    def add_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'添加输入文件',self.directory.text(),'文档 (*.txt *.md *.docx *.pdf)')
        self.append_files(paths)
    def append_files(self,paths):
        current={self.files.item(i).text() for i in range(self.files.count())}
        for path in paths:
            if str(path) not in current: self.files.addItem(str(path)); current.add(str(path))
    def load_directory(self):
        root=Path(self.directory.text())
        if not root.is_dir(): return self.alert('请先选择有效工作目录。')
        paths=[p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.txt','.md','.docx','.pdf')
               and not set(p.relative_to(root).parts[:-1]) & {'Agent成果','userdata','temp','.git','outputs'}]
        paths.sort(key=lambda p:[(0,int(s)) if s.isdigit() else (1,s.lower()) for s in re.split(r'(\d+)',str(p))])
        self.append_files(paths)
    def remove_files(self):
        for item in self.files.selectedItems(): self.files.takeItem(self.files.row(item))
    def alert(self,message): QMessageBox.warning(self,'新版 Agent',str(message))
    def launch(self,action,callback=None):
        if self.busy or self._closing: return
        self.after=None
        worker=TaskWorker(action,self); self.worker=worker
        worker.result.connect(callback or (lambda _:None)); worker.error.connect(self.alert)
        worker.finished.connect(self.finished)
        self.create_button.setEnabled(False); self.history.setEnabled(False)
        worker.start()
    def finished(self):
        worker=self.worker; self.worker=None
        worker.deleteLater(); self.create_button.setEnabled(True); self.history.setEnabled(True)
        self.refresh_state()
        after=self.after; self.after=None
        # Transfer ownership to the next action before returning to the event
        # loop: no false idle/old-approval window or queued restart on shutdown.
        if after and not self._closing: after()
    def create(self):
        if self.busy: return
        if self.host._chat_worker is not None and self.host._chat_worker.isRunning(): return self.alert('请先停止普通对话中的模型任务。')
        try:
            if self.host._using_api(): raise ValueError('新版 Agent 使用本地 Ollama，请先在配置中选择 Ollama。')
            root=Path(self.directory.text().strip())
            if not self.directory.text().strip() or not root.is_dir(): raise ValueError('请选择有效工作目录。')
            config=ModelConfig(model=self.host._model_name(),base_url=self.host._base(),num_ctx=int(self.context.currentText()),num_predict=2048,read_timeout=300)
            workflow=self.workflow.currentData(); mode=self.mode.currentData()
            paths=[] if workflow=='web' else [self.files.item(i).text() for i in range(self.files.count())]
            options={'export_root':str(root.resolve()),'urls':self.urls.toPlainText(),'skill':self.skill.text().strip() if workflow=='fiction' else '',
                     'chapters':self.chapters.value(),'start_chapter':self.first_chapter.value(),'scenes':self.scenes.value(),'find':self.find.text(),'replace':self.replace.text(),
                     'prefix':self.prefix.text(),'format':self.format.currentText(),'encoding':self.encoding.currentText()}
            instruction=self.instruction.toPlainText()
            self.tabs.setCurrentIndex(1); self.status.setText('正在整理输入并建立可恢复任务…')
            self.launch(lambda token,event:create_task(self.task_root,workflow,mode,paths,instruction,config,options,token),self.created)
        except Exception as exc: self.alert(exc)
    def created(self,store):
        self.store=store; self.refresh_history(); self.after=self.resume
    def refresh_history(self):
        if self.busy and self.store is None: return
        selected=str(self.store.root) if self.store else self.history.currentData()
        self.history.blockSignals(True); self.history.clear()
        if self.task_root.exists():
            for path in sorted(self.task_root.iterdir(),reverse=True):
                if not (path/'task.json').is_file(): continue
                try:
                    task=TaskStore(path).task()
                    self.history.addItem(f"{path.name} · {WORKFLOWS.get(task.get('workflow'),'文档处理')}",str(path))
                except (OSError,ValueError): continue
        index=self.history.findData(selected)
        if index>=0: self.history.setCurrentIndex(index)
        self.history.blockSignals(False); self.select_task()
    def select_task(self):
        path=self.history.currentData()
        self.store=TaskStore(path) if path else None
        self.last_approval=None; self.refresh_state()
    def refresh_state(self):
        self.run_button.setEnabled(not self.busy and self.store is not None)
        self.pause_button.setEnabled(self.busy)
        self.approve_button.setEnabled(False); self.revise_button.setEnabled(False)
        if not self.store: return
        try:
            state=self.store.read_json('state.json'); steps=state.get('steps',{})
            done=sum(v=='succeeded' for v in steps.values())
            self.progress.setRange(0,max(1,len(steps))); self.progress.setValue(done)
            self.status.setText(f"{STATUS.get(state['status'],state['status'])} · {done}/{len(steps)} 步\n{state.get('error','')}")
            approval=state.get('approval') if state['status']=='awaiting_approval' else None
            if approval:
                if self.last_approval!=approval:
                    self.preview.setPlainText(self.store.path(approval['draft_path']).read_text(encoding='utf-8')); self.feedback.clear()
                self.last_approval=approval
                self.approve_button.setEnabled(not self.busy); self.revise_button.setEnabled(not self.busy)
            else:
                self.last_approval=None
                self.preview.setPlainText('任务目录：'+str(self.store.root)+'\n\n成果：\n'+'\n'.join(state.get('outputs',[]))+'\n\n每步输入、原始响应与校验记录位于 steps，事件日志位于 logs。')
        except (OSError,ValueError,KeyError) as exc: self.status.setText('正在更新任务状态：'+str(exc))
    def resume(self):
        if self.busy or not self.store: return
        if self.host._chat_worker is not None and self.host._chat_worker.isRunning(): return self.alert('请先停止普通对话中的模型任务。')
        store=self.store
        try:
            task=store.task()
            if task.get('mode')=='move' and not store.path('approvals/move.json').exists():
                plan=move_plan(store)
                details='\n\n'.join(p['source']+'\n→ '+p['destination'] for p in plan)
                box=QMessageBox(QMessageBox.Question,'确认移动文件','核验目标副本后，将移除以下源文件。是否执行？',QMessageBox.Yes|QMessageBox.No,self)
                box.setDetailedText(details); box.setDefaultButton(QMessageBox.No)
                if box.exec_()!=QMessageBox.Yes: return
                confirm_move(store,fingerprint(plan))
            config=ModelConfig(**task['config'])
            self.launch(lambda token,event:Engine(store,self.client_factory(config)).run(token,on_event=event))
        except Exception as exc: self.alert(exc)
    def pause(self):
        if self.worker: self.worker.token.cancel(); self.status.setText('正在暂停，保留已完成步骤…')
    def approve(self):
        if not self.last_approval or self.busy: return
        try: confirm_chapter(self.store,self.last_approval['draft_hash']); self.resume()
        except Exception as exc: self.alert(exc)
    def revise(self):
        if not self.last_approval or self.busy: return
        try: revise_chapter(self.store,self.feedback.toPlainText(),self.last_approval['draft_hash']); self.resume()
        except Exception as exc: self.alert(exc)
    def open_folder(self,outputs):
        if not self.store: return
        path=self.store.root
        if outputs:
            task=self.store.task(); root=task.get('options',{}).get('export_root')
            path=Path(root)/'Agent成果'/task['task_id'] if root else self.store.path('outputs')
        if path.exists(): QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    def shutdown(self):
        self._closing=True; self.after=None
        self.timer.stop()
        if self.worker:
            self.worker.token.cancel()
            self.worker.wait()
