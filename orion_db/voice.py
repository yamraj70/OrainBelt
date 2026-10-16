"""Local microphone speech-to-text. No cloud APIs, no audio persistence.

Optional desktop-only dependencies: sounddevice, numpy and faster-whisper.
The speech recognizer is separate from Qwen3: recognized text is sent through
ORION's existing authenticated chat routing (general -> Qwen3).
"""
from pathlib import Path
from threading import Lock

from .config import resolve_path


DEFAULTS = {
    'enabled': False,
    'model_path': 'models/faster-whisper-base',
    'language': 'auto',
    'sample_rate': 16000,
    'max_record_seconds': 30,
    'auto_send': True,
    'compute_type': 'int8',
    'cpu_threads': 4,
    'beam_size': 3,
}


def options(settings):
    """Read/validate voice settings; invalid values cannot cause huge recordings."""
    opts = {**DEFAULTS, **settings.get('voice', {})}
    opts['enabled'] = bool(settings.get('features', {}).get('voice', False) and opts['enabled'])
    opts['sample_rate'] = int(opts['sample_rate'])
    if opts['sample_rate'] not in (16000, 32000, 44100, 48000):
        raise ValueError('voice.sample_rate must be 16000, 32000, 44100 or 48000')
    opts['max_record_seconds'] = max(2, min(120, int(opts['max_record_seconds'])))
    opts['cpu_threads'] = max(1, min(32, int(opts['cpu_threads'])))
    opts['beam_size'] = max(1, min(10, int(opts['beam_size'])))
    opts['language'] = str(opts['language']).strip() or 'auto'
    opts['auto_send'] = bool(opts['auto_send'])
    return opts


class MicrophoneRecorder:
    """Start/stop short PCM recordings in RAM; the callback never touches Qt."""
    def __init__(self, settings):
        self.settings = options(settings)
        self._stream = None
        self._chunks = []
        self._frames = 0
        self._lock = Lock()
        self._error = None

    @property
    def recording(self):
        return self._stream is not None

    def start(self):
        if self._stream is not None:
            raise RuntimeError('Microphone is already recording')
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError('Microphone support missing. Install requirements-voice.txt') from exc
        self._chunks, self._frames, self._error = [], 0, None
        limit = int(self.settings['max_record_seconds'] * self.settings['sample_rate'])

        def callback(indata, frames, time_info, status):
            # The audio callback must never block on the GUI, filesystem or model.
            with self._lock:
                if status:
                    self._error = str(status)
                if self._frames < limit:
                    keep = min(frames, limit - self._frames)
                    self._chunks.append(indata[:keep, 0].copy())
                    self._frames += keep

        stream = sd.InputStream(samplerate=self.settings['sample_rate'], channels=1,
                                dtype='float32', blocksize=1024, callback=callback)
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self._stream = stream

    def stop(self):
        if self._stream is None:
            raise RuntimeError('Microphone is not recording')
        stream, self._stream = self._stream, None
        try:
            stream.stop()
        finally:
            stream.close()
        with self._lock:
            chunks = self._chunks
            self._chunks = []
            err = self._error
        if not chunks:
            raise RuntimeError('No audio captured. Check microphone access in Windows Settings.')
        if err:
            raise RuntimeError('Microphone capture failed: ' + err)
        import numpy as np
        audio = np.concatenate(chunks)
        if len(audio) < self.settings['sample_rate'] // 4:
            raise RuntimeError('Recording too short; speak for at least one second.')
        return audio

    def discard(self):
        if self._stream is not None:
            stream, self._stream = self._stream, None
            try:
                stream.stop()
            finally:
                stream.close()
        with self._lock:
            self._chunks = []
            self._frames = 0


def transcribe(audio, settings):
    """Transcribe locally from RAM; never downloads a model at runtime."""
    opts = options(settings)
    model_dir = resolve_path(opts['model_path'])
    if not model_dir.is_dir() or not (model_dir / 'model.bin').is_file():
        raise RuntimeError('Offline speech model not found at ' + str(model_dir) +
                           '. Run scripts/DOWNLOAD_VOICE_MODEL.py once or copy a faster-whisper model there.')
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError('Speech recognition missing. Install requirements-voice.txt') from exc
    # CPU int8 works on ordinary Windows PCs. Voice and Qwen3 remain separate models.
    model = WhisperModel(str(model_dir), device='cpu', compute_type=opts['compute_type'],
                         cpu_threads=opts['cpu_threads'], local_files_only=True)
    segments, _ = model.transcribe(audio, language=None if opts['language'].lower() == 'auto' else opts['language'],
                                   beam_size=opts['beam_size'], vad_filter=True, condition_on_previous_text=False)
    text = ' '.join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    if not text:
        raise RuntimeError('No speech recognized. Try speaking more clearly or recording longer.')
    return text[:5000]
