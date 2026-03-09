#!/usr/bin/env python3
"""
Local STT transcription for Memoria.chat
Supports faster-whisper (preferred) and openai-whisper as fallback.

Usage: python local-stt.py <audio_file> [model_size] [language]
  model_size: tiny, base (default), small, medium, large-v3
  language:   en, zh, ja, ... (optional, auto-detect if omitted)

Output: JSON to stdout  {"text": "transcribed text"}
Errors: JSON to stderr  {"error": "message"}
"""

import sys
import json
import os


def transcribe_faster_whisper(audio_path, model_size, language):
    from faster_whisper import WhisperModel

    device = "cpu"
    compute_type = "int8"

    # Auto-detect CUDA
    try:
        import torch
        if torch.cuda.is_available():
            device = "cuda"
            compute_type = "float16"
    except ImportError:
        pass

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(
        audio_path,
        language=language if language else None,
        vad_filter=True,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


def transcribe_openai_whisper(audio_path, model_size, language):
    import whisper

    model = whisper.load_model(model_size)
    opts = {}
    if language:
        opts["language"] = language
    result = model.transcribe(audio_path, **opts)
    return (result.get("text") or "").strip()


def main():
    if len(sys.argv) < 2:
        json.dump({"error": "Usage: local-stt.py <audio_file> [model] [language]"}, sys.stderr)
        sys.exit(1)

    audio_path = sys.argv[1]
    model_size = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("WHISPER_MODEL", "base")
    language = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("WHISPER_LANGUAGE", "")

    if not os.path.isfile(audio_path):
        json.dump({"error": f"File not found: {audio_path}"}, sys.stderr)
        sys.exit(1)

    # Try faster-whisper first (faster, less memory)
    try:
        text = transcribe_faster_whisper(audio_path, model_size, language)
        json.dump({"text": text}, sys.stdout, ensure_ascii=False)
        return
    except ImportError:
        pass
    except Exception as e:
        json.dump({"error": f"faster-whisper failed: {e}"}, sys.stderr)
        sys.exit(1)

    # Fallback to openai-whisper
    try:
        text = transcribe_openai_whisper(audio_path, model_size, language)
        json.dump({"text": text}, sys.stdout, ensure_ascii=False)
        return
    except ImportError:
        pass
    except Exception as e:
        json.dump({"error": f"openai-whisper failed: {e}"}, sys.stderr)
        sys.exit(1)

    json.dump({
        "error": "No Whisper package found. Install one:\n  pip install faster-whisper\n  pip install openai-whisper"
    }, sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
