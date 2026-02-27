param(
    [string]$Model = "llama3.1:latest",
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [string]$Language = "tr-TR",
    [double]$MinConfidence = 0.45,
    [object]$AllowLowConfidence = $true,
    [int]$MaxListenSeconds = 20,
    [switch]$NoVoice,
    [switch]$ListRecognizers,
    [switch]$ListVoices
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Convert-ToBooleanValue {
    param(
        [object]$Value,
        [string]$ParamName
    )

    if ($Value -is [bool]) {
        return $Value
    }

    if ($Value -is [int]) {
        if ($Value -in 0, 1) {
            return [bool]$Value
        }
    }

    if ($Value -is [string]) {
        $normalized = $Value.Trim().ToLowerInvariant()
        if ($normalized.StartsWith('$')) {
            $normalized = $normalized.Substring(1)
        }

        switch ($normalized) {
            "true" { return $true }
            "false" { return $false }
            "1" { return $true }
            "0" { return $false }
            "yes" { return $true }
            "no" { return $false }
            "on" { return $true }
            "off" { return $false }
        }
    }

    throw "Parametre '$ParamName' icin gecersiz deger: '$Value'. Gecerli degerler: true/false, 1/0, yes/no."
}

function Test-OllamaModel {
    param(
        [string]$BaseUrl,
        [string]$ModelName
    )

    $tags = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/tags" -TimeoutSec 20
    if (-not $tags.models) {
        return $false
    }

    foreach ($m in $tags.models) {
        if ($m.name -eq $ModelName -or $m.name -like "${ModelName}:*") {
            return $true
        }
    }
    return $false
}

function New-Recognizer {
    param([string]$Lang)

    Add-Type -AssemblyName System.Speech

    $installedRecognizers = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
    $exactInfo = $installedRecognizers |
        Where-Object { $_.Culture.Name -eq $Lang } |
        Select-Object -First 1

    $recognizer = $null
    $activeCulture = "unknown"
    $exactMatch = $false

    if ($exactInfo) {
        try {
            $recognizer = [System.Speech.Recognition.SpeechRecognitionEngine]::new($exactInfo)
            $activeCulture = $exactInfo.Culture.Name
            $exactMatch = $true
        } catch {
            Write-Warning "Istenen recognizer acilamadi ($Lang). Varsayilan engine'e dusulecek."
        }
    }

    if (-not $recognizer) {
        $fallbackInfo = $installedRecognizers | Select-Object -First 1
        if ($fallbackInfo -and (-not $exactInfo -or $fallbackInfo.Culture.Name -ne $Lang)) {
            Write-Warning "Dil icin recognizer bulunamadi ($Lang). Kurulu ilk recognizer denenecek: $($fallbackInfo.Culture.Name)"
        }

        if ($fallbackInfo) {
            try {
                $recognizer = [System.Speech.Recognition.SpeechRecognitionEngine]::new($fallbackInfo)
                $activeCulture = $fallbackInfo.Culture.Name
            } catch {
                Write-Warning "Kurulu recognizer acilamadi. Varsayilan engine'e dusulecek."
            }
        }
    }

    if (-not $recognizer) {
        $recognizer = [System.Speech.Recognition.SpeechRecognitionEngine]::new()
        if ($recognizer.RecognizerInfo -and $recognizer.RecognizerInfo.Culture) {
            $activeCulture = $recognizer.RecognizerInfo.Culture.Name
        } elseif ($exactInfo -and $exactInfo.Culture) {
            $activeCulture = $exactInfo.Culture.Name
        } else {
            $activeCulture = "unknown"
        }
    }

    $recognizer.SetInputToDefaultAudioDevice()
    $grammar = [System.Speech.Recognition.DictationGrammar]::new()
    $recognizer.LoadGrammar($grammar)
    return [PSCustomObject]@{
        Engine        = $recognizer
        ActiveCulture = $activeCulture
        ExactMatch    = $exactMatch
    }
}

function New-Synthesizer {
    param([string]$Lang)

    try {
        Add-Type -AssemblyName System.Speech
        $synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
        $voice = $synth.GetInstalledVoices() |
            Where-Object { $_.VoiceInfo.Culture.Name -eq $Lang } |
            Select-Object -First 1

        if ($voice) {
            $synth.SelectVoice($voice.VoiceInfo.Name)
        }

        return [PSCustomObject]@{
            Type   = "SystemSpeech"
            Engine = $synth
        }
    } catch {
        Write-Warning "System.Speech TTS acilamadi, SAPI fallback denenecek."
    }

    try {
        $sapi = New-Object -ComObject SAPI.SpVoice
        return [PSCustomObject]@{
            Type   = "SAPI"
            Engine = $sapi
        }
    } catch {
        Write-Warning "TTS motoru acilamadi. Sesli yanit kapatiliyor."
        return $null
    }
}

function Invoke-TtsSpeak {
    param(
        $Synth,
        [string]$Text
    )

    if (-not $Synth -or [string]::IsNullOrWhiteSpace($Text)) {
        return
    }

    if ($Synth.Type -eq "SystemSpeech") {
        $Synth.Engine.SpeakAsyncCancelAll() | Out-Null
        $Synth.Engine.Speak($Text)
        return
    }

    if ($Synth.Type -eq "SAPI") {
        $Synth.Engine.Speak($Text) | Out-Null
    }
}

if ($ListRecognizers) {
    Add-Type -AssemblyName System.Speech
    [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() |
        ForEach-Object {
            [PSCustomObject]@{
                Name    = $_.Name
                Culture = $_.Culture.Name
                Id      = $_.Id
            }
        } | Format-Table -AutoSize
    exit 0
}

if ($ListVoices) {
    try {
        Add-Type -AssemblyName System.Speech
        $tmpSynth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
        $voices = $tmpSynth.GetInstalledVoices()
        if ($voices.Count -gt 0) {
            $voices |
                ForEach-Object {
                    [PSCustomObject]@{
                        Name    = $_.VoiceInfo.Name
                        Culture = $_.VoiceInfo.Culture.Name
                        Gender  = $_.VoiceInfo.Gender
                        Engine  = "SystemSpeech"
                    }
                } | Format-Table -AutoSize
            exit 0
        }
    } catch {
        Write-Warning "System.Speech ile voice listesi alinamadi, SAPI denenecek."
    }

    try {
        $sapi = New-Object -ComObject SAPI.SpVoice
        $sapi.GetVoices() |
            ForEach-Object {
                [PSCustomObject]@{
                    Name   = $_.GetDescription()
                    Culture = "Unknown"
                    Gender = "Unknown"
                    Engine = "SAPI"
                }
            } | Format-Table -AutoSize
        exit 0
    } catch {
        Write-Host "[Hata] Voice listesi alinamadi: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}

try {
    $AllowLowConfidence = Convert-ToBooleanValue -Value $AllowLowConfidence -ParamName "AllowLowConfidence"
} catch {
    Write-Host "[Hata] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

try {
    $modelOk = Test-OllamaModel -BaseUrl $OllamaUrl -ModelName $Model
} catch {
    Write-Host "[Hata] Ollama baglantisi kurulamadi: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Ollama acik olmali. Gerekirse: ollama serve" -ForegroundColor Yellow
    exit 1
}

if (-not $modelOk) {
    Write-Host "[Hata] Model bulunamadi: $Model" -ForegroundColor Red
    Write-Host "Once su komutu calistirin: ollama pull $Model" -ForegroundColor Yellow
    exit 1
}

$recognizer = $null
$recognizerMeta = $null
try {
    $recognizerMeta = New-Recognizer -Lang $Language
    $recognizer = $recognizerMeta.Engine
} catch {
    Write-Host "[Hata] Mikrofon acilamadi: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Windows'ta mikrofon iznini acin ve kullanimda olan cihazi serbest birakin." -ForegroundColor Yellow
    exit 1
}
$synth = $null
if (-not $NoVoice) {
    $synth = New-Synthesizer -Lang $Language
}

$messages = @(
    @{
        role    = "system"
        content = "Sen yerel bir sesli asistansin. Turkce, net ve kisa cevap ver."
    }
)

Write-Host "Asistan hazir. Model: $Model" -ForegroundColor Green
Write-Host "STT dili: istenen=$Language, aktif=$($recognizerMeta.ActiveCulture)" -ForegroundColor Green
if (-not $recognizerMeta.ExactMatch) {
    Write-Host "Not: Istenen dilde recognizer yok. Dilerseniz -AllowLowConfidence:\$false ile dusuk guveni filtreleyin." -ForegroundColor DarkYellow
}
if ($AllowLowConfidence) {
    Write-Host "Dusuk guven modu: acik (dusuk confidence metinler de gonderilir)." -ForegroundColor Green
} else {
    Write-Host "Dusuk guven modu: kapali (dusuk confidence metinler reddedilir)." -ForegroundColor Green
}
Write-Host "Konusarak sor. Cikmak icin: cikis / kapat / quit / exit" -ForegroundColor Green

:mainLoop while ($true) {
    Write-Host ""
    Write-Host "Dinliyorum..." -ForegroundColor Cyan
    $result = $recognizer.Recognize([TimeSpan]::FromSeconds($MaxListenSeconds))

    if (-not $result) {
        Write-Host "Ses algilanmadi. Tekrar deneyin." -ForegroundColor DarkYellow
        continue
    }

    $text = $result.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        Write-Host "Anlasilamadi. Tekrar deneyin." -ForegroundColor DarkYellow
        continue
    }

    $confidence = [Math]::Round($result.Confidence, 2)
    if ($result.Confidence -lt $MinConfidence) {
        if (-not $AllowLowConfidence) {
            Write-Host "Dusuk guven skoru ($confidence). Tekrar deneyin." -ForegroundColor DarkYellow
            continue
        }
        Write-Host "Siz : $text (guven: $confidence - dusuk, yine de gonderiliyor)" -ForegroundColor DarkYellow
    } else {
        Write-Host "Siz : $text (guven: $confidence)" -ForegroundColor White
    }

    $normalized = $text.ToLowerInvariant()
    if ($normalized -in @("cikis", "kapat", "quit", "exit")) {
        break mainLoop
    }

    $messages += @{
        role    = "user"
        content = $text
    }

    $payload = @{
        model    = $Model
        messages = $messages
        stream   = $false
    } | ConvertTo-Json -Depth 20

    try {
        $response = Invoke-RestMethod -Uri "$OllamaUrl/api/chat" -Method Post -ContentType "application/json" -Body $payload -TimeoutSec 240
        $reply = $response.message.content.Trim()
    } catch {
        Write-Host "[Hata] Ollama cevabi alinamadi: $($_.Exception.Message)" -ForegroundColor Red
        if ($messages.Count -gt 1) {
            $messages = $messages[0..($messages.Count - 2)]
        }
        continue
    }

    if ([string]::IsNullOrWhiteSpace($reply)) {
        Write-Host "[Uyari] Bos cevap alindi." -ForegroundColor DarkYellow
        continue
    }

    $messages += @{
        role    = "assistant"
        content = $reply
    }

    Write-Host "Asistan: $reply" -ForegroundColor Yellow

    if ($synth) {
        Invoke-TtsSpeak -Synth $synth -Text $reply
    }
}

Write-Host "Asistan kapatildi." -ForegroundColor Green
