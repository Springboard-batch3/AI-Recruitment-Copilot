"""Isolated Local Speech Processing Adapters (Phase 4D.3).

Provides local Piper TTS for candidate question speech playback and local whisper.cpp
for ephemeral candidate voice response transcription.
Enforces zero cloud leakage, strict subprocess argument isolation (shell=False),
ephemeral audio cleanup in finally blocks, and zero storage of raw candidate audio.
"""
import hashlib
import io
import logging
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
from typing import Optional, Tuple
import uuid
import wave

from app.config import get_settings

logger = logging.getLogger("ai_hiring.services.speech")


def _generate_mock_wav(duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generates valid 16kHz mono 16-bit PCM WAV audio in memory."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        num_frames = int(duration_s * sample_rate)
        for i in range(num_frames):
            t = float(i) / sample_rate
            value = int(32767.0 * 0.1 * math.sin(2.0 * math.pi * 440.0 * t) * (1.0 - t / duration_s))
            wav_file.writeframes(struct.pack("<h", value))
    return buffer.getvalue()


class SpeechRuntimeCapabilityService:
    """Evaluates local speech runtime binary and model readiness safely."""

    @staticmethod
    def is_stt_available() -> bool:
        settings = get_settings()
        if not settings.whisper_cpp_bin or not settings.whisper_cpp_model:
            return False
        bin_path = Path(settings.whisper_cpp_bin)
        model_path = Path(settings.whisper_cpp_model)
        return (
            bin_path.is_file()
            and os.access(bin_path, os.X_OK)
            and model_path.is_file()
            and model_path.stat().st_size > 0
        )

    @staticmethod
    def is_tts_available() -> bool:
        settings = get_settings()
        if settings.piper_bin and settings.piper_model:
            bin_path = Path(settings.piper_bin)
            model_path = Path(settings.piper_model)
            if (
                bin_path.is_file()
                and os.access(bin_path, os.X_OK)
                and model_path.is_file()
                and model_path.stat().st_size > 0
            ):
                return True
        # On macOS, native 'say' is available out-of-the-box for speech synthesis
        say_bin = shutil.which("say")
        if say_bin:
            return True
        if getattr(settings, "audio_mock_mode", False):
            return True
        return False

    @staticmethod
    def is_ffmpeg_available() -> bool:
        settings = get_settings()
        if not settings.ffmpeg_bin:
            which_ffmpeg = shutil.which("ffmpeg")
            return which_ffmpeg is not None
        bin_path = Path(settings.ffmpeg_bin)
        return bin_path.is_file() and os.access(bin_path, os.X_OK)

    @classmethod
    def get_configured_model_label(cls) -> Optional[str]:
        """Extracts a sanitized model label (e.g., 'small.en', 'base.en') without exposing file paths."""
        settings = get_settings()
        if not settings.whisper_cpp_model:
            return None
        model_name = Path(settings.whisper_cpp_model).name.lower()
        if "small.en" in model_name or "small" in model_name:
            return "small.en"
        elif "base.en" in model_name or "base" in model_name:
            return "base.en"
        elif "tiny.en" in model_name or "tiny" in model_name:
            return "tiny.en"
        elif "medium.en" in model_name or "medium" in model_name:
            return "medium.en"
        elif "large" in model_name:
            return "large"
        # Sanitize filename: remove ggml- and .bin extensions
        clean_label = re.sub(r"^(ggml-)?", "", model_name)
        clean_label = re.sub(r"\.(bin|pt|onnx)$", "", clean_label)
        return clean_label or "custom"

    @classmethod
    def get_safe_capabilities(cls) -> dict:
        """Returns candidate-safe capability metadata without exposing paths or stack traces."""
        stt_ok = cls.is_stt_available()
        tts_ok = cls.is_tts_available()
        settings = get_settings()

        safe_reason: Optional[str] = None
        if not stt_ok or not tts_ok:
            missing = []
            if not stt_ok:
                missing.append("speech transcription")
            if not tts_ok:
                missing.append("question speech audio")
            safe_reason = f"Audio interview service is currently unavailable ({', '.join(missing)})."

        return {
            "speech_to_text_available": stt_ok,
            "text_to_speech_available": tts_ok,
            "microphone_required": True,
            "secure_context_required": True,
            "max_recording_seconds": settings.audio_max_seconds,
            "max_upload_bytes": settings.audio_max_upload_bytes,
            "configured_model_label": None,
            "model_recommendation_warning": None,
            "safe_unavailable_reason": safe_reason,
            "unavailable_reason": safe_reason,
        }


class TextToSpeechAdapter:
    """Piper TTS adapter for candidate-safe question speech synthesis."""

    @staticmethod
    def _compute_cache_key(question_text: str, question_id: str, set_version: int, model_identifier: str) -> str:
        clean_text = question_text.strip()
        text_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()[:16]
        raw_key = f"{model_identifier}:{set_version}:{question_id}:{text_hash}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def synthesize_question_speech(
        cls,
        question_snapshot_id: Optional[str] = None,
        question_text: str = "",
        question_id: Optional[str] = None,
        set_version: int = 1,
        **kwargs,
    ) -> bytes:
        """Generates or retrieves cached question audio for playback.

        Receives only candidate-safe question text. Rubrics or hidden content are strictly excluded.
        """
        clean_text = (question_text or kwargs.get("text") or "").strip()
        qid = question_snapshot_id or question_id or kwargs.get("snapshot_id") or "default"
        settings = get_settings()
        if not SpeechRuntimeCapabilityService.is_tts_available():
            raise RuntimeError("Piper TTS binary or voice model is not configured or available.")

        cache_dir = settings.get_audio_tts_cache_path()
        has_real_piper = (
            settings.piper_bin
            and settings.piper_model
            and Path(settings.piper_bin).is_file()
            and Path(settings.piper_model).is_file()
        )

        if not has_real_piper:
            # On macOS, use the built-in 'say' tool to synthesize realistic spoken speech for the actual question text
            say_bin = shutil.which("say")
            if say_bin and clean_text:
                cache_key = cls._compute_cache_key(clean_text, qid, set_version, "macos_say")
                cached_file = cache_dir / f"q_{cache_key}.wav"
                if cached_file.is_file() and cached_file.stat().st_size > 0:
                    return cached_file.read_bytes()
                
                temp_wav = cache_dir / f"tmp_say_{uuid.uuid4().hex}.wav"
                try:
                    res = subprocess.run(
                        [say_bin, "-o", str(temp_wav), "--data-format=LEI16@16000", clean_text],
                        capture_output=True,
                        shell=False,
                        timeout=settings.audio_processing_timeout_seconds,
                        check=False,
                    )
                    if res.returncode == 0 and temp_wav.is_file() and temp_wav.stat().st_size > 0:
                        os.replace(temp_wav, cached_file)
                        return cached_file.read_bytes()
                except Exception as e:
                    logger.warning(f"macOS say TTS failed: {e}")
                finally:
                    if temp_wav.exists():
                        try:
                            temp_wav.unlink(missing_ok=True)
                        except Exception:
                            pass

            cache_key = cls._compute_cache_key(clean_text, qid, set_version, "mock_piper")
            cached_file = cache_dir / f"q_{cache_key}.wav"
            if cached_file.is_file() and cached_file.stat().st_size > 0:
                return cached_file.read_bytes()
            wav_bytes = _generate_mock_wav(duration_s=1.5)
            try:
                cached_file.write_bytes(wav_bytes)
            except Exception:
                pass
            return wav_bytes

        model_name = Path(settings.piper_model).name
        cache_key = cls._compute_cache_key(clean_text, qid, set_version, model_name)
        cached_file = cache_dir / f"q_{cache_key}.wav"

        if cached_file.is_file() and cached_file.stat().st_size > 0:
            return cached_file.read_bytes()

        # Synthesize via Piper subprocess
        piper_bin = settings.piper_bin
        piper_model = settings.piper_model
        temp_wav = cache_dir / f"tmp_{uuid.uuid4().hex}.wav"

        cmd = [
            piper_bin,
            "--model",
            piper_model,
            "--output_file",
            str(temp_wav),
        ]

        try:
            res = subprocess.run(
                cmd,
                input=question_text.strip().encode("utf-8"),
                capture_output=True,
                shell=False,
                timeout=settings.audio_processing_timeout_seconds,
                check=False,
            )
            if res.returncode != 0:
                err_msg = res.stderr.decode("utf-8", errors="replace")[:200]
                logger.error(f"Piper TTS synthesis failed with exit code {res.returncode}: {err_msg}")
                raise RuntimeError("Failed to synthesize question audio.")

            if not temp_wav.is_file() or temp_wav.stat().st_size == 0:
                raise RuntimeError("Piper TTS did not produce audio output.")

            # Atomically move to cached filename
            os.replace(temp_wav, cached_file)
            return cached_file.read_bytes()
        finally:
            if temp_wav.exists():
                try:
                    temp_wav.unlink(missing_ok=True)
                except Exception:
                    pass


def _inspect_wav_properties(wav_bytes: bytes) -> Tuple[float, int, int, int, float]:
    """Inspects PCM WAV headers and computes audio duration (seconds) and RMS energy.
    
    Returns: (duration_seconds, sample_rate, channels, sampwidth, rms_energy)
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            duration = frames / float(rate) if rate > 0 else 0.0
            raw_frames = wf.readframes(frames)

            # Calculate RMS energy for 16-bit PCM audio
            if sampwidth == 2 and len(raw_frames) >= 2:
                count = len(raw_frames) // 2
                shorts = struct.unpack(f"<{count}h", raw_frames)
                sum_sq = sum(s * s for s in shorts)
                rms = math.sqrt(sum_sq / count) if count > 0 else 0.0
            else:
                rms = 0.0

            return duration, rate, channels, sampwidth, rms
    except Exception as e:
        logger.warning(f"Failed to inspect WAV properties: {e}")
        return 0.0, 0, 0, 0, 0.0


class SpeechToTextAdapter:
    """whisper.cpp STT adapter for local candidate voice transcription."""

    @staticmethod
    def _clean_transcript(raw_text: str) -> str:
        # Strip timestamps e.g. [00:00:00.000 --> 00:00:05.000]
        cleaned = re.sub(r"\[\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}\]", "", raw_text)
        # Strip whisper system tokens e.g. (_BEG_), [BLANK_AUDIO]
        cleaned = re.sub(r"\[BLANK_AUDIO\]", "", cleaned, flags=re.IGNORECASE)
        # Normalize whitespace
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @classmethod
    def transcribe_wav(cls, wav_path: Path) -> str:
        """Transcribes a 16kHz mono WAV file locally using whisper.cpp."""
        settings = get_settings()
        if not SpeechRuntimeCapabilityService.is_stt_available():
            raise RuntimeError("whisper.cpp binary or model is not configured or available.")

        has_real_stt = (
            settings.whisper_cpp_bin
            and settings.whisper_cpp_model
            and Path(settings.whisper_cpp_bin).is_file()
            and Path(settings.whisper_cpp_model).is_file()
        )

        if not has_real_stt:
            # When whisper.cpp is not installed, return empty string instead of a fabricated answer
            if getattr(settings, "audio_mock_mode", False):
                return ""
            raise RuntimeError("whisper.cpp binary or model is not installed or configured.")

        whisper_bin = settings.whisper_cpp_bin
        whisper_model = settings.whisper_cpp_model
        output_prefix = wav_path.parent / f"out_{uuid.uuid4().hex}"

        # Ensure no stale output file exists before running
        stale_txt = wav_path.parent / f"{output_prefix.name}.txt"
        if stale_txt.exists():
            stale_txt.unlink(missing_ok=True)

        cmd = [
            whisper_bin,
            "-m",
            whisper_model,
            "-f",
            str(wav_path),
            "-of",
            str(output_prefix),
            "-otxt",
            "-nt",  # No timestamps
            "-l",
            "en",  # English language
        ]

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                shell=False,
                timeout=settings.audio_processing_timeout_seconds,
                check=False,
            )
            if res.returncode != 0:
                err_msg = res.stderr.decode("utf-8", errors="replace")[:200]
                logger.error(f"whisper.cpp transcription failed with exit code {res.returncode}: {err_msg}")
                raise RuntimeError("Speech transcription failed.")

            txt_file = wav_path.parent / f"{output_prefix.name}.txt"
            if not txt_file.is_file():
                raise RuntimeError("whisper.cpp did not produce a transcript output file.")

            raw_content = txt_file.read_text(encoding="utf-8", errors="replace")
            transcript = cls._clean_transcript(raw_content)
            return transcript
        finally:
            txt_file = wav_path.parent / f"{output_prefix.name}.txt"
            if txt_file.exists():
                try:
                    txt_file.unlink(missing_ok=True)
                except Exception:
                    pass


class EphemeralAudioProcessor:
    """Manages secure, isolated temporary audio processing with guaranteed cleanup."""

    @staticmethod
    def _get_ffmpeg_bin() -> str:
        settings = get_settings()
        if settings.ffmpeg_bin and Path(settings.ffmpeg_bin).is_file():
            return settings.ffmpeg_bin
        which_ffmpeg = shutil.which("ffmpeg")
        if which_ffmpeg:
            return which_ffmpeg
        return "ffmpeg"

    @classmethod
    def process_and_transcribe(
        cls,
        audio_bytes: Optional[bytes] = None,
        original_content_type: str = "audio/webm",
        raw_audio_bytes: Optional[bytes] = None,
        content_type: Optional[str] = None,
        **kwargs,
    ) -> Tuple[str, float]:
        """Saves raw audio to a secure ephemeral temp file, converts to 16kHz WAV, validates audio, and transcribes.

        Guarantees all ephemeral temp files are deleted in a finally block.
        Returns: (transcript, duration_seconds)
        """
        payload_bytes = audio_bytes if audio_bytes is not None else (raw_audio_bytes or b"")
        if not payload_bytes or len(payload_bytes) == 0:
            raise ValueError("Audio recording is empty.")

        resolved_content_type = original_content_type or content_type or "audio/webm"
        settings = get_settings()
        temp_dir = settings.get_audio_temp_path()
        run_id = uuid.uuid4().hex

        # Restrictive temp directory for this attempt
        session_temp = temp_dir / f"stt_{run_id}"
        session_temp.mkdir(parents=True, exist_ok=True)
        os.chmod(session_temp, 0o700)

        raw_file = session_temp / f"input_{run_id}.raw"
        wav_file = session_temp / f"converted_{run_id}.wav"

        try:
            # Write with restrictive permissions
            raw_file.write_bytes(payload_bytes)
            os.chmod(raw_file, 0o600)

            ffmpeg_bin = cls._get_ffmpeg_bin()
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                str(raw_file),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(wav_file),
            ]

            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    shell=False,
                    timeout=settings.audio_processing_timeout_seconds,
                    check=False,
                )
                if res.returncode != 0:
                    logger.warning(f"ffmpeg conversion failed: {res.stderr.decode('utf-8', errors='replace')[:200]}")
                    if payload_bytes.startswith(b"RIFF") and b"WAVE" in payload_bytes[:16]:
                        wav_file = raw_file
                    elif getattr(settings, "audio_mock_mode", False):
                        wav_file.write_bytes(_generate_mock_wav())
                    else:
                        raise ValueError("Invalid audio format or audio conversion failed.")
            except FileNotFoundError:
                if payload_bytes.startswith(b"RIFF") and b"WAVE" in payload_bytes[:16]:
                    wav_file = raw_file
                elif getattr(settings, "audio_mock_mode", False):
                    wav_file.write_bytes(_generate_mock_wav())
                else:
                    raise RuntimeError("ffmpeg binary is not configured or available for audio conversion.")

            # Validate converted WAV audio integrity & duration
            if not wav_file.is_file() or wav_file.stat().st_size == 0:
                raise ValueError("Converted audio is empty.")

            wav_data = wav_file.read_bytes()
            duration_s, rate, channels, sampwidth, rms_energy = _inspect_wav_properties(wav_data)

            # Check minimum duration (at least 0.3s)
            if duration_s < 0.3:
                raise ValueError("Recording is too short. Please speak your answer clearly and record again.")

            # Check for meaningful audio energy (avoid completely silent noise-less files unless mock mode)
            if rms_energy < 5.0 and not getattr(settings, "audio_mock_mode", False):
                raise ValueError("No voice detected in the recording. Please check your microphone.")

            # Transcribe via whisper.cpp
            transcript = SpeechToTextAdapter.transcribe_wav(wav_file)

            # Plausibility guard: Maximum expected words rule
            # maximum_expected_words = ceil(duration_seconds * 4) + 5
            word_count = len(transcript.split())
            max_expected_words = math.ceil(duration_s * 4.0) + 5
            if word_count > max_expected_words:
                logger.warning(
                    f"Transcript rejected by duration plausibility guard: "
                    f"duration={duration_s:.2f}s, words={word_count}, max_allowed={max_expected_words}"
                )
                raise ValueError("TRANSCRIPT_DURATION_MISMATCH: Transcript is implausibly long for the recording duration.")

            return transcript, duration_s
        finally:
            # Guaranteed complete cleanup
            try:
                if session_temp.exists():
                    shutil.rmtree(session_temp, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Failed to cleanup ephemeral audio temp dir {session_temp}: {e}")


def cleanup_abandoned_audio_temp_files():
    """Startup cleanup of abandoned temp audio directories strictly inside audio_ephemeral_temp_dir."""
    try:
        settings = get_settings()
        temp_dir = settings.get_audio_temp_path()
        if not temp_dir.exists():
            return
        for item in temp_dir.iterdir():
            if item.is_dir() and item.name.startswith("stt_"):
                shutil.rmtree(item, ignore_errors=True)
            elif item.is_file() and item.name.startswith("tmp_"):
                item.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed during audio temp cleanup: {e}")
