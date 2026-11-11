param(
    [Parameter(Mandatory = $true)][string]$Jobs,   # json: [{text, out}]
    [string]$Voice = "Microsoft Hanhan Desktop",
    [int]$Rate = 0                                  # -10 .. 10
)
# Offline TTS with a Windows SAPI voice. Writes one 48 kHz 16-bit mono WAV per job.
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$jobList = Get-Content -LiteralPath $Jobs -Raw -Encoding UTF8 | ConvertFrom-Json
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $names = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }
    if ($names -notcontains $Voice) { throw ("voice '" + $Voice + "' not installed. Installed: " + ($names -join ", ")) }
    $s.SelectVoice($Voice)
    $s.Rate = $Rate
    $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(48000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    foreach ($j in $jobList) {
        $s.SetOutputToWaveFile($j.out, $fmt)
        $s.Speak([string]$j.text)
    }
    $s.SetOutputToNull()
    Write-Output ("sapi: " + @($jobList).Count + " sentences")
} finally {
    $s.Dispose()
}
