"""v2.5 offline microphone tests; no real microphone or model required."""
import sys
import types

import numpy as np
import pytest

from orion_db import voice


def settings(**voice_settings):
    return {'features': {'voice': True}, 'voice': {'enabled': True, **voice_settings}}


def test_voice_settings_enabled_and_bounded():
    assert voice.options(settings())['auto_send'] is True
    assert voice.options(settings(max_record_seconds=999))['max_record_seconds'] == 120
    assert voice.options(settings(max_record_seconds=1))['max_record_seconds'] == 2
    assert not voice.options({'features': {'voice': False}, 'voice': {'enabled': True}})['enabled']
    with pytest.raises(ValueError):
        voice.options(settings(sample_rate=1))


def test_capture_in_ram_and_stop(monkeypatch):
    fake = types.ModuleType('sounddevice')
    state = {'stopped': False, 'closed': False}

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs['callback']
        def start(self):
            self.callback(np.ones((16000, 1), dtype=np.float32), 16000, None, None)
        def stop(self):
            state['stopped'] = True
        def close(self):
            state['closed'] = True

    fake.InputStream = FakeStream
    monkeypatch.setitem(sys.modules, 'sounddevice', fake)
    recorder = voice.MicrophoneRecorder(settings())
    recorder.start()
    assert recorder.recording
    audio = recorder.stop()
    assert len(audio) == 16000
    assert np.all(audio == 1.0)
    assert state == {'stopped': True, 'closed': True}
    assert not recorder.recording
    assert recorder._chunks == []


def test_capture_limits_max_seconds(monkeypatch):
    fake = types.ModuleType('sounddevice')
    class FakeStream:
        def __init__(self, **kwargs): self.callback = kwargs['callback']
        def start(self):
            for _ in range(5):
                self.callback(np.ones((16000, 1), dtype=np.float32), 16000, None, None)
        def stop(self): pass
        def close(self): pass
    fake.InputStream = FakeStream
    monkeypatch.setitem(sys.modules, 'sounddevice', fake)
    rec = voice.MicrophoneRecorder(settings(max_record_seconds=2))
    rec.start()
    assert len(rec.stop()) == 32000


def test_transcribe_offline_and_uses_separate_model(monkeypatch, tmp_path):
    model_dir = tmp_path / 'faster-whisper-base'
    model_dir.mkdir()
    (model_dir / 'model.bin').write_bytes(b'model-test-stub')
    monkeypatch.setattr(voice, 'resolve_path', lambda path: model_dir)
    fake = types.ModuleType('faster_whisper')
    calls = {}
    class FakeWhisper:
        def __init__(self, path, **kw):
            calls['path'] = path
            calls.update(kw)
        def transcribe(self, audio, **kw):
            calls['audio_len'] = len(audio)
            calls.update(kw)
            return iter([types.SimpleNamespace(text=' What is '),
                         types.SimpleNamespace(text=' a database? ')]), None
    fake.WhisperModel = FakeWhisper
    monkeypatch.setitem(sys.modules, 'faster_whisper', fake)
    result = voice.transcribe(np.zeros(16000, dtype=np.float32), settings())
    assert result == 'What is a database?'
    assert calls['local_files_only'] is True
    assert calls['device'] == 'cpu'
    assert calls['language'] is None
    assert calls['vad_filter'] is True


def test_missing_model_never_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(voice, 'resolve_path', lambda path: tmp_path / 'missing')
    with pytest.raises(RuntimeError, match='Offline speech model not found'):
        voice.transcribe(np.zeros(16000, dtype=np.float32), settings())


def test_desktop_routes_voice_transcript_through_normal_chat():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'orion_db' / 'desktop.py').read_text(encoding='utf-8')
    assert 'self.input.setText(transcript)' in source
    voice_handler = source.split('    def _voice_recognized(self, transcript):', 1)[1].split('    def _voice_failed', 1)[0]
    assert 'self.send_message()' not in voice_handler
    assert 'self.send_btn.clicked.connect(self.send_message)' in source
    assert "self.send({'type': 'chat', 'thread_id': self.current_thread, 'message': question})" in source
