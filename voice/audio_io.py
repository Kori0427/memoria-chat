"""Microphone recording and speaker playback via sounddevice."""

from __future__ import annotations

import queue as _queue_mod
import threading

import numpy as np
import sounddevice as sd


def list_devices() -> None:
    """Print all available audio devices."""
    print(sd.query_devices())


def record(seconds: float, sample_rate: int = 16000, channels: int = 1) -> np.ndarray:
    """Record audio from the default input device (blocking).

    Returns:
        numpy array of shape (frames, channels), dtype float32.
    """
    frames = int(seconds * sample_rate)
    audio = sd.rec(frames, samplerate=sample_rate, channels=channels, dtype="float32")
    sd.wait()
    return audio


def play(audio_data: np.ndarray, sample_rate: int = 16000) -> None:
    """Play audio through the default output device (blocking)."""
    sd.play(audio_data, samplerate=sample_rate)
    sd.wait()


# ---------------------------------------------------------------------------
# Tone generation helpers
# ---------------------------------------------------------------------------

def _generate_tone(
    freq_hz: int,
    duration_ms: int,
    sr: int = 24000,
    amplitude: float = 0.35,
    harmonics: bool = False,
) -> np.ndarray:
    """Generate a single sine tone with 10 ms fade-in/out."""
    n_samples = int(sr * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, n_samples, endpoint=False, dtype=np.float32)
    tone = amplitude * np.sin(2 * np.pi * freq_hz * t)
    if harmonics:
        tone += 0.2 * np.sin(2 * np.pi * freq_hz * 2 * t)
    fade_len = int(sr * 0.01)
    if fade_len > 0 and fade_len * 2 < n_samples:
        tone[:fade_len] *= np.linspace(0, 1, fade_len, dtype=np.float32)
        tone[-fade_len:] *= np.linspace(1, 0, fade_len, dtype=np.float32)
    return tone


# ---------------------------------------------------------------------------
# Callback-driven audio player — the ONLY OutputStream in the application.
# Handles both TTS playback and UI tones (ding, remind, bye) through a
# single stream to avoid WASAPI multi-stream conflicts on Windows. — keeps an OutputStream running at all times,
# outputting silence when idle.  Eliminates the WASAPI output-start underflow
# that clips the first syllable in blocking write() mode.
# ---------------------------------------------------------------------------


class TTSPlayer:
    """Callback-driven audio player for TTS output.

    The stream runs continuously, outputting an inaudible AC dither
    keepalive signal (±1e-4) when idle to prevent WASAPI/DAC endpoint
    suspension.  Each AI response goes through: ``begin_response`` →
    ``enqueue`` × N → ``end_response`` → ``wait_done``.  Playback
    starts after at least 2 chunks or ~200 ms of audio is buffered,
    avoiding first-sentence stutter from network jitter.
    """

    def __init__(self, samplerate: int = 24000, prebuffer_ms: int = 500) -> None:
        self.sr = samplerate
        self._prebuffer_frames = int(samplerate * prebuffer_ms / 1000)
        self._q: _queue_mod.Queue[np.ndarray | None] = _queue_mod.Queue(maxsize=256)
        self._current = np.zeros(0, dtype=np.float32)
        self._pos = 0
        self._armed = False
        self._interrupted = False
        self._queued_frames = 0
        self._queued_chunks = 0
        self._done = threading.Event()
        self._done.set()  # not waiting initially

        # AC dither keepalive: ±1e-4 (~-80 dB, inaudible) alternating
        # signal.  Pure 0.0 or 1e-10 both quantize to zero in 16/24-bit
        # DACs, causing WASAPI to suspend the audio endpoint.  The
        # alternating polarity prevents APO DC-offset filtering.
        self._keepalive = np.empty(256, dtype=np.float32)
        self._keepalive[0::2] = 1e-4
        self._keepalive[1::2] = -1e-4

        self._stream = sd.OutputStream(
            samplerate=samplerate,
            channels=1,
            dtype="float32",
            blocksize=0,
            latency="high",
            callback=self._callback,
        )
        self._stream.start()

    # -- PortAudio callback (runs on audio thread) -------------------------

    def _callback(self, outdata: np.ndarray, frames: int, _time_info, status) -> None:
        if status:
            print(f"  [audio] callback status: {status}", flush=True)

        out = outdata[:, 0]

        # Fill with AC dither keepalive (prevents WASAPI/DAC suspension)
        ka = self._keepalive
        full, rem = divmod(frames, len(ka))
        for k in range(full):
            out[k * len(ka) : (k + 1) * len(ka)] = ka
        if rem:
            out[full * len(ka) :] = ka[:rem]

        if not self._armed or self._interrupted:
            return

        i = 0
        while i < frames:
            if self._pos >= len(self._current):
                try:
                    chunk = self._q.get_nowait()
                except _queue_mod.Empty:
                    if i > 0:
                        print("  [audio] queue starved mid-frame", flush=True)
                    break  # rest of buffer keeps keepalive dither
                if chunk is None:  # end-of-response sentinel
                    self._armed = False
                    self._done.set()
                    break
                self._current = chunk
                self._pos = 0
            n = min(frames - i, len(self._current) - self._pos)
            out[i : i + n] = self._current[self._pos : self._pos + n]
            i += n
            self._pos += n

    # -- Public API --------------------------------------------------------

    def _drain_queue(self) -> None:
        """Remove all pending chunks from the playback queue."""
        while True:
            try:
                self._q.get_nowait()
            except _queue_mod.Empty:
                break

    def begin_response(self) -> None:
        """Prepare for a new AI response.  Disarms the callback until
        enough audio has been buffered (prebuffer)."""
        self._interrupted = False  # reset interrupt flag from previous barge-in
        self._armed = False  # callback stops reading immediately
        self._drain_queue()
        self._current = np.zeros(0, dtype=np.float32)
        self._pos = 0
        self._queued_frames = 0
        self._queued_chunks = 0
        self._done.clear()

    def enqueue(self, audio_np: np.ndarray) -> None:
        """Add a decoded float32 audio chunk (one sentence) to the queue.

        Arms the callback when enough audio is buffered: at least 2 chunks
        OR total frames exceeding the prebuffer threshold (whichever comes
        first).  This prevents first-sentence stutter when only one short
        chunk is available and the next hasn't arrived from the network yet.
        """
        a = np.asarray(audio_np, dtype=np.float32).reshape(-1)
        self._q.put(a)
        if not self._armed:
            self._queued_frames += len(a)
            self._queued_chunks += 1
            if (self._queued_chunks >= 2
                    and self._queued_frames >= self._prebuffer_frames):
                self._armed = True

    def end_response(self) -> None:
        """Signal that no more audio will be enqueued for this response."""
        self._armed = True  # flush even if prebuffer wasn't reached
        self._q.put(None)   # sentinel for the callback

    def wait_done(self, timeout: float | None = None) -> None:
        """Block until all enqueued audio has been played."""
        self._done.wait(timeout)

    def play_sync(self, audio_np: np.ndarray, timeout: float = 5.0) -> None:
        """Enqueue audio and block until fully played.  For short sounds."""
        self.begin_response()
        self.enqueue(audio_np)
        self.end_response()
        self.wait_done(timeout)

    def interrupt(self) -> None:
        """Stop playback immediately and discard all queued audio.

        Thread-safe: sets a bool flag (atomic under CPython GIL) that the
        PortAudio callback checks on its next invocation.  Queue draining
        uses ``queue.Queue`` which is inherently thread-safe.
        """
        self._interrupted = True
        self._drain_queue()
        self._done.set()

    def close(self) -> None:
        """Stop and close the stream.  Call on shutdown."""
        self._armed = False
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


_tts_player: TTSPlayer | None = None


def get_tts_player(sr: int = 24000) -> TTSPlayer:
    """Return the module-level TTSPlayer, creating it on first call."""
    global _tts_player
    if _tts_player is not None:
        return _tts_player
    _tts_player = TTSPlayer(samplerate=sr)
    return _tts_player


def close_tts_player() -> None:
    """Close the TTSPlayer.  Call on shutdown."""
    global _tts_player
    if _tts_player is not None:
        _tts_player.close()
        _tts_player = None


def play_tone(freq_hz: int = 880, duration_ms: int = 300) -> None:
    """Play a confirmation beep with octave harmonic through the TTSPlayer."""
    player = get_tts_player()
    tone = _generate_tone(freq_hz, duration_ms, sr=player.sr, amplitude=0.45, harmonics=True)
    player.play_sync(tone)


def stream_record_with_vad(
    vad,
    sample_rate: int = 16000,
    silence_ms: int = 800,
    max_seconds: float = 60,
    start_timeout: float = 0,
) -> np.ndarray:
    """Record from mic using VAD to detect end-of-speech.

    Args:
        vad: A SileroVAD instance (must have .process_chunk and .threshold).
        sample_rate: Sample rate in Hz (must be 16000 for Silero VAD).
        silence_ms: Milliseconds of silence after speech to stop recording.
        max_seconds: Hard cap on recording length.
        start_timeout: Max seconds to wait for speech to begin (0 = unlimited).
                       Returns empty array if no speech detected within timeout.

    Returns:
        1-D float32 numpy array of the full recording.
    """
    if sample_rate != 16000:
        raise ValueError(
            f"Silero VAD requires 16000 Hz, got {sample_rate}. "
            "Do not override sample_rate when using VAD."
        )
    chunk_size = 512  # Silero VAD V5 expects 512 samples per call
    chunk_duration_ms = chunk_size / sample_rate * 1000  # ~32 ms
    silence_frames = int(silence_ms / chunk_duration_ms)
    max_chunks = int(max_seconds * sample_rate / chunk_size)
    start_timeout_chunks = int(start_timeout * sample_rate / chunk_size) if start_timeout > 0 else 0

    chunks: list[np.ndarray] = []
    speech_started = False
    silent_count = 0
    waited_chunks = 0

    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        blocksize=chunk_size,
    ) as stream:
        for _ in range(max_chunks):
            data, _overflowed = stream.read(chunk_size)
            chunk = data[:, 0]  # (chunk_size, 1) → (chunk_size,)
            chunks.append(chunk.copy())

            prob = vad.process_chunk(chunk)

            if prob >= vad.threshold:
                speech_started = True
                silent_count = 0
            elif speech_started:
                silent_count += 1
                if silent_count >= silence_frames:
                    break
            elif start_timeout_chunks > 0:
                waited_chunks += 1
                if waited_chunks >= start_timeout_chunks:
                    return np.array([], dtype=np.float32)

    if not chunks:
        return np.array([], dtype=np.float32)
    return np.concatenate(chunks)


# ---------------------------------------------------------------------------
# Step 3 additions
# ---------------------------------------------------------------------------

def numpy_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Convert a float32 [-1, 1] numpy array to in-memory WAV bytes.

    Returns raw bytes suitable for uploading to Whisper STT.
    """
    import io
    import wave

    # Clip and convert to 16-bit PCM
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def play_wav(file_path: str) -> None:
    """Play a WAV file through the default output device.

    Silently returns if the file does not exist.
    """
    import os
    import wave

    if not os.path.isfile(file_path):
        print(f"Warning: WAV file not found: {file_path}")
        return

    with wave.open(file_path, "rb") as wf:
        sr = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        dtype = np.int16 if wf.getsampwidth() == 2 else np.float32
        audio = np.frombuffer(frames, dtype=dtype).astype(np.float32)
        if dtype == np.int16:
            audio = audio / 32768.0

    sd.play(audio, samplerate=sr)
    sd.wait()


# Pre-defined tone patterns: list of (freq_hz, duration_ms)
REMIND_PATTERN = [(440, 200), (554, 300)]       # gentle ascending — "still there?"
BYE_PATTERN = [(554, 200), (440, 200), (330, 400)]  # descending — "goodnight"


def decode_wav_bytes(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """Decode in-memory WAV bytes to (float32 mono array, sample_rate).

    Used by the TTS pipeline to pre-decode audio before feeding it to a
    persistent OutputStream for gapless playback.
    """
    import io
    import wave

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sr = wf.getframerate()
        n_ch = wf.getnchannels()
        raw = wf.readframes(wf.getnframes())

    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if n_ch > 1:
        audio = audio.reshape(-1, n_ch)[:, 0]  # downmix to mono
    return audio, sr


def play_tone_pattern(
    freqs: list[tuple[int, int]],
    gap_ms: int = 50,
) -> None:
    """Play a sequence of (freq_hz, duration_ms) tones with gaps and fades."""
    player = get_tts_player()
    sr = player.sr
    parts: list[np.ndarray] = []
    for freq_hz, duration_ms in freqs:
        parts.append(_generate_tone(freq_hz, duration_ms, sr=sr))
        if gap_ms > 0:
            parts.append(np.zeros(int(sr * gap_ms / 1000), dtype=np.float32))
    player.play_sync(np.concatenate(parts))
