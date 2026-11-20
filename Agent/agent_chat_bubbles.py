"""Native selectable message bubbles; copying always uses the original text."""
from PyQt5.QtCore import Qt,QTimer,QSize,pyqtSlot
from PyQt5.QtWidgets import (QApplication,QFrame,QLabel,QPushButton,QHBoxLayout,QVBoxLayout,
    QScrollArea,QWidget,QSizePolicy)
from PyQt5.QtGui import QIcon,QPixmap,QPainter,QPen,QColor
from styles.style_all import tk,theme
from utils.logger import get_logger

log=get_logger('agent.chat')

def copy_icon(color,copied=False):
    pixmap=QPixmap(20,20); pixmap.fill(Qt.transparent)
    painter=QPainter(pixmap); painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(color),1.6))
    if copied:
        painter.drawLine(4,10,8,14); painter.drawLine(8,14,16,5)
    else:
        painter.drawRoundedRect(7,7,10,11,2,2)
        painter.drawLine(4,13,3,13); painter.drawLine(3,13,3,3)
        painter.drawLine(3,3,13,3); painter.drawLine(13,3,13,4)
    painter.end(); return QIcon(pixmap)

class MessageBubble(QFrame):
    def __init__(self,message,parent=None):
        super().__init__(parent)
        self.message=dict(message)
        self.setObjectName('AgentMessageBubble')
        layout=QVBoxLayout(self); layout.setContentsMargins(15,11,15,14); layout.setSpacing(9)
        header=QHBoxLayout(); self.author=QLabel('你' if message['role']=='user' else 'Agent')
        header.addWidget(self.author); header.addStretch(1)
        self.copy_button=QPushButton(); self.copy_button.setCursor(Qt.PointingHandCursor)
        self.copy_button.setAccessibleName('复制这条消息的全部内容')
        self.copy_button.setToolTip('复制原始消息全文，保留换行')
        self.copy_button.clicked.connect(self.copy_message); header.addWidget(self.copy_button)
        layout.addLayout(header)
        self.body=QLabel(message['text']); self.body.setTextFormat(Qt.PlainText)
        self.body.setWordWrap(True); self.body.setTextInteractionFlags(Qt.TextSelectableByMouse|Qt.TextSelectableByKeyboard)
        self.body.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Preferred)
        layout.addWidget(self.body)
        self.copy_buttons=[self.copy_button]
        if len(message['text'])>800:
            footer=QHBoxLayout(); footer.addStretch(1)
            button=QPushButton(); button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName('复制这条长消息的全部内容'); button.clicked.connect(self.copy_message)
            footer.addWidget(button); layout.addLayout(footer); self.copy_buttons.append(button)
        for button in self.copy_buttons:
            button.setFixedSize(30,30); button.setIconSize(QSize(20,20))
            button.setToolTip('复制整段内容')
        self.reset_timer=QTimer(self); self.reset_timer.setSingleShot(True)
        self.reset_timer.timeout.connect(self.reset_copy_label)
        self.restyle()

    def copy_message(self):
        QApplication.clipboard().setText(self.message['text'])
        for button in self.copy_buttons:
            button.setProperty('copied',True); button.setToolTip('已复制')
        self.restyle()
        self.reset_timer.start(1500)

    @pyqtSlot()
    def reset_copy_label(self):
        for button in self.copy_buttons:
            button.setProperty('copied',False); button.setToolTip('复制整段内容')
        self.restyle()

    def restyle(self):
        color=tk('sel_bg') if self.message['role']=='user' else tk('panel')
        copy_color=tk('text') if self.message['role']=='user' else tk('accent')
        self.setStyleSheet(f'''QFrame#AgentMessageBubble {{background:{color};border:1px solid {tk('border')};border-radius:13px;}}
            QFrame#AgentMessageBubble QLabel {{background:transparent;border:0;color:{tk('text')};}}
            QFrame#AgentMessageBubble QPushButton {{background:transparent;border:0;color:{copy_color};padding:3px;}}
            QFrame#AgentMessageBubble QPushButton:hover {{background:{tk('panel_2')};border-radius:5px;}}''')
        self.author.setStyleSheet('font-weight:600;')
        for button in self.copy_buttons: button.setIcon(copy_icon(copy_color,bool(button.property('copied'))))

class BubbleTranscript(QScrollArea):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True); self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.container=QWidget(); self.rows=QVBoxLayout(self.container)
        self.rows.setContentsMargins(7,12,7,12); self.rows.setSpacing(15)
        self.rows.setAlignment(Qt.AlignTop); self.setWidget(self.container)
        self.messages=[]; self.bubbles=[]; self.row_widgets=[]
        self.scroll_timer=QTimer(self); self.scroll_timer.setSingleShot(True)
        self.scroll_timer.timeout.connect(self.restore_scroll)
        # Follow the latest message until the user scrolls away from the bottom.
        # The scrollbar range updates after the new bubble is laid out, so a
        # setValue during set_messages still sees the old maximum.
        self._stick=True; self._position=0; self._adjusting=False
        self._pending_restore=False; self._fitting=False; self._in_resize=False
        self._restore_passes=0; self._reported=False
        bar=self.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_value_changed)
        theme.changed.connect(self.restyle)

    def set_messages(self,messages,has_task=False):
        bar=self.verticalScrollBar()
        self._position=bar.value()
        rebuilt=self.messages!=messages[:len(self.messages)]
        if rebuilt:
            for bubble in self.bubbles: bubble.reset_timer.stop()
            for row in self.row_widgets:
                self.rows.removeWidget(row); row.hide(); row.deleteLater()
            self.messages=[]; self.bubbles=[]; self.row_widgets=[]
        for message in messages[len(self.messages):]:
            row=QWidget(); layout=QHBoxLayout(row); layout.setContentsMargins(0,0,0,0)
            bubble=MessageBubble(message,row)
            if message['role']=='user': layout.addStretch(1); layout.addWidget(bubble)
            else: layout.addWidget(bubble); layout.addStretch(1)
            self.rows.addWidget(row)
            self.row_widgets.append(row); self.bubbles.append(bubble); self.messages.append(dict(message))
        self._pending_restore=rebuilt and not self._stick
        self.resize_bubbles(); self._fit_content(); self._apply_follow()
        self.scroll_timer.start(0)

    def _fit_content(self):
        """Size the transcript to wrapped bubble height before the scroll range settles."""
        if self._fitting: return
        width=self.viewport().width()
        if width<40: return
        self._fitting=True
        try:
            height=self.rows.heightForWidth(width) if self.rows.hasHeightForWidth() else self.rows.sizeHint().height()
            height=max(0,int(height))
            if self.container.minimumHeight()!=height: self.container.setMinimumHeight(height)
        finally: self._fitting=False

    def _apply_follow(self):
        bar=self.verticalScrollBar()
        if self._stick: self._set_scroll(bar.maximum())
        elif self._pending_restore: self._set_scroll(min(self._position,bar.maximum()))

    def _set_scroll(self,value):
        bar=self.verticalScrollBar()
        value=max(bar.minimum(),min(int(value),bar.maximum()))
        if bar.value()==value: return
        self._adjusting=True
        try: bar.setValue(value)
        finally: self._adjusting=False

    def _on_range_changed(self,_minimum,_maximum):
        # setValue inside this signal re-enters it. A real window can recurse
        # until PyQt aborts the process. Follow on the next turn instead.
        try:
            if self._adjusting: return
            if self._stick or self._pending_restore: self.scroll_timer.start(0)
        except Exception:
            self._report('跟随对话滚动失败')

    def _on_value_changed(self,value):
        # Ignore the setValue we just did. A real user scroll updates whether we keep following.
        try:
            if self._adjusting: return
            bar=self.verticalScrollBar()
            self._pending_restore=False
            self._position=value
            self._stick=bar.maximum()<=0 or value>=bar.maximum()-24
        except Exception:
            self._report('记录对话滚动位置失败')

    def resize_bubbles(self):
        maximum=max(160,int(self.viewport().width()*.86)-15)
        for bubble in self.bubbles:
            metrics=bubble.body.fontMetrics()
            natural=max((metrics.horizontalAdvance(line) for line in bubble.message['text'].splitlines()),default=0)+34
            width=min(maximum,max(180,natural))
            if bubble.width()!=width: bubble.setFixedWidth(width)

    def resizeEvent(self,event):
        try:
            super().resizeEvent(event)
            if not hasattr(self,'bubbles') or self._in_resize or self._fitting: return
            self._in_resize=True
            try:
                self.resize_bubbles(); self._fit_content()
            finally:
                self._in_resize=False
        except Exception:
            self._report('调整对话气泡失败')

    def restore_scroll(self):
        try:
            self._restore_passes+=1
            if self._restore_passes>4:
                self._restore_passes=0; self._pending_restore=False; return
            self._fit_content(); self._apply_follow()
            bar=self.verticalScrollBar()
            if self._stick and bar.value()<bar.maximum():
                self.scroll_timer.start(0)
            else:
                self._pending_restore=False; self._restore_passes=0
        except Exception:
            self._report('恢复对话滚动位置失败')

    def restyle(self,*_):
        try:
            for bubble in self.bubbles: bubble.restyle()
        except Exception:
            self._report('刷新对话气泡样式失败')

    def _report(self,where):
        if self._reported: return
        self._reported=True
        log.exception(where)

    def toPlainText(self):
        return '\n\n'.join(message['text'] for message in self.messages)

    def closeEvent(self,event):
        self.scroll_timer.stop()
        for bubble in self.bubbles: bubble.reset_timer.stop()
        super().closeEvent(event)
