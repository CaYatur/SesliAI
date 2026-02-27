# SesliAI

> 🇹🇷 Türkçe aşağıda · 🇬🇧 English below

## 🇹🇷 Türkçe

**SesliAI**, tamamen yerel çalışan bir sesli asistandır. Mikrofondan konuşursun, konuşma metne çevrilir (Vosk), cevabı bilgisayarındaki **Ollama** modeli üretir ve cevap **Piper** ile sesli okunur. İnternete veya bulut API'sine ihtiyaç duymaz (yalnızca ilk çalıştırmada modelleri indirmek için).

### Özellikler
- **`voice_assistant.py`** (önerilen): Vosk ile Türkçe + İngilizce otomatik konuşma tanıma (`--stt-language auto|tr|en`), güven skoruna göre en iyi sonucu seçme, Ollama sohbeti, Piper nöral TTS (alternatif: `powershell` veya `pyttsx3`).
- **`voice_assistant.ps1`**: Windows `System.Speech` tabanlı alternatif sürüm (ek kurulum gerektirmez).
- **`android_server.py`**: Bilgisayarı HTTP sunucusuna çevirir; telefon tarayıcısından (`android_web/index.html`) konuşursun, cevap PC'deki Ollama + Piper'dan WAV olarak gelir. Oturum geçmişi, özelleştirilebilir sistem promptu.
- **`android-apk/`**: Wear OS / Android için yerel (WebView olmayan) istemci: maskotlu arayüz, tek mikrofon butonu, yuvarlak ekran düzeni, Android SpeechRecognizer ile dinleme.
- Eksik modeller ilk çalıştırmada otomatik indirilir.

### Teknolojiler
Python 3 (requests, sounddevice, vosk, pyttsx3), Ollama, Vosk, Piper TTS, PowerShell, HTML/JS, Android (Java, Gradle, compileSdk 35, minSdk 24).

### Gereksinimler ve modeller
Modeller repoda **yoktur** (boyut nedeniyle). Kod eksik olanları otomatik indirir; elle indirmek istersen:

| Model | Varsayılan yol | Kaynak |
|---|---|---|
| Ollama LLM | — | `ollama pull llama3.1:latest` |
| Vosk Türkçe | `models/vosk-tr` | https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip |
| Vosk İngilizce | `models/vosk-en` | https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip |
| Piper ses (TR) | `models/piper/tr_TR-dfki-medium.onnx` (+ `.onnx.json`) | https://huggingface.co/rhasspy/piper-voices/tree/main/tr/tr_TR/dfki/medium |
| Piper programı | `piper` (PATH) veya `--piper-exe` | https://github.com/rhasspy/piper/releases |

### Kurulum ve çalıştırma
```powershell
ollama serve
ollama pull llama3.1:latest
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python .\voice_assistant.py                  # TR+EN otomatik
python .\voice_assistant.py --list-devices   # mikrofonları listele
```
PowerShell sürümü: `.\voice_assistant.ps1 -Model "llama3.1:latest"`

**Telefon üzerinden (sunucu modu):**
```powershell
python .\android_server.py --host 0.0.0.0 --port 8000 --model "llama3.1:latest" --piper-exe ".\piper\piper.exe"
```
Telefon ve PC aynı Wi-Fi'da olmalı; telefonda `http://BILGISAYAR_IP:8000` aç ve mikrofon izni ver.

**Wear OS / Android uygulaması:** Sunucu adresi `MainActivity.java` içindeki `SERVER_BASE_URL` sabitinde ve `res/xml/network_security_config.xml` içinde (varsayılan bir yerel ağ adresi). Kendi PC IP'n ile değiştirip derle:
```powershell
cd android-apk
.\gradlew.bat assembleDebug
```
(`local.properties` içinde `sdk.dir` ayarlı olmalı.)

### Proje yapısı
```
voice_assistant.py      Masaüstü asistan (Vosk + Ollama + Piper)
voice_assistant.ps1     Windows System.Speech sürümü
android_server.py       Telefon için HTTP sunucusu
android_web/index.html  Tarayıcı istemcisi
android-apk/            Wear OS / Android yerel istemci
requirements.txt
```

### Bilinen sorunlar
- Android uygulamasında sunucu adresi koda gömülü; değiştirmek için yeniden derlemek gerekir.
- Sunucu HTTP (şifresiz) çalışır ve kimlik doğrulama yoktur; yalnızca güvenilir yerel ağda kullan.
- Release'teki APK debug anahtarıyla imzalıdır ve yerel bir ağ adresine bağlanacak şekilde derlenmiştir.

### Proje tarihleri
- Oluşturma: 27 Şubat 2026
- Son güncelleme: 27 Şubat 2026

### Lisans
Lisans belirtilmemiştir.

---

## 🇬🇧 English

**SesliAI** is a fully local voice assistant. You speak into the microphone, speech is transcribed with **Vosk**, a local **Ollama** model generates the answer and **Piper** reads it aloud. No cloud APIs (internet only needed to download models on first run).

### Features
- **`voice_assistant.py`** (recommended): Turkish + English automatic speech recognition (`--stt-language auto|tr|en`) picking the best result by confidence, Ollama chat, Piper neural TTS (alternatives: `powershell`, `pyttsx3`).
- **`voice_assistant.ps1`**: Windows `System.Speech` based alternative, no extra install.
- **`android_server.py`**: turns the PC into an HTTP server; talk from a phone browser (`android_web/index.html`) and receive WAV replies from Ollama + Piper. Session history and custom system prompt.
- **`android-apk/`**: native (non-WebView) Wear OS / Android client with mascot UI, single mic button, round-screen layout, Android SpeechRecognizer.
- Missing models are downloaded automatically on first run.

### Tech
Python 3 (requests, sounddevice, vosk, pyttsx3), Ollama, Vosk, Piper TTS, PowerShell, HTML/JS, Android (Java, Gradle, compileSdk 35, minSdk 24).

### Requirements & models
Models are **not** included in the repo. They are downloaded automatically, or manually:
- Ollama: `ollama pull llama3.1:latest`
- Vosk TR → `models/vosk-tr`: https://alphacephei.com/vosk/models/vosk-model-small-tr-0.3.zip
- Vosk EN → `models/vosk-en`: https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
- Piper voice → `models/piper/tr_TR-dfki-medium.onnx` (+ `.onnx.json`): https://huggingface.co/rhasspy/piper-voices/tree/main/tr/tr_TR/dfki/medium
- Piper binary (on PATH or `--piper-exe`): https://github.com/rhasspy/piper/releases

### Setup & run
```powershell
ollama serve; ollama pull llama3.1:latest
python -m venv .venv; .\.venv\Scripts\activate
pip install -r requirements.txt
python .\voice_assistant.py
```
Phone/server mode: `python .\android_server.py --host 0.0.0.0 --port 8000`, then open `http://PC_IP:8000` on a phone on the same Wi-Fi.
Wear OS app: set `SERVER_BASE_URL` in `MainActivity.java` and the domain in `network_security_config.xml` to your PC's IP, then `cd android-apk; .\gradlew.bat assembleDebug`.

### Known issues
- Server address is hardcoded in the Android app (rebuild to change).
- Server is plain HTTP without authentication; use on a trusted LAN only.
- The release APK is debug-signed and built for a local network address.

### Project dates
- Created: 27 Feb 2026
- Last updated: 27 Feb 2026

### License
No license specified.
