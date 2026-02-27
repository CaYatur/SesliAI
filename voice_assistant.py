import argparse
import array
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
import zipfile
from pathlib import Path
from typing import Any

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None

try:
    import requests
except ImportError:
    requests = None

try:
    import sounddevice as sd
except ImportError:
    sd = None

try:
    import vosk
except ImportError:
    vosk = None


DEFAULT_MODEL = "llama3.1:latest"
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_VOSK_TR_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip"
DEFAULT_VOSK_EN_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
DEFAULT_PIPER_MODEL_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "tr/tr_TR/dfki/medium/tr_TR-dfki-medium.onnx"
)
DEFAULT_PIPER_CONFIG_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "tr/tr_TR/dfki/medium/tr_TR-dfki-medium.onnx.json"
)


def str_to_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(
        f"Gecersiz bool degeri: {value}. true/false, 1/0, yes/no kullanin."
    )


def require_module(name: str, module: Any, install_hint: str) -> bool:
    if module is not None:
        return True
    print(f"[Hata] Eksik paket: {name}")
    print(f"Kurulum: {install_hint}")
    return False


def download_and_extract(url: str, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        zip_path = temp_path / "model.zip"
        print(f"[STT] Vosk modeli indiriliyor: {url}")
        urllib.request.urlretrieve(url, zip_path)

        print("[STT] Model arsivi aciliyor...")
        with zipfile.ZipFile(zip_path, "r") as zip_file:
            zip_file.extractall(temp_path)

        extracted_dirs = [p for p in temp_path.iterdir() if p.is_dir()]
        if not extracted_dirs:
            raise RuntimeError("Model arsivinden klasor cikmadi.")

        source_dir = extracted_dirs[0]
        target_dir = destination_dir / source_dir.name
        if target_dir.exists():
            return target_dir

        shutil.move(str(source_dir), str(target_dir))
        return target_dir


def ensure_vosk_model(model_path: Path, model_url: str) -> Path:
    if model_path.exists() and any(model_path.iterdir()):
        return model_path

    parent = model_path.parent
    downloaded_path = download_and_extract(model_url, parent)
    if downloaded_path != model_path:
        if model_path.exists():
            shutil.rmtree(model_path)
        downloaded_path.rename(model_path)
    return model_path


def ensure_file(file_path: Path, download_url: str | None) -> Path:
    if file_path.exists():
        return file_path
    if not download_url:
        raise RuntimeError(f"Dosya bulunamadi: {file_path}")

    file_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[TTS] Dosya indiriliyor: {download_url}")
    urllib.request.urlretrieve(download_url, file_path)
    return file_path


def rms_level(raw_chunk: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(raw_chunk)
    if not samples:
        return 0.0

    square_sum = 0.0
    for sample in samples:
        square_sum += float(sample) * float(sample)
    return math.sqrt(square_sum / len(samples))


def record_until_silence(
    sample_rate: int,
    block_size: int,
    silence_threshold: float,
    silence_seconds: float,
    max_seconds: float,
    input_device: str | int | None,
) -> bytes:
    audio_queue: queue.Queue[bytes] = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[Mic] {status}", file=sys.stderr)
        audio_queue.put(bytes(indata))

    chunk_duration = block_size / sample_rate
    silence_chunks_needed = max(1, int(silence_seconds / chunk_duration))
    max_chunks = max(1, int(max_seconds / chunk_duration))

    chunks: list[bytes] = []
    speaking_started = False
    silent_streak = 0

    with sd.RawInputStream(
        samplerate=sample_rate,
        blocksize=block_size,
        dtype="int16",
        channels=1,
        device=input_device,
        callback=callback,
    ):
        print("Sizi dinliyorum... (cikmak icin 'cikis' veya Ctrl+C)")
        start = time.time()
        for _ in range(max_chunks):
            chunk = audio_queue.get()
            chunks.append(chunk)
            level = rms_level(chunk)

            if level >= silence_threshold:
                speaking_started = True
                silent_streak = 0
            elif speaking_started:
                silent_streak += 1
                if silent_streak >= silence_chunks_needed:
                    break

            if time.time() - start >= max_seconds:
                break

    if not speaking_started:
        return b""
    return b"".join(chunks)


def transcribe_audio_with_vosk(
    audio_data: bytes, model: Any, sample_rate: int
) -> tuple[str, float]:
    recognizer = vosk.KaldiRecognizer(model, sample_rate)
    recognizer.SetWords(True)

    chunk_size = 4000
    for i in range(0, len(audio_data), chunk_size):
        recognizer.AcceptWaveform(audio_data[i : i + chunk_size])

    result = json.loads(recognizer.FinalResult())
    text = result.get("text", "").strip()
    words = result.get("result", [])
    if not words:
        return text, 0.0

    confidences = [
        float(item.get("conf", 0.0))
        for item in words
        if isinstance(item.get("conf"), (int, float))
    ]
    if not confidences:
        return text, 0.0
    return text, sum(confidences) / len(confidences)


def choose_best_candidate(candidates: list[dict[str, Any]], prefer_language: str) -> dict[str, Any]:
    usable = [c for c in candidates if c.get("text")]
    if not usable:
        return {"language": None, "text": "", "confidence": 0.0}

    def score(item: dict[str, Any]) -> float:
        text = item["text"]
        confidence = float(item["confidence"])
        bonus_len = min(len(text), 80) / 400.0
        bonus_lang = 0.03 if item["language"] == prefer_language else 0.0
        return confidence + bonus_len + bonus_lang

    return max(usable, key=score)


def transcribe_audio(
    audio_data: bytes,
    sample_rate: int,
    stt_models: dict[str, Any],
    stt_language: str,
    prefer_language: str,
    debug_stt: bool,
) -> tuple[str, float, str | None]:
    candidates: list[dict[str, Any]] = []

    if stt_language in {"tr", "auto"} and "tr" in stt_models:
        text, confidence = transcribe_audio_with_vosk(audio_data, stt_models["tr"], sample_rate)
        candidates.append({"language": "tr", "text": text, "confidence": confidence})

    if stt_language in {"en", "auto"} and "en" in stt_models:
        text, confidence = transcribe_audio_with_vosk(audio_data, stt_models["en"], sample_rate)
        candidates.append({"language": "en", "text": text, "confidence": confidence})

    best = choose_best_candidate(candidates, prefer_language)

    if debug_stt and candidates:
        debug_parts = []
        for candidate in candidates:
            c_text = candidate["text"] if candidate["text"] else "<bos>"
            c_conf = round(float(candidate["confidence"]), 2)
            debug_parts.append(f'{candidate["language"]}: "{c_text}" ({c_conf})')
        selected = best["language"] if best["language"] else "none"
        print("[STT-DEBUG] " + " | ".join(debug_parts) + f" -> secilen={selected}")

    return best["text"], float(best["confidence"]), best["language"]


def list_pyttsx3_voices() -> list[dict[str, str]]:
    if pyttsx3 is None:
        return []

    engine = pyttsx3.init()
    voices = engine.getProperty("voices")
    listed: list[dict[str, str]] = []
    for voice in voices:
        listed.append(
            {
                "id": str(getattr(voice, "id", "")),
                "name": str(getattr(voice, "name", "")),
                "languages": str(getattr(voice, "languages", "")),
            }
        )
    return listed


def list_powershell_voices() -> list[dict[str, str]]:
    command = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
    )
    process = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        error_msg = process.stderr.strip() or process.stdout.strip() or "Bilinmeyen hata"
        raise RuntimeError(error_msg)

    voices: list[dict[str, str]] = []
    for line in process.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        voices.append({"id": name, "name": name, "languages": ""})
    return voices


def pick_tts_voice_id(voices: list[dict[str, str]], requested_voice: str, prefer_language: str) -> str | None:
    if not voices:
        return None

    if requested_voice and requested_voice.lower() != "auto":
        target = requested_voice.lower()
        for voice in voices:
            blob = f'{voice["id"]} {voice["name"]} {voice["languages"]}'.lower()
            if target in blob:
                return voice["id"]

    pref_tokens = ["tr", "turkish", "turkce"] if prefer_language == "tr" else ["en", "english"]
    for voice in voices:
        blob = f'{voice["id"]} {voice["name"]} {voice["languages"]}'.lower()
        if any(token in blob for token in pref_tokens):
            return voice["id"]

    return voices[0]["id"]


def normalize_powershell_rate(tts_rate: int) -> int:
    # pyttsx3 tarzi ~180 normal hiz kabul edilir, SAPI -10..10 bekler.
    mapped = int((tts_rate - 180) / 20)
    return max(-10, min(10, mapped))


def resolve_piper_executable(path_or_name: str) -> str:
    candidate = Path(path_or_name)
    if candidate.exists():
        return str(candidate.resolve())

    resolved = shutil.which(path_or_name)
    if resolved:
        return resolved

    raise RuntimeError(
        "Piper calistirilamadi. `piper` kurun veya --piper-exe ile yol verin."
    )


def normalize_piper_rate(tts_rate: int) -> float:
    # Piper'da daha dusuk length_scale daha hizli, yuksek daha yavas.
    # 180 normal kabul edilip 0.8..1.4 araligina map edilir.
    delta = (tts_rate - 180) / 100.0
    scale = 1.0 - (delta * 0.4)
    return max(0.8, min(1.4, scale))


def play_wav_file(wav_path: Path) -> None:
    dtype_by_width = {1: "uint8", 2: "int16", 3: "int24", 4: "int32"}
    with wave.open(str(wav_path), "rb") as wav_file:
        sample_width = wav_file.getsampwidth()
        channels = wav_file.getnchannels()
        frame_rate = wav_file.getframerate()
        dtype = dtype_by_width.get(sample_width)
        if dtype is None:
            raise RuntimeError(f"Desteklenmeyen PCM sample width: {sample_width}")

        with sd.RawOutputStream(
            samplerate=frame_rate,
            channels=channels,
            dtype=dtype,
        ) as stream:
            while True:
                chunk = wav_file.readframes(4096)
                if not chunk:
                    break
                stream.write(chunk)


def create_tts_engine(
    backend: str,
    rate: int,
    volume: float,
    requested_voice: str,
    prefer_language: str,
    piper_exe: str,
    piper_model_path: str,
    piper_model_url: str | None,
    piper_config_path: str,
    piper_config_url: str | None,
    piper_noise_scale: float,
    piper_noise_w: float,
) -> Any:
    if backend == "piper":
        exe = resolve_piper_executable(piper_exe)
        model_path = ensure_file(Path(piper_model_path), piper_model_url)
        config_path = ensure_file(Path(piper_config_path), piper_config_url)
        return {
            "backend": "piper",
            "exe": exe,
            "model_path": str(model_path.resolve()),
            "config_path": str(config_path.resolve()),
            "length_scale": normalize_piper_rate(rate),
            "noise_scale": max(0.0, float(piper_noise_scale)),
            "noise_w": max(0.0, float(piper_noise_w)),
        }

    if backend == "powershell":
        voices = list_powershell_voices()
        voice_id = pick_tts_voice_id(voices, requested_voice, prefer_language)
        return {
            "backend": "powershell",
            "voice_id": voice_id,
            "rate": normalize_powershell_rate(rate),
        }

    engine = pyttsx3.init()
    engine.setProperty("rate", rate)
    engine.setProperty("volume", max(0.0, min(1.0, volume)))

    voices = list_pyttsx3_voices()
    voice_id = pick_tts_voice_id(voices, requested_voice, prefer_language)
    if voice_id:
        engine.setProperty("voice", voice_id)

    return {"backend": "pyttsx3", "engine": engine}


def initialize_tts(args: argparse.Namespace) -> tuple[Any | None, str]:
    if args.no_tts:
        return None, "none"

    if args.tts_backend == "piper" and sd is None:
        print("[Uyari] sounddevice bulunamadi. Piper TTS icin gerekli. (pip install sounddevice)")
        if args.tts_fallback_backend == "none":
            return None, "none"

    def try_create_backend(backend_name: str) -> tuple[Any | None, str | None]:
        if backend_name == "none":
            return None, None
        if backend_name == "pyttsx3" and pyttsx3 is None:
            return None, "pyttsx3 bulunamadi. (pip install pyttsx3)"
        if backend_name == "piper" and sd is None:
            return None, "sounddevice bulunamadi. (pip install sounddevice)"
        try:
            engine = create_tts_engine(
                backend_name,
                args.tts_rate,
                args.tts_volume,
                args.tts_voice,
                args.prefer_language,
                args.piper_exe,
                args.piper_model_path,
                args.piper_model_url or None,
                args.piper_config_path,
                args.piper_config_url or None,
                args.piper_noise_scale,
                args.piper_noise_w,
            )
            return engine, None
        except Exception as backend_exc:
            return None, str(backend_exc)

    tts_engine, tts_error = try_create_backend(args.tts_backend)
    if tts_engine is not None:
        return tts_engine, args.tts_backend

    if tts_error:
        print(f"[Uyari] TTS ({args.tts_backend}) baslatilamadi: {tts_error}")

    if args.tts_fallback_backend == "none":
        return None, "none"

    fallback_engine, fallback_error = try_create_backend(args.tts_fallback_backend)
    if fallback_engine is not None:
        print(f"[Bilgi] TTS fallback aktif: {args.tts_fallback_backend}")
        return fallback_engine, args.tts_fallback_backend

    if fallback_error:
        print(f"[Uyari] TTS fallback ({args.tts_fallback_backend}) baslatilamadi: {fallback_error}")
    return None, "none"


def speak_with_powershell(text: str, voice_id: str | None, rate: int) -> None:
    escaped_text = text.replace("'", "''")
    script_parts = ["Add-Type -AssemblyName System.Speech", "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer", f"$s.Rate = {rate}"]
    if voice_id:
        escaped_voice = voice_id.replace("'", "''")
        script_parts.append(f"try {{ $s.SelectVoice('{escaped_voice}') }} catch {{ }}")
    script_parts.append(
        "try { "
        f"$s.Speak('{escaped_text}'); "
        "exit 0 "
        "} catch { "
        "try { "
        "$sp = New-Object -ComObject SAPI.SpVoice; "
        f"$null = $sp.Speak('{escaped_text}'); "
        "exit 0 "
        "} catch { "
        "Write-Error $_.Exception.Message; "
        "exit 1 "
        "} "
        "}"
    )
    command = "; ".join(script_parts)

    process = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        raw_error = process.stderr.strip() or process.stdout.strip() or "Bilinmeyen hata"
        if "0x8004503A" in raw_error:
            error_msg = (
                "Windows TTS kullanilamiyor (0x8004503A). "
                "Ayarlar > Time & language > Speech altinda bir ses paketi kurun ve tekrar deneyin."
            )
        elif "Class not registered" in raw_error or "Sınıf kaydedilmemiş" in raw_error:
            error_msg = (
                "Windows TTS sinifi kayitli degil. PowerShell/SAPI sesleri bu sistemde kullanilamiyor."
            )
        else:
            error_msg = raw_error.splitlines()[-1] if raw_error.splitlines() else raw_error
        raise RuntimeError(error_msg)


def speak_with_piper(
    text: str,
    exe: str,
    model_path: str,
    config_path: str,
    length_scale: float,
    noise_scale: float,
    noise_w: float,
) -> None:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
        wav_path = Path(temp_wav.name)

    command = [
        exe,
        "--model",
        model_path,
        "--config",
        config_path,
        "--output_file",
        str(wav_path),
        "--length_scale",
        f"{length_scale:.3f}",
        "--noise_scale",
        f"{noise_scale:.3f}",
        "--noise_w",
        f"{noise_w:.3f}",
    ]

    try:
        process = subprocess.run(
            command,
            input=text,
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            error_msg = process.stderr.strip() or process.stdout.strip() or "Bilinmeyen hata"
            raise RuntimeError(error_msg)
        play_wav_file(wav_path)
    finally:
        if wav_path.exists():
            wav_path.unlink(missing_ok=True)


def speak(engine: Any, text: str) -> None:
    if engine["backend"] == "piper":
        speak_with_piper(
            text,
            engine["exe"],
            engine["model_path"],
            engine["config_path"],
            float(engine["length_scale"]),
            float(engine["noise_scale"]),
            float(engine["noise_w"]),
        )
        return

    if engine["backend"] == "powershell":
        speak_with_powershell(text, engine.get("voice_id"), int(engine.get("rate", 0)))
        return

    pyttsx_engine = engine["engine"]
    pyttsx_engine.say(text)
    pyttsx_engine.runAndWait()


def looks_like_question(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return stripped.endswith("?") or "?" in stripped


def apply_follow_up(reply: str, chatty: bool, detected_lang: str | None) -> str:
    if not chatty:
        return reply
    if looks_like_question(reply):
        return reply

    follow_up = " Devam etmek ister misin?"
    if detected_lang == "en":
        follow_up = " Would you like to continue?"
    return reply.rstrip() + follow_up


def ollama_tags(base_url: str, timeout: float) -> dict:
    response = requests.get(f"{base_url}/api/tags", timeout=timeout)
    response.raise_for_status()
    return response.json()


def model_exists(tags_payload: dict, model_name: str) -> bool:
    for item in tags_payload.get("models", []):
        name = item.get("name", "")
        if name == model_name or name.startswith(f"{model_name}:"):
            return True
    return False


def chat_with_ollama(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    timeout: float,
) -> str:
    payload = {"model": model, "messages": messages, "stream": False}
    response = requests.post(f"{base_url}/api/chat", json=payload, timeout=timeout)
    response.raise_for_status()
    body = response.json()
    return body["message"]["content"].strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Yerel sesli asistan: STT (Vosk TR+EN) + LLM (Ollama) + TTS"
    )
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--stt-language", choices=["auto", "tr", "en"], default="auto")
    parser.add_argument("--prefer-language", choices=["tr", "en"], default="tr")
    parser.add_argument("--vosk-tr-model-path", default="models/vosk-tr")
    parser.add_argument("--vosk-tr-model-url", default=DEFAULT_VOSK_TR_MODEL_URL)
    parser.add_argument("--vosk-en-model-path", default="models/vosk-en")
    parser.add_argument("--vosk-en-model-url", default=DEFAULT_VOSK_EN_MODEL_URL)
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--block-size", type=int, default=4000)
    parser.add_argument("--silence-threshold", type=float, default=500.0)
    parser.add_argument("--silence-seconds", type=float, default=1.0)
    parser.add_argument("--max-seconds", type=float, default=20.0)
    parser.add_argument("--min-confidence", type=float, default=0.35)
    parser.add_argument("--allow-low-confidence", type=str_to_bool, default=True)
    parser.add_argument("--debug-stt", action="store_true")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--tts-backend",
        choices=["piper", "powershell", "pyttsx3"],
        default="piper",
        help="TTS motoru. piper daha dogal, powershell daha uyumlu.",
    )
    parser.add_argument(
        "--tts-fallback-backend",
        choices=["none", "powershell", "pyttsx3"],
        default="powershell",
        help="Ana TTS acilamazsa otomatik fallback motoru.",
    )
    parser.add_argument("--tts-rate", type=int, default=180)
    parser.add_argument("--tts-volume", type=float, default=1.0)
    parser.add_argument(
        "--tts-voice",
        default="auto",
        help="TTS ses secimi. 'auto' veya id/isim parcasi.",
    )
    parser.add_argument(
        "--piper-exe",
        default="piper",
        help="piper executable yolu veya komut adi.",
    )
    parser.add_argument(
        "--piper-model-path",
        default="models/piper/tr_TR-dfki-medium.onnx",
        help="Piper ONNX model dosya yolu.",
    )
    parser.add_argument(
        "--piper-model-url",
        default=DEFAULT_PIPER_MODEL_URL,
        help="Model yoksa indirilecek URL. Bos vererek otomatik indirmeyi kapat.",
    )
    parser.add_argument(
        "--piper-config-path",
        default="models/piper/tr_TR-dfki-medium.onnx.json",
        help="Piper model config dosya yolu.",
    )
    parser.add_argument(
        "--piper-config-url",
        default=DEFAULT_PIPER_CONFIG_URL,
        help="Config yoksa indirilecek URL. Bos vererek otomatik indirmeyi kapat.",
    )
    parser.add_argument(
        "--piper-noise-scale",
        type=float,
        default=0.667,
        help="Piper ses cesitliligi (genelde 0.5-0.8).",
    )
    parser.add_argument(
        "--piper-noise-w",
        type=float,
        default=0.8,
        help="Piper fonem varyasyonu (genelde 0.7-1.0).",
    )
    parser.add_argument("--input-device", default=None)
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--list-voices", action="store_true")
    parser.add_argument(
        "--test-tts",
        default="",
        help="TTS test mesaji. Doluysa mesaji seslendirip cikar.",
    )
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument(
        "--startup-greeting",
        default="Merhaba, ben hazirim. Ne yapmak istersin?",
        help="Baslangicta asistanin soyleyecegi mesaj. Bos string verirsen kapatilir.",
    )
    parser.add_argument(
        "--chatty",
        type=str_to_bool,
        default=True,
        help="Her cevap sonunda kisa takip sorusu ekle.",
    )
    return parser.parse_args()


def load_stt_models(base_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    models: dict[str, Any] = {}

    if args.stt_language in {"tr", "auto"}:
        tr_model_path = (base_dir / args.vosk_tr_model_path).resolve()
        tr_model_dir = ensure_vosk_model(tr_model_path, args.vosk_tr_model_url)
        models["tr"] = vosk.Model(str(tr_model_dir))

    if args.stt_language in {"en", "auto"}:
        en_model_path = (base_dir / args.vosk_en_model_path).resolve()
        en_model_dir = ensure_vosk_model(en_model_path, args.vosk_en_model_url)
        models["en"] = vosk.Model(str(en_model_dir))

    return models


def main() -> int:
    args = parse_args()

    if args.list_voices:
        try:
            if args.tts_backend == "piper":
                model_path = Path(args.piper_model_path)
                config_path = Path(args.piper_config_path)
                print("Piper backend secili.")
                print(f"Model : {model_path}")
                print(f"Config: {config_path}")
                if not model_path.exists():
                    print("Model dosyasi yok.")
                if not config_path.exists():
                    print("Config dosyasi yok.")
                return 0
            if args.tts_backend == "powershell":
                voices = list_powershell_voices()
            else:
                if pyttsx3 is None:
                    print("[Hata] pyttsx3 bulunamadi. Kurulum: pip install pyttsx3")
                    return 1
                voices = list_pyttsx3_voices()
        except Exception as exc:
            print(f"[Hata] TTS sesleri listelenemedi: {exc}")
            return 0

        if not voices:
            print("[Uyari] Kullanilabilir TTS sesi bulunamadi.")
            return 0

        for idx, voice in enumerate(voices, start=1):
            print(f'{idx}. id="{voice["id"]}" | name="{voice["name"]}" | languages="{voice["languages"]}"')
        return 0

    tts_engine, active_tts_backend = initialize_tts(args)
    if args.test_tts.strip():
        if not tts_engine:
            print("[Hata] TTS aktif degil; test sesi calinamadi.")
            return 1
        try:
            speak(tts_engine, args.test_tts.strip())
            print(f"[Bilgi] TTS testi tamamlandi. Backend: {active_tts_backend}")
            return 0
        except Exception as exc:
            print(f"[Hata] TTS testi basarisiz: {exc}")
            return 1

    requirements_ok = all(
        [
            require_module("requests", requests, "pip install requests"),
            require_module("sounddevice", sd, "pip install sounddevice"),
            require_module("vosk", vosk, "pip install vosk"),
        ]
    )
    if not requirements_ok:
        return 1

    if args.list_devices:
        print(sd.query_devices())
        return 0

    base_dir = Path(__file__).resolve().parent

    try:
        stt_models = load_stt_models(base_dir, args)
    except Exception as exc:
        print(f"[Hata] STT modeli hazirlanamadi: {exc}")
        return 1

    if not stt_models:
        print("[Hata] Aktif STT modeli yuklenemedi.")
        return 1

    try:
        tags = ollama_tags(args.ollama_url, args.timeout)
    except Exception as exc:
        print(f"[Hata] Ollama baglantisi kurulamadi: {exc}")
        print("Ollama'nin calistigindan emin olun: `ollama serve`")
        return 1

    if not model_exists(tags, args.model):
        print(f"[Hata] Model bulunamadi: {args.model}")
        print(f"Once su komutu calistirin: `ollama pull {args.model}`")
        return 1

    system_content = (
        "Sen yerel bir sesli asistansin. Kisa, net cevap ver. "
        "Kullanici hangi dilde konusursa o dilde yanit ver."
    )
    if args.chatty:
        system_content += " Her cevabin sonunda tek bir kisa takip sorusu sor."

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": system_content,
        }
    ]

    active_langs = ",".join(sorted(stt_models.keys()))
    print(f"Asistan hazir. Model: {args.model}")
    print(f"STT dil modu: {args.stt_language} (yuklu: {active_langs}, tercih: {args.prefer_language})")
    if tts_engine:
        print(f"TTS: {active_tts_backend} (voice: {args.tts_voice})")
    else:
        print("TTS: kapali")
    print("Konusmayi bitirmek icin: 'cikis', 'kapat', 'quit' veya 'exit' deyin.")

    if args.startup_greeting.strip():
        greeting = args.startup_greeting.strip()
        print(f"Asistan: {greeting}\n")
        if tts_engine:
            try:
                speak(tts_engine, greeting)
            except Exception as exc:
                print(f"[Hata] TTS calismadi: {exc}")

    while True:
        try:
            audio = record_until_silence(
                sample_rate=args.sample_rate,
                block_size=args.block_size,
                silence_threshold=args.silence_threshold,
                silence_seconds=args.silence_seconds,
                max_seconds=args.max_seconds,
                input_device=args.input_device,
            )
        except KeyboardInterrupt:
            print("\nCikiliyor...")
            return 0
        except Exception as exc:
            print(f"[Hata] Mikrofon okunamadi: {exc}")
            continue

        if not audio:
            print("[STT] Ses algilanmadi, tekrar deneyin.")
            continue

        text, confidence, detected_lang = transcribe_audio(
            audio_data=audio,
            sample_rate=args.sample_rate,
            stt_models=stt_models,
            stt_language=args.stt_language,
            prefer_language=args.prefer_language,
            debug_stt=args.debug_stt,
        )
        if not text:
            print("[STT] Anlasilamadi, tekrar deneyin.")
            continue

        confidence_rounded = round(confidence, 2)
        lang_info = detected_lang if detected_lang else "unknown"
        if confidence < args.min_confidence:
            if not args.allow_low_confidence:
                print(
                    f"[STT] Dusuk guven ({confidence_rounded}, dil={lang_info}). Tekrar deneyin."
                )
                continue
            print(
                f"\nSiz : {text} (guven: {confidence_rounded}, dil: {lang_info}, dusuk ama gonderiliyor)"
            )
        else:
            print(f"\nSiz : {text} (guven: {confidence_rounded}, dil: {lang_info})")

        normalized = text.lower().strip()
        if normalized in {"cikis", "kapat", "quit", "exit"}:
            print("Asistan kapatiliyor.")
            return 0

        messages.append({"role": "user", "content": text})
        try:
            reply = chat_with_ollama(args.ollama_url, args.model, messages, args.timeout)
        except Exception as exc:
            print(f"[Hata] Ollama cevap veremedi: {exc}")
            messages.pop()
            continue

        reply = apply_follow_up(reply, args.chatty, detected_lang)
        messages.append({"role": "assistant", "content": reply})
        print(f"Asistan: {reply}\n")
        if tts_engine:
            try:
                speak(tts_engine, reply)
            except Exception as exc:
                print(f"[Hata] TTS calismadi: {exc}")


if __name__ == "__main__":
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    raise SystemExit(main())
