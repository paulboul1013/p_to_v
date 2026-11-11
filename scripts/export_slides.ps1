param(
    [Parameter(Mandatory = $true)][string]$Pptx,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [int]$MaxW = 3840,
    [int]$MaxH = 2160,
    [int]$MaxSlides = 0
)
# Export visible slides of a .pptx to PNG through PowerPoint COM, plus a layout map.
# - works on a private copy (kept as WORK\_deck_copy.pptx for later state exports),
#   so a deck the user has open is never touched
# - only quits PowerPoint if it was not already running with open presentations
# - keeps the deck's aspect ratio (fits inside MaxW x MaxH)
# - skips hidden slides; files are numbered slide_001.png ... in show order
# - writes OutDir\slides.json: per slide the original index, title, and every shape /
#   paragraph with an id (S3, S3.2) and a normalized box [x, y, w, h] (0..1)
$ErrorActionPreference = "Stop"
$Pptx = (Resolve-Path $Pptx).Path
New-Item -ItemType Directory -Force $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
Get-ChildItem $OutDir -Filter "slide_*.png" | Remove-Item -Force
$copy = Join-Path (Split-Path $OutDir -Parent) ("_deck_copy" + [IO.Path]::GetExtension($Pptx))
Copy-Item -LiteralPath $Pptx -Destination $copy -Force

function Short([string]$t, [int]$n = 70) {
    $t = ($t -replace "[\r\n\v\u000b]+", " ").Trim()
    if ($t.Length -gt $n) { $t = $t.Substring(0, $n) + "..." }
    return $t
}

$app = New-Object -ComObject PowerPoint.Application
$wasOpen = 0
try { $wasOpen = $app.Presentations.Count } catch { $wasOpen = 0 }
$p = $null
try {
    # Open(FileName, ReadOnly, Untitled, WithWindow)
    $p = $app.Presentations.Open($copy, $true, $false, $false)
    $sw = $p.PageSetup.SlideWidth; $sh = $p.PageSetup.SlideHeight
    if (($sw / $sh) -ge ($MaxW / $MaxH)) { $w = $MaxW; $h = [int][Math]::Round($MaxW * $sh / $sw) }
    else { $h = $MaxH; $w = [int][Math]::Round($MaxH * $sw / $sh) }
    $n = 0; $skipped = @(); $meta = @()
    foreach ($s in $p.Slides) {
        if ($s.SlideShowTransition.Hidden -ne 0) { $skipped += $s.SlideIndex; continue }
        if ($MaxSlides -gt 0 -and $n -ge $MaxSlides) { break }
        $n++
        $s.Export((Join-Path $OutDir ("slide_{0:D3}.png" -f $n)), "PNG", $w, $h)

        $title = ""
        try { if ($s.Shapes.HasTitle -ne 0) { $title = Short $s.Shapes.Title.TextFrame.TextRange.Text 60 } } catch {}
        $shapes = @(); $k = 0
        foreach ($x in $s.Shapes) {
            $k++
            $o = [ordered]@{ id = "S$k"; name = $x.Name; text = ""
                box = @([Math]::Round($x.Left / $sw, 4), [Math]::Round($x.Top / $sh, 4),
                        [Math]::Round($x.Width / $sw, 4), [Math]::Round($x.Height / $sh, 4)); paras = @() }
            try {
                if ($x.HasTable -ne 0) { $o.text = "(table)" }
                elseif ($x.HasChart -ne 0) { $o.text = "(chart)" }
                elseif ($x.Type -eq 13) { $o.text = "(picture)" }
                elseif ($x.Type -eq 6) { $o.text = "(group)" }
            } catch {}
            try {
                if ($x.HasTextFrame -ne 0 -and $x.TextFrame.HasText -ne 0) {
                    $tr = $x.TextFrame.TextRange
                    $o.text = Short $tr.Text
                    $pc = $tr.Paragraphs().Count
                    if ($pc -gt 1) {
                        for ($j = 1; $j -le $pc; $j++) {
                            $pr = $tr.Paragraphs($j)
                            if ($pr.Text.Trim().Length -eq 0) { continue }
                            $o.paras += [ordered]@{ id = "S$k.$j"; text = (Short $pr.Text 50)
                                box = @([Math]::Round($pr.BoundLeft / $sw, 4), [Math]::Round($pr.BoundTop / $sh, 4),
                                        [Math]::Round($pr.BoundWidth / $sw, 4), [Math]::Round($pr.BoundHeight / $sh, 4)) }
                        }
                    }
                }
            } catch {}
            $shapes += $o
        }
        $meta += [ordered]@{ n = $n; orig = $s.SlideIndex; title = $title; shapes = $shapes }
    }
    $json = ConvertTo-Json -InputObject @($meta) -Depth 8
    [IO.File]::WriteAllText((Join-Path $OutDir "slides.json"), $json, (New-Object System.Text.UTF8Encoding $false))
    Write-Output "exported $n slides (${w}x${h}) to $OutDir"
    if ($skipped.Count) { Write-Output ("skipped hidden slides: " + ($skipped -join ", ")) }
} finally {
    if ($p) { $p.Close(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($p) }
    if ($wasOpen -eq 0 -and $app.Presentations.Count -eq 0) { $app.Quit() }
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}
