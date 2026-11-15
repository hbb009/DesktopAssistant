"""Native selectable message bubbles; copying always uses the original text."""
from PyQt5.QtCore import Qt,QTimer,QSize,pyqtSlot
from PyQt5.QtWidgets import (QApplication,QFrame,QLabel,QPushButton,QHBoxLayout,QVBoxLayout,
    QScrollArea,QWidget,QSizePolicy)
from PyQt5.QtGui import QIcon,QPixmap,QPainter,QPen,QColor
from styles.style_all import tk,theme

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
        self._bottom=True; self._position=0
        theme.changed.connect(self.restyle)

    def set_messages(self,messages,has_task=False):
        scroll=self.verticalScrollBar()
        self._bottom=scroll.value()>=scroll.maximum()-24; self._position=scroll.value()
        if self.messages!=messages[:len(self.messages)]:
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
        self.resize_bubbles(); self.scroll_timer.start(0)

    def resize_bubbles(self):
        maximum=max(160,int(self.viewport().width()*.86)-15)
        for bubble in self.bubbles:
            metrics=bubble.body.fontMetrics()
            natural=max((metrics.horizontalAdvance(line) for line in bubble.message['text'].splitlines()),default=0)+34
            bubble.setFixedWidth(min(maximum,max(180,natural)))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'bubbles'): self.resize_bubbles()

    def restore_scroll(self):
        self.container.layout().activate()
        scrollbar=self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum() if self._bottom else self._position)

    def restyle(self,*_):
        for bubble in self.bubbles: bubble.restyle()

    def toPlainText(self):
        return '\n\n'.join(message['text'] for message in self.messages)

    def closeEvent(self,event):
        self.scroll_timer.stop()
        for bubble in self.bubbles: bubble.reset_timer.stop()
        super().closeEvent(event)
