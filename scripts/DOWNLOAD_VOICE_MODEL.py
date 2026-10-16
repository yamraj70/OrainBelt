"""Explicit one-time model download. No downloads happen during transcription."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / 'models' / 'faster-whisper-base'


def main():
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print('Install requirements-voice.txt first.', file=sys.stderr)
        return 1
    print('Downloading Systran/faster-whisper-base into:', TARGET)
    print('This requires Internet access once. Review the model license before use.')
    TARGET.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id='Systran/faster-whisper-base', local_dir=str(TARGET))
    if not (TARGET / 'model.bin').exists():
        print('Downloaded model.bin missing.', file=sys.stderr)
        return 1
    print('Ready. Speech recognition is offline from now on.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
