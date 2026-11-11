param(
    [Parameter(Mandatory = $true)][string]$Deck,      # WORK\_deck_copy.pptx
    [Parameter(Mandatory = $true)][string]$Jobs       # json: [{orig, out, w, h, hide:["S3","S2.1",...]}]
)
# Export "reveal" states: for each job, duplicate the slide, hide the listed shapes (S3) or
# paragraphs (S3.2, made fully transparent so layout does not move), export, delete the duplicate.
$ErrorActionPreference = "Stop"
$Deck = (Resolve-Path $Deck).Path
$jobList = Get-Content -LiteralPath $Jobs -Raw -Encoding UTF8 | ConvertFrom-Json
$app = New-Object -ComObject PowerPoint.Application
$wasOpen = 0
try { $wasOpen = $app.Presentations.Count } catch { $wasOpen = 0 }
$p = $null
try {
    $p = $app.Presentations.Open($Deck, $false, $false, $false)
    $done = 0
    foreach ($j in $jobList) {
        $dup = $p.Slides.Item([int]$j.orig).Duplicate().Item(1)
        try {
            foreach ($id in $j.hide) {
                if ($id -notmatch '^S(\d+)(?:\.(\d+))?$') { continue }
                $k = [int]$Matches[1]
                if ($k -gt $dup.Shapes.Count) { continue }
                $x = $dup.Shapes.Item($k)
                if ($Matches[2]) {
                    $para = $x.TextFrame2.TextRange.Paragraphs([int]$Matches[2])
                    $para.Font.Fill.Transparency = 1.0
                    try { $para.ParagraphFormat.Bullet.Font.Fill.Transparency = 1.0 } catch {}
                    try { $para.ParagraphFormat.Bullet.Visible = 0 } catch {}
                } else {
                    $x.Visible = 0
                }
            }
            $dup.Export($j.out, "PNG", [int]$j.w, [int]$j.h)
            $done++
        } finally {
            $dup.Delete()
        }
    }
    Write-Output "exported $done reveal states"
} finally {
    if ($p) { $p.Saved = $true; $p.Close(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($p) }
    if ($wasOpen -eq 0 -and $app.Presentations.Count -eq 0) { $app.Quit() }
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}
