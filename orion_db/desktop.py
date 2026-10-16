"""PySide6 desktop retaining the uploaded v26 Orianbelt sidebar/chat/theme design."""
import html
import json
import re
import socket
import ssl
import sys
import time
import uuid
from pathlib import Path

from .config import Config, resolve_path
from .voice import MicrophoneRecorder, options as voice_options, transcribe

try:
    from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
    from PySide6.QtGui import QAction, QFontMetrics, QIcon
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
        QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
        QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter, QSystemTrayIcon,
        QTextEdit, QVBoxLayout, QWidget, QStyle,
    )
except ImportError as exc:
    raise RuntimeError('Desktop requires PySide6: pip install -r requirements.txt') from exc

THEMES = {
    'Midnight': ('#101522', '#182033', '#202b42', '#e7edf8', '#94a7c5', '#699bff'),
    'Ocean': ('#0b1d28', '#123040', '#1b4252', '#e4f8fb', '#9bc1cb', '#34c7cf'),
    'Forest': ('#101d19', '#1a3028', '#254237', '#e6f7e9', '#a2c6ad', '#66c68b'),
    'Light': ('#f3f6fb', '#ffffff', '#e8edf5', '#202b40', '#61718a', '#426bde'),
}


class Listener(QThread):
    packet = Signal(dict)
    disconnected = Signal()

    def __init__(self, sock):
        super().__init__()
        self.sock = sock

    def run(self):
        try:
            stream = self.sock.makefile('rb')
            while True:
                raw = stream.readline(2097152)
                if not raw:
                    break
                try:
                    self.packet.emit(json.loads(raw))
                except (ValueError, TypeError):
                    self.packet.emit({'type': 'error', 'message': 'Invalid JSON response from server'})
        except OSError:
            pass
        self.disconnected.emit()

    def stop(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class SpeechToTextWorker(QThread):
    recognized = Signal(str)
    failed = Signal(str)

    def __init__(self, audio, settings, parent=None):
        super().__init__(parent)
        self.audio = audio
        self.settings = settings

    def run(self):
        try:
            self.recognized.emit(transcribe(self.audio, self.settings))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.audio = None


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('ORION Employee Sign In')
        self.setMinimumWidth(390)
        layout = QFormLayout(self)
        self.username = QLineEdit()
        self.username.setPlaceholderText('Employee username')
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.returnPressed.connect(self.accept)
        layout.addRow('Username', self.username)
        layout.addRow('Password', self.password)
        button = QPushButton('Sign in')
        button.clicked.connect(self.accept)
        layout.addRow(button)


class DBDialog(QDialog):
    def __init__(self, profiles, backends, parent=None, suggestion='', sqlserver_auth_modes=None):
        super().__init__(parent)
        self.setWindowTitle('Connect to Live Database — explicit approval required')
        self.resize(540, 450)
        layout = QFormLayout(self)
        self.profiles = profiles
        self.profile = QComboBox()
        self.profile.addItem('Manual connection', -1)
        for index, item in enumerate(profiles):
            self.profile.addItem(str(item.get('label') or item.get('alias')), index)
        layout.addRow('Documented profile', self.profile)
        self.backend = QComboBox()
        for backend in backends:
            self.backend.addItem({'sqlite': 'SQLite', 'postgresql': 'PostgreSQL', 'sqlserver': 'SQL Server'}.get(backend, backend), backend)
        layout.addRow('Database type', self.backend)
        self.host = QLineEdit()
        layout.addRow('Host / IP', self.host)
        self.port = QLineEdit()
        layout.addRow('Port', self.port)
        self.database = QLineEdit()
        layout.addRow('Database name or SQLite file', self.database)
        browse = QPushButton('Browse SQLite file…')
        browse.clicked.connect(self.browse_sqlite)
        layout.addRow('', browse)
        self.auth_mode = QComboBox()
        auth_modes = sqlserver_auth_modes if sqlserver_auth_modes is not None else ['sql']
        if 'sql' in auth_modes:
            self.auth_mode.addItem('SQL Server login / password', 'sql')
        if 'windows' in auth_modes:
            self.auth_mode.addItem('Windows integrated authentication', 'windows')
        layout.addRow('SQL Server authentication', self.auth_mode)
        self.auth_mode.currentIndexChanged.connect(self.update_backend)
        self.username = QLineEdit()
        layout.addRow('DB username', self.username)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow('DB password', self.password)
        self.tls = QCheckBox('Use TLS / encrypted connection')
        self.tls.setChecked(True)
        layout.addRow('', self.tls)
        self.trust = QCheckBox('Trust SQL Server certificate (test environments only)')
        layout.addRow('', self.trust)
        self.note = QLabel('Credentials are sent to the local ORION server only for this connection.\nThey are not saved in YAML, chat history or logs.')
        self.note.setWordWrap(True)
        layout.addRow('', self.note)
        connect = QPushButton('Review and connect')
        connect.clicked.connect(self.accept)
        layout.addRow('', connect)
        self.profile.currentIndexChanged.connect(self.fill_profile)
        self.backend.currentIndexChanged.connect(self.update_backend)
        self.fill_profile()
        if suggestion:
            for i, p in enumerate(profiles):
                if (str(p.get('alias', '')).casefold() in suggestion.casefold() or
                    str(p.get('database', '')).casefold() in suggestion.casefold()):
                    self.profile.setCurrentIndex(i + 1)
                    break

    def fill_profile(self):
        index = self.profile.currentData()
        if index is not None and index >= 0:
            p = self.profiles[index]
            match = self.backend.findData(p.get('backend'))
            if match >= 0:
                self.backend.setCurrentIndex(match)
            self.host.setText(str(p.get('host', '')))
            self.port.setText(str(p.get('port', '')))
            self.database.setText(str(p.get('database', '')))
        self.update_backend()

    def update_backend(self):
        sqlite = self.backend.currentData() == 'sqlite'
        sqlserver = self.backend.currentData() == 'sqlserver'
        self.auth_mode.setEnabled(sqlserver)
        windows_auth = sqlserver and self.auth_mode.currentData() == 'windows'
        for widget in (self.host, self.port, self.tls):
            widget.setEnabled(not sqlite)
        for widget in (self.username, self.password):
            widget.setEnabled(not sqlite and not windows_auth)
        self.trust.setEnabled(sqlserver)

    def browse_sqlite(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose SQLite database', '', 'SQLite databases (*.sqlite *.db *.sqlite3);;All files (*)')
        if path:
            index = self.backend.findData('sqlite')
            if index >= 0:
                self.backend.setCurrentIndex(index)
            self.database.setText(path)

    def payload(self):
        backend = self.backend.currentData()
        if not self.database.text().strip():
            raise ValueError('Enter a database name or SQLite file path')
        if backend != 'sqlite' and not self.host.text().strip():
            raise ValueError('Database host is required')
        windows_auth = backend == 'sqlserver' and self.auth_mode.currentData() == 'windows'
        if backend != 'sqlite' and not windows_auth and not (self.username.text().strip() and self.password.text()):
            raise ValueError('Database username and password are required')
        return {'type': 'db_connect', 'backend': backend, 'host': self.host.text().strip(),
                'port': self.port.text().strip(), 'database': self.database.text().strip(),
                'username': '' if windows_auth else self.username.text().strip(),
                'password': '' if windows_auth else self.password.text(),
                'auth_mode': self.auth_mode.currentData() if backend == 'sqlserver' else 'sql',
                'tls': self.tls.isChecked(), 'trust_server_certificate': self.trust.isChecked(), 'confirmed': True}


class DocumentReviewDialog(QDialog):
    """Authorized reviewer panel; suggestions never auto-select grants."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('Review document permissions')
        self.resize(760, 540)
        self.desktop = parent
        self.items = []
        self.selected = None
        layout = QVBoxLayout(self)
        self.heading = QLabel('Loading documents…')
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel('Show:'))
        self.status_filter = QComboBox()
        for label, value in [('All documents', 'all'), ('Pending', 'pending'),
                             ('Approved', 'approved'), ('Rejected', 'rejected')]:
            self.status_filter.addItem(label, value)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        filter_row.addWidget(self.status_filter)
        layout.addLayout(filter_row)
        layout.addWidget(self.heading)
        self.documents = QListWidget()
        self.documents.currentRowChanged.connect(self._select)
        layout.addWidget(self.documents, 2)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 2)
        roles_row = QHBoxLayout()
        self.role_checks = {}
        for role in ('hr', 'manager', 'finance', 'analyst', 'employee', 'viewer'):
            checkbox = QCheckBox(role.title())
            self.role_checks[role] = checkbox
            roles_row.addWidget(checkbox)
        layout.addLayout(roles_row)
        actions = QHBoxLayout()
        self.refresh_btn = QPushButton('Refresh documents')
        self.refresh_btn.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_btn)
        self.preview_btn = QPushButton('Preview indexed text')
        self.preview_btn.clicked.connect(self.preview)
        actions.addWidget(self.preview_btn)
        self.reject_btn = QPushButton('Reject access')
        self.reject_btn.clicked.connect(self.reject_access)
        actions.addWidget(self.reject_btn)
        self.approve_btn = QPushButton('Approve selected roles')
        self.approve_btn.clicked.connect(self.approve)
        actions.addWidget(self.approve_btn)
        self.close_btn = QPushButton('Close')
        self.close_btn.clicked.connect(self.reject_dialog)
        actions.addWidget(self.close_btn)
        layout.addLayout(actions)
        self._set_controls()

    def reject_dialog(self):
        QDialog.reject(self)

    def refresh(self, *_):
        self.desktop.send({'type': 'document_review_queue',
                           'status': self.status_filter.currentData(), 'page_size': 100})

    def populate(self, packet):
        self.items = packet.get('items', [])
        self.heading.setText(f"{self.status_filter.currentText()}: {packet.get('total', 0)} document(s)" +
                             (' — showing first 100' if packet.get('total', 0) > 100 else ''))
        self.documents.blockSignals(True)
        self.documents.clear()
        for item in self.items:
            self.documents.addItem(item['source'] + '  [' + item.get('status', 'pending') + ']')
        self.documents.blockSignals(False)
        self.documents.setCurrentRow(0 if self.items else -1)
        self._select(self.documents.currentRow())

    def _select(self, index):
        self.selected = self.items[index] if 0 <= index < len(self.items) else None
        for checkbox in self.role_checks.values():
            checkbox.setChecked(False)
        if self.selected:
            item = self.selected
            for role in item.get('approved_roles', []):
                if role in self.role_checks:
                    self.role_checks[role].setChecked(True)
            self.details.setPlainText(
                'File: ' + item['source'] + '\nDepartment: ' + item['department'] +
                '\nDocument type: ' + item['document_type'] +
                '\nSensitivity: ' + item['sensitivity'] +
                '\nClassification: ' + item['classification_status'] +
                '\nQwen3 suggested roles (NOT approved): ' + ', '.join(item.get('suggested_roles', [])) +
                '\nCurrent status: ' + item.get('status', 'pending') +
                '\nApproved roles: ' + ', '.join(item.get('approved_roles', [])) +
                '\n\nSelect roles yourself, then approve. Admin access is implicit.')
        else:
            self.details.setPlainText('No documents in this view. Try All documents or synchronize knowledge.')
        self._set_controls()

    def _set_controls(self):
        active = self.selected is not None
        for control in (self.preview_btn, self.reject_btn, self.approve_btn):
            control.setEnabled(active)
        for checkbox in self.role_checks.values():
            checkbox.setEnabled(active)

    def preview(self):
        if self.selected:
            self.desktop.send({'type': 'document_review_preview',
                               'source': self.selected['source'], 'sha256': self.selected['sha256']})

    def approve(self):
        if not self.selected:
            return
        roles = [role for role, checkbox in self.role_checks.items() if checkbox.isChecked()]
        if not roles:
            QMessageBox.warning(self, 'Choose access roles', 'Select at least one role. Suggestions do not grant access.')
            return
        if QMessageBox.question(self, 'Confirm document access',
                                'Grant ' + ', '.join(roles) + ' access to\n' + self.selected['source'] + '?',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.desktop.send({'type': 'document_review_decide', 'source': self.selected['source'],
                               'sha256': self.selected['sha256'], 'approve': True, 'roles': roles,
                               'expected_status': self.selected['status'],
                               'expected_roles': self.selected.get('approved_roles', [])})

    def reject_access(self):
        if self.selected and QMessageBox.question(self, 'Reject document access',
                'Deny non-admin access to\n' + self.selected['source'] + '?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.desktop.send({'type': 'document_review_decide', 'source': self.selected['source'],
                               'sha256': self.selected['sha256'], 'approve': False, 'roles': [],
                               'expected_status': self.selected['status'],
                               'expected_roles': self.selected.get('approved_roles', [])})


class Desktop(QMainWindow):
    def __init__(self, config=None):
        super().__init__()
        self.cfg = config or Config()
        settings = self.cfg.get()
        self.setWindowTitle(settings['app'].get('name', 'Orianbelt'))
        self.resize(int(settings['ui'].get('width', 1050)), int(settings['ui'].get('height', 720)))
        self.setMinimumSize(730, 490)
        self.sock = None
        self.listener = None
        self.connected = False
        self._connecting = False
        self._closing = False
        self.busy = False
        self.current_thread = None
        self.client_id = str(uuid.uuid4())
        self.auth_token = None
        self.demo_session = False
        self.username = None
        self.role = 'viewer'
        self.review_dialog = None
        self.assistant_name = settings['app'].get('assistant_name', 'Orianbelt')
        self._busy_since = 0
        self._pending_db_suggestion = ''
        self._last_tables = []
        self.voice_settings = self.cfg.get()
        self.voice_options = voice_options(self.voice_settings)
        self.voice_recorder = None
        self.voice_worker = None
        self.voice_timer = QTimer(self)
        self.voice_timer.setSingleShot(True)
        self.voice_timer.timeout.connect(self.stop_voice_recording)
        self._build_ui()
        self.apply_theme(settings['ui'].get('theme', 'Midnight'))
        self._setup_tray()
        self.retry_timer = QTimer(self)
        self.retry_timer.setInterval(max(1, int(settings['client'].get('reconnect_seconds', 4))) * 1000)
        self.retry_timer.timeout.connect(self.connect_server)
        self.retry_timer.start()
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self._update_busy)
        self.elapsed_timer.start()
        QTimer.singleShot(100, self.connect_server)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter)
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 20, 14, 14)
        side.setSpacing(13)
        brand = QLabel('✦  ORIANBELT')
        brand.setObjectName('brand')
        side.addWidget(brand)
        self.new_button = QPushButton('＋  New chat')
        self.new_button.setObjectName('newChat')
        self.new_button.clicked.connect(lambda: self.send({'type': 'new_chat'}) if not self.busy else None)
        side.addWidget(self.new_button)
        recent = QLabel('RECENT CHATS')
        recent.setObjectName('muted')
        side.addWidget(recent)
        self.chat_list = QListWidget()
        self.chat_list.setObjectName('recentList')
        self.chat_list.itemClicked.connect(self.open_chat)
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self.chat_context_menu)
        self.chat_list.setSpacing(3)
        side.addWidget(self.chat_list, 1)
        side.addWidget(QLabel('Offline • Organization LAN'))
        splitter.addWidget(sidebar)
        main = QWidget()
        content = QVBoxLayout(main)
        content.setContentsMargins(24, 16, 24, 18)
        content.setSpacing(14)
        header = QHBoxLayout()
        self.chat_title = QLabel('New conversation')
        self.chat_title.setObjectName('title')
        header.addWidget(self.chat_title)
        header.addStretch()
        self.status = QLabel('Connecting…')
        self.status.setObjectName('muted')
        header.addWidget(self.status)
        self.gear = QPushButton('⚙')
        self.gear.setObjectName('gear')
        self.gear.setFixedSize(42, 40)
        self.gear.clicked.connect(self.open_settings)
        header.addWidget(self.gear)
        content.addLayout(header)
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setObjectName('conversation')
        content.addWidget(self.chat_display, 1)
        bottom = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText('Ask about documented databases or request a live connection…')
        self.input.returnPressed.connect(self.send_message)
        bottom.addWidget(self.input, 1)
        self.mic_button = QPushButton('🎙 Speak')
        self.mic_button.setObjectName('mic')
        self.mic_button.setToolTip('Speak, then Stop to put recognized text in the message box; press Send when ready')
        self.mic_button.setFixedWidth(105)
        self.mic_button.clicked.connect(self.toggle_voice_recording)
        self.mic_button.setVisible(self.voice_options['enabled'])
        bottom.addWidget(self.mic_button)
        self.send_btn = QPushButton('➤')
        self.send_btn.setObjectName('send')
        self.send_btn.setFixedWidth(52)
        self.send_btn.clicked.connect(self.send_message)
        bottom.addWidget(self.send_btn)
        content.addLayout(bottom)
        tools = QHBoxLayout()
        self.db_button = QPushButton('Connect Database')
        self.db_button.clicked.connect(self.connect_database)
        tools.addWidget(self.db_button)
        self.tables_button = QPushButton('Browse Tables')
        self.tables_button.clicked.connect(lambda: self.send({'type': 'db_tables'}))
        tools.addWidget(self.tables_button)
        self.sync_button = QPushButton('Sync Knowledge')
        self.sync_button.clicked.connect(lambda: self.send({'type': 'sync_knowledge'}))
        tools.addWidget(self.sync_button)
        self.discovery_button = QPushButton('Discovery Status')
        self.discovery_button.clicked.connect(lambda: self.send({'type': 'discovery_status'}))
        tools.addWidget(self.discovery_button)
        self.export_button = QPushButton('Export Excel')
        self.export_button.clicked.connect(self.export_excel)
        tools.addWidget(self.export_button)
        tools.addStretch()
        content.addLayout(tools)
        splitter.addWidget(main)
        splitter.setSizes([250, 800])
        splitter.setStretchFactor(1, 1)
        self.welcome()
        self.set_ready(False)

    def apply_theme(self, name):
        bg, panel, hover, fg, muted, accent = THEMES.get(name, THEMES['Midnight'])
        self.setStyleSheet(f'''
            QWidget {{ background: {bg}; color: {fg}; font: 13px 'Segoe UI'; }}
            QWidget#sidebar {{ background: {panel}; }}
            QLabel#brand {{ font-size: 19px; font-weight: 800; color: {accent}; }}
            QLabel#title {{ font-size: 18px; font-weight: 700; }}
            QLabel#muted {{ color: {muted}; font-size: 11px; font-weight: 600; }}
            QTextEdit#conversation {{ background: {bg}; border: none; padding: 18px 8px; font-size: 14px; }}
            QListWidget#recentList {{ background: {panel}; border: none; outline: none; }}
            QListWidget#recentList::item {{ padding: 10px; border-radius: 8px; }}
            QListWidget#recentList::item:hover {{ background: {hover}; }}
            QListWidget#recentList::item:selected {{ background: {hover}; color: {accent}; }}
            QLineEdit {{ background: {panel}; border: 1px solid {hover}; border-radius: 13px; padding: 12px; font-size: 14px; }}
            QLineEdit:focus {{ border: 1px solid {accent}; }}
            QPushButton {{ background: {hover}; border: none; border-radius: 10px; padding: 11px; font-weight: 600; }}
            QPushButton:hover {{ background: {accent}; color: {bg}; }}
            QPushButton#newChat {{ text-align: left; background: {hover}; }}
            QPushButton#send {{ background: {accent}; color: {bg}; font-size: 19px; }}
            QPushButton#gear {{ background: transparent; font-size: 22px; }}
            QComboBox {{ background: {panel}; border: 1px solid {hover}; padding: 8px; }}
            QDialog {{ background: {bg}; }}
            QSplitter::handle {{ background: {hover}; width: 1px; }}
        ''')

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self)
        menu = QMenu(self)
        show = QAction('Open Orianbelt', self)
        show.triggered.connect(self.showNormal)
        quit_action = QAction('Exit', self)
        quit_action.triggered.connect(self.force_exit)
        menu.addAction(show)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def welcome(self):
        name = html.escape(self.assistant_name)
        self.chat_display.setHtml(f'<div style="margin-top:90px;text-align:center"><h2>Hello, I’m {name} ✦</h2><p>Ask about your database documentation or connect to a live database.</p></div>')
        self.chat_title.setText('New conversation')

    def set_ready(self, ready):
        usable = ready and self.connected and bool(self.auth_token)
        self.input.setEnabled(usable and not self.busy)
        self.send_btn.setEnabled(usable and not self.busy)
        self.new_button.setEnabled(usable and not self.busy)
        self.chat_list.setEnabled(usable and not self.busy)
        self.db_button.setEnabled(usable and not self.busy and self.role in ('admin', 'analyst'))
        self.tables_button.setEnabled(usable and not self.busy and self.role in ('admin', 'analyst'))
        self.sync_button.setEnabled(usable and not self.busy and self.role == 'admin')
        self.discovery_button.setEnabled(usable and not self.busy and not self.demo_session)
        self.export_button.setEnabled(usable and not self.busy and self.role in ('admin', 'analyst'))
        # Keep Stop accessible while recording; otherwise require an authenticated chat.
        self.mic_button.setEnabled(self.voice_options['enabled'] and
                                   (bool(self.voice_recorder and self.voice_recorder.recording) or
                                    (usable and not self.busy and self.voice_worker is None)))

    def toggle_voice_recording(self):
        if self.voice_recorder and self.voice_recorder.recording:
            self.stop_voice_recording()
            return
        if not self.voice_options['enabled'] or not self.auth_token or self.busy or not self.current_thread:
            return
        try:
            self.voice_recorder = MicrophoneRecorder(self.cfg.get())
            self.voice_recorder.start()
        except Exception as exc:
            self.voice_recorder = None
            QMessageBox.warning(self, 'Microphone unavailable', str(exc))
            return
        self.mic_button.setText('■ Stop')
        self.status.setText('🎙 Listening… speak your request')
        self.voice_timer.start(self.voice_options['max_record_seconds'] * 1000)
        self.set_ready(True)

    def stop_voice_recording(self):
        self.voice_timer.stop()
        recorder, self.voice_recorder = self.voice_recorder, None
        self.mic_button.setText('🎙 Speak')
        if recorder is None or not recorder.recording:
            return
        try:
            audio = recorder.stop()
        except Exception as exc:
            QMessageBox.warning(self, 'Speech capture failed', str(exc))
            self.set_ready(True)
            return
        self.status.setText('Transcribing locally…')
        self.voice_worker = SpeechToTextWorker(audio, self.cfg.get(), self)
        self.voice_worker.recognized.connect(self._voice_recognized)
        self.voice_worker.failed.connect(self._voice_failed)
        self.voice_worker.finished.connect(self._voice_finished)
        self.voice_worker.start()
        self.set_ready(True)

    def _voice_recognized(self, transcript):
        # Dictation only: let the user review/edit before sending.
        # Do not honor voice.auto_send; speaking must never submit a chat.
        self.input.setText(transcript)
        self.status.setText('Speech recognized — review and press Send')
        self.input.setFocus()

    def _voice_failed(self, error):
        self.status.setText('Speech recognition failed')
        QMessageBox.warning(self, 'Speech recognition', error)

    def _voice_finished(self):
        worker, self.voice_worker = self.voice_worker, None
        if worker is not None:
            worker.deleteLater()
        self.set_ready(True)

    def connect_server(self):
        if self.connected or self._connecting or self._closing:
            return
        self._connecting = True
        try:
            options = self.cfg.get()['client']
            host = str(options['host'])
            sock = socket.create_connection((host, int(options['port'])), timeout=float(options.get('connect_timeout_seconds', 3)))
            ca = str(options.get('tls_ca') or '').strip()
            if ca:
                ctx = ssl.create_default_context(cafile=str(resolve_path(ca)))
                sock = ctx.wrap_socket(sock, server_hostname=host)
            elif host not in ('127.0.0.1', 'localhost', '::1'):
                sock.close()
                raise OSError('Remote TCP requires a trusted TLS CA in YAML')
            sock.settimeout(None)  # No AI inference read timeout: wait asynchronously for server response.
            listener = Listener(sock)
            listener.packet.connect(self.receive)
            listener.disconnected.connect(lambda l=listener: self.on_disconnected(l))
            self.sock, self.listener = sock, listener
            self.connected = True
            demo_mode = (self.cfg.get().get('security', {}).get('authentication', {})
                         .get('mode', 'required') == 'local_demo')
            self.status.setText('● Connected — starting local demo' if demo_mode else '● Connected — sign in')
            listener.start()
            if demo_mode:
                QTimer.singleShot(100, lambda: self.send({'type': 'demo_login'}))
            else:
                QTimer.singleShot(100, self.prompt_login)
        except OSError:
            self.status.setText('● Disconnected — retrying…')
            self.set_ready(False)
        finally:
            self._connecting = False

    def prompt_login(self):
        if self.cfg.get().get('security', {}).get('authentication', {}).get('mode', 'required') == 'local_demo':
            return
        if not self.connected or self.auth_token:
            return
        dialog = LoginDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.username.text().strip() and dialog.password.text():
            self.send({'type': 'login', 'username': dialog.username.text().strip(), 'password': dialog.password.text()})
            dialog.password.clear()
        else:
            self.status.setText('● Connected — sign in via Settings')

    def on_disconnected(self, listener=None):
        if self.voice_recorder is not None:
            self.voice_timer.stop()
            self.voice_recorder.discard()
            self.voice_recorder = None
            self.mic_button.setText('🎙 Speak')
        if listener is not None and listener is not self.listener:
            return
        self.connected = False
        self.busy = False
        self.auth_token = None
        self.demo_session = False
        self.current_thread = None
        self.status.setText('● Disconnected — retrying…')
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock, self.listener = None, None
        self.set_ready(False)

    def send(self, packet):
        if not self.sock or not self.connected:
            return False
        packet = dict(packet)
        packet['client_id'] = self.client_id
        if self.auth_token:
            packet['token'] = self.auth_token
        try:
            self.sock.sendall((json.dumps(packet, ensure_ascii=False) + '\n').encode('utf-8'))
            return True
        except OSError as exc:
            self._append('Connection error', str(exc))
            self.on_disconnected(self.listener)
            return False

    @staticmethod
    def _message_html(speaker, message, assistant_name='ORION'):
        """Escaped, direction-aware chat bubbles for Qt rich text."""
        is_user = speaker == 'You'
        side = 'right' if is_user else 'left'
        background = '#3158a0' if is_user else '#25334b'
        label = 'You' if is_user else str(speaker or assistant_name)
        safe_label = html.escape(label, quote=True)
        safe_message = html.escape(str(message), quote=True).replace('\n', '<br/>')
        # Qt's QTextDocument supports table alignment/background more reliably
        # than CSS flexbox or floats, which are not supported here.
        return (
            '<table width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td align="{side}">'
            f'<table bgcolor="{background}" cellspacing="0" cellpadding="9" '
            'width="76%"><tr><td>'
            f'<span style="color:#ffffff;"><b>{safe_label}</b><br/>'
            f'{safe_message}</span>'
            '</td></tr></table></td></tr></table><p></p>'
        )

    def _append(self, speaker, message):
        self.chat_display.append(self._message_html(speaker, message, self.assistant_name))
        self.chat_display.moveCursor(self.chat_display.textCursor().MoveOperation.End)

    def _update_threads(self, threads):
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        for thread in threads:
            title = ' '.join(str(thread.get('title', 'New chat')).split())
            item = QListWidgetItem('◷  ' + title)
            item.setToolTip(title)
            item.setData(Qt.ItemDataRole.UserRole, thread.get('id'))
            self.chat_list.addItem(item)
            if thread.get('id') == self.current_thread:
                self.chat_list.setCurrentItem(item)
        self.chat_list.blockSignals(False)

    def chat_context_menu(self, point):
        item = self.chat_list.itemAt(point)
        if item is None or self.busy:
            return
        menu = QMenu(self)
        delete = menu.addAction('Delete conversation')
        if menu.exec(self.chat_list.mapToGlobal(point)) == delete:
            answer = QMessageBox.question(self, 'Delete conversation', 'Delete this conversation and all its messages?',
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self.send({'type': 'delete_chat', 'thread_id': item.data(Qt.ItemDataRole.UserRole)})

    def open_chat(self, item):
        if not self.busy:
            self.send({'type': 'open_chat', 'thread_id': item.data(Qt.ItemDataRole.UserRole)})

    def send_message(self):
        question = self.input.text().strip()
        if not question or not self.current_thread or self.busy or not self.auth_token:
            return
        if self.send({'type': 'chat', 'thread_id': self.current_thread, 'message': question}):
            if re.search(r'\bconnect\b.*\b(db|database|sql|postgres|sqlite)\b', question, re.I):
                self._pending_db_suggestion = question
            if self.chat_title.text() == 'New conversation':
                self.chat_display.clear()
            self._append('You', question)
            self.input.clear()
            self.busy = True
            self._busy_since = time.monotonic()
            self.status.setText('Processing request…')
            self.set_ready(True)

    def _update_busy(self):
        if self.busy:
            elapsed = int(time.monotonic() - self._busy_since)
            self.status.setText(f'Processing request… {elapsed}s')

    def connect_database(self):
        self._pending_db_suggestion = self._pending_db_suggestion or ''
        self.send({'type': 'db_profiles'})

    def _show_db_dialog(self, packet):
        dialog = DBDialog(packet.get('profiles', []), packet.get('allowed_backends', []), self,
                          self._pending_db_suggestion, packet.get('sqlserver_auth_modes', ['sql']))
        self._pending_db_suggestion = ''
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            payload = dialog.payload()
        except ValueError as exc:
            QMessageBox.warning(self, 'Missing database settings', str(exc))
            return
        summary = ('Backend: ' + str(payload['backend']) + '\nHost: ' + str(payload['host'] or '(local file)') +
                   '\nDatabase: ' + str(payload['database']) + '\nUsername: ' + str(payload['username'] or '(not required)') +
                   '\n\nOpen a new live read-only connection?')
        if QMessageBox.question(self, 'Approve live database connection', summary,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.busy = True
            self._busy_since = time.monotonic()
            self.status.setText('Verifying database connection…')
            self.set_ready(True)
            self.send(payload)
        payload['password'] = ''  # Drop credentials after sending.

    def _show_tables(self, packet):
        tables = packet.get('tables', [])
        self._last_tables = tables
        if not tables:
            self._append(self.assistant_name, 'The live database has no accessible tables or views.')
            return
        names = [t['schema'] + '.' + t['table'] for t in tables]
        self._append(self.assistant_name, 'Live tables (verified connection):\n' + '\n'.join(names[:60]))
        chosen, ok = QInputDialog.getItem(self, 'Preview live table', 'Choose a table:', names, 0, False)
        if not ok:
            return
        max_rows = max(1, int(packet.get('max_preview_rows', 100)))
        count, ok = QInputDialog.getInt(self, 'Read-only preview', 'Maximum rows:', min(20, max_rows), 1, max_rows, 1)
        if ok:
            item = tables[names.index(chosen)]
            self.busy = True
            self._busy_since = time.monotonic()
            self.set_ready(True)
            self.send({'type': 'db_preview', 'schema': item['schema'], 'table': item['table'], 'limit': count})

    def export_excel(self, tables=None):
        tables = tables if tables is not None else self._last_tables
        if not tables:
            self._append(self.assistant_name, 'Browse Tables first, or ask to generate a report after connecting.')
            self.send({'type': 'db_tables'})
            return
        labels = [t['schema'] + '.' + t['table'] for t in tables]
        selected, ok = QInputDialog.getItem(self, 'Export live table to Excel', 'Verified table:', labels, 0, False)
        if ok:
            t = tables[labels.index(selected)]
            self.busy = True
            self._busy_since = time.monotonic()
            self.set_ready(True)
            self.send({'type': 'report_generate', 'schema': t['schema'], 'table': t['table']})

    def receive(self, packet):
        kind = packet.get('type')
        if kind == 'login_ok':
            self.auth_token = packet['token']
            self.demo_session = bool(packet.get('demo', False))
            self.username = packet['username']
            self.role = packet.get('role', 'viewer')
            self.status.setText(('● Local demo: ' if self.demo_session else '● Signed in: ') + self.username)
            self.send({'type': 'hello'})
        elif kind == 'profile':
            self.assistant_name = packet.get('assistant_name', self.assistant_name)
            self.role = packet.get('role', self.role)
            threads = packet.get('threads', [])
            self._update_threads(threads)
            self.set_ready(True)
            if threads:
                self.send({'type': 'open_chat', 'thread_id': threads[0]['id']})
            else:
                self.send({'type': 'new_chat'})
        elif kind == 'chat_opened':
            self.current_thread = packet.get('thread_id')
            self._update_threads(packet.get('threads', []))
            title = next((t.get('title') for t in packet.get('threads', []) if t.get('id') == self.current_thread), 'New chat')
            self.chat_title.setText('New conversation' if title == 'New chat' else str(title))
            history = packet.get('history', [])
            if not history:
                self.welcome()
            else:
                self.chat_display.clear()
                for entry in history:
                    self._append('You' if entry['role'] == 'user' else self.assistant_name, entry['content'])
                self.chat_display.moveCursor(self.chat_display.textCursor().MoveOperation.End)
            self.status.setText('● Connected')
            self.set_ready(True)
        elif kind == 'answer':
            self.busy = False
            structured = packet.get('response_json')
            if isinstance(structured, dict):
                display = (json.dumps(structured, ensure_ascii=False, indent=2) if packet.get('presentation') == 'json'
                           else structured.get('answer', packet.get('answer', '')))
            else:
                display = packet.get('answer', '')
            self._append(self.assistant_name, display)
            self._update_threads(packet.get('threads', []))
            self.status.setText('● Connected')
            self.set_ready(True)
        elif kind == 'db_connect_required':
            self.busy = False
            self._append(self.assistant_name, packet.get('message', 'Connect to a live database.'))
            self._update_threads(packet.get('threads', []))
            self.set_ready(True)
            self.connect_database()
        elif kind == 'db_table_selection_required':
            self.busy = False
            self._append(self.assistant_name, packet.get('message', 'Select a table.'))
            self.set_ready(True)
            self.send({'type': 'db_tables'})
        elif kind == 'db_profiles':
            self._show_db_dialog(packet)
        elif kind == 'db_connected':
            self.busy = False
            self._append(self.assistant_name, 'Verified live connection: ' + packet.get('backend', '') + ' / ' + packet.get('database', ''))
            self.status.setText('● Live DB connected')
            self.set_ready(True)
        elif kind == 'db_tables':
            self._show_tables(packet)
        elif kind == 'report_table_selection_required':
            self.busy = False
            self.set_ready(True)
            self._append(self.assistant_name, packet.get('message', 'Select a table.'))
            self.export_excel(packet.get('tables', []))
        elif kind == 'report_ready':
            self.busy = False
            self._append(self.assistant_name, 'Excel report generated: ' + packet.get('path', '') +
                         '\nSQL: ' + packet.get('sql', '') +
                         '\nRows: ' + str(packet.get('row_count', 0)) +
                         (' (limit reached)' if packet.get('truncated') else '') +
                         '\nFile is saved on the ORION server machine.')
            self.status.setText('● Excel report ready')
            self.set_ready(True)
        elif kind == 'db_password_required':
            self._append(self.assistant_name, packet.get('message', 'Database password required.'))
            self.connect_database()
        elif kind == 'discovery_status':
            details = packet.get('catalog', {})
            knowledge = packet.get('knowledge', {})
            self._append(self.assistant_name,
                         'Discovery: ' + packet.get('status', 'running') +
                         '\nKnowledge files: ' + str(knowledge.get('files', 0)) +
                         '\nLast knowledge sync: ' + str(knowledge.get('last_sync', 'not yet')) +
                         '\nDiscovered databases: ' + str(details.get('databases', 0)) +
                         '\nDiscovered tables: ' + str(details.get('table_definitions', 0)) +
                         '\nLast live schema scan: ' + str(details.get('last_schema_scan', 'not yet')))
        elif kind == 'schema_catalog':
            databases = packet.get('databases', [])
            self._append(self.assistant_name, 'Discovered databases:\n' + '\n'.join(
                x['name'] + ' — ' + str(len(x['tables'])) + ' tables — ' +
                ('live verified' if x.get('verified') else 'documented only') for x in databases))
        elif kind == 'db_rows':
            self.busy = False
            headers = packet.get('columns', [])
            rows = packet.get('rows', [])
            lines = [' | '.join(map(str, headers))]
            lines += [' | '.join('' if x is None else str(x) for x in row) for row in rows]
            self._append(self.assistant_name + ' — LIVE ' + packet.get('database', ''), '\n'.join(lines))
            self.status.setText('● Live DB connected')
            self.set_ready(True)
        elif kind == 'document_review_queue':
            if self.role in ('admin', 'manager'):
                if self.review_dialog is None or not self.review_dialog.isVisible():
                    self.review_dialog = DocumentReviewDialog(self)
                    self.review_dialog.show()
                self.review_dialog.populate(packet)
        elif kind == 'document_review_preview':
            if self.role in ('admin', 'manager') and self.review_dialog and self.review_dialog.isVisible():
                QMessageBox.information(self.review_dialog, 'Indexed document preview: ' + packet.get('source', ''),
                    packet.get('text', '') + ('\n\n[Preview truncated]' if packet.get('truncated') else '') +
                    '\n\n' + packet.get('note', ''))
        elif kind == 'document_review_result':
            if self.role in ('admin', 'manager') and self.review_dialog and self.review_dialog.isVisible():
                QMessageBox.information(self.review_dialog, 'Document review saved',
                    packet.get('source', '') + ' — ' + packet.get('status', ''))
                self.review_dialog.refresh()
        elif kind == 'learning_feedback_submitted':
            self.busy = False
            self._append(self.assistant_name, packet.get('message', 'Correction queued.'))
            self.set_ready(True)
        elif kind == 'learning_feedback_reviewed':
            self.busy = False
            self._append(self.assistant_name, 'Correction ' + str(packet.get('status', 'reviewed')) + '.')
            self.set_ready(True)
        elif kind == 'learning_feedback_pending':
            self.busy = False
            items = packet.get('items', [])
            self.set_ready(True)
            if not items:
                QMessageBox.information(self, 'Learning review', 'No pending corrections.')
            else:
                labels = [str(item['id'])[:8] + ' — ' + str(item['question'])[:75] for item in items]
                selected, ok = QInputDialog.getItem(self, 'Review proposed knowledge', 'Select correction:', labels, 0, False)
                if ok:
                    item = items[labels.index(selected)]
                    choice = QMessageBox.question(self, 'Approve knowledge correction',
                        'Question: ' + str(item['question']) + '\n\nProposed answer: ' + str(item['answer']) +
                        '\n\nApprove this as verified company knowledge?',
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel)
                    if choice != QMessageBox.StandardButton.Cancel:
                        self.send({'type': 'learning_feedback_review', 'id': item['id'],
                                   'approve': choice == QMessageBox.StandardButton.Yes})
        elif kind == 'knowledge_status':
            self._append(self.assistant_name, 'Knowledge sync: ' + packet.get('status', '') + ' — ' + str(packet.get('files', 0)) + ' TXT/MD documents. ' + str(packet.get('error', '')))
        elif kind == 'chat_deleted':
            self._update_threads(packet.get('threads', []))
            if self.current_thread == packet.get('thread_id'):
                self.send({'type': 'new_chat'})
        elif kind == 'chats':
            self._update_threads(packet.get('threads', []))
        elif kind == 'db_disconnected':
            self._append(self.assistant_name, 'Live database disconnected.')
        elif kind == 'model_status':
            self._append(self.assistant_name, 'Model status: ' + str({k: v for k, v in packet.items() if k != 'type'}))
        elif kind == 'error':
            self.busy = False
            self.status.setText('● Connected' if self.connected else '● Disconnected')
            self._append('ORION error', packet.get('message', 'Unknown server error'))
            self.set_ready(True)
            if not self.auth_token and self.connected:
                QTimer.singleShot(300, self.prompt_login)
        elif kind == 'logged_out':
            self.auth_token = None
            self.demo_session = False
            self.set_ready(False)

    def open_settings(self):
        menu = QMenu(self)
        appearance = menu.addAction('Appearance')
        sync = menu.addAction('Knowledge status')
        model = menu.addAction('Model status')
        voice_help = menu.addAction('Speech-to-text setup') if self.voice_options['enabled'] else None
        suggest = menu.addAction('Suggest knowledge correction')
        review = menu.addAction('Review learning suggestions') if self.role == 'admin' else None
        document_review = menu.addAction('Review document permissions') if self.role in ('admin', 'manager') else None
        disconnect = menu.addAction('Disconnect live database')
        demo_mode = (self.cfg.get().get('security', {}).get('authentication', {})
                     .get('mode', 'required') == 'local_demo')
        login = None if demo_mode else menu.addAction('Employee sign in')
        selected = menu.exec(self.gear.mapToGlobal(self.gear.rect().bottomLeft()))
        if selected == appearance:
            name, ok = QInputDialog.getItem(self, 'Appearance', 'Color theme:', list(THEMES),
                                             list(THEMES).index(self.cfg.get()['ui'].get('theme', 'Midnight')), False)
            if ok:
                self.cfg.update_ui(theme=name)
                self.apply_theme(name)
        elif selected == sync:
            self.send({'type': 'knowledge_status'})
        elif selected == model:
            self.send({'type': 'model_status'})
        elif voice_help is not None and selected == voice_help:
            QMessageBox.information(self, 'Offline voice recognition',
                'Click 🎙 Speak, talk, then click ■ Stop. Speech is converted to text locally.\n'
                'Install requirements-voice.txt and download the offline voice model first.\n'
                'Set voice.auto_send to false in YAML to review text before sending.')
        elif selected == suggest:
            question, ok = QInputDialog.getMultiLineText(self, 'Suggest knowledge correction',
                                                          'Question or topic:')
            if ok and question.strip():
                answer, ok2 = QInputDialog.getMultiLineText(self, 'Suggest knowledge correction',
                                                             'Proposed verified answer (administrator approval required):')
                if ok2 and answer.strip():
                    self.send({'type': 'learning_feedback_submit', 'question': question.strip(),
                               'answer': answer.strip()})
        elif review is not None and selected == review:
            self.send({'type': 'learning_feedback_pending'})
        elif document_review is not None and selected == document_review:
            self.send({'type': 'document_review_queue', 'status': 'all', 'page_size': 100})
        elif selected == disconnect:
            self.send({'type': 'db_disconnect'})
        elif login is not None and selected == login:
            self.prompt_login()

    def closeEvent(self, event):
        if self.cfg.get()['ui'].get('minimize_to_tray', False) and not self._closing:
            self.hide()
            event.ignore()
        else:
            self.force_exit()
            event.accept()

    def force_exit(self):
        self._closing = True
        self.voice_timer.stop()
        if self.voice_recorder is not None:
            self.voice_recorder.discard()
            self.voice_recorder = None
        if self.voice_worker is not None and self.voice_worker.isRunning():
            # Do not destroy a running QThread on application exit.
            self.voice_worker.wait(10000)
        self.retry_timer.stop()
        self.elapsed_timer.stop()
        if self.listener:
            self.listener.stop()
        if self.sock:
            self.sock.close()
        QApplication.quit()


def main(config=None):
    app = QApplication(sys.argv)
    window = Desktop(config)
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
