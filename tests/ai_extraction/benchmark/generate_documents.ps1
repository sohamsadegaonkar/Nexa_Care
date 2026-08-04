param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'documents')
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
[System.IO.Directory]::CreateDirectory($OutputDirectory) | Out-Null

$documents = @(
    @{ File='01-simple-laboratory-form.png'; Style='bordered'; Title='SIMPLE LABORATORY FORM'; Lines=@('Patient Name: Synthetic Patient Alpha','HbA1c: 7.2 %','Laboratory: Demo Care Laboratory','Physician: Test Physician') },
    @{ File='02-multiple-laboratory-rows.jpg'; Style='table'; Title='LABORATORY RESULTS'; Lines=@('Patient Name: Synthetic Patient Lambda','Investigation | Value | Unit | Reference Range','HbA1c | 6.8 | % | 4.0 - 5.6','FBS | 108 | mg/dL | 70 - 100','RBS | 146 | mg/dL | 70 - 140') },
    @{ File='03-repeated-glucose-table.png'; Style='compact'; Title='REPEATED GLUCOSE MEASUREMENTS'; Lines=@('Patient Name: Synthetic Patient Mu','Test | Result | Unit | Date','FBS | 102 | mg/dL | 05 Aug 2026','PPBS | 138 | mg/dL | 05 Aug 2026','RBS | 121 | mg/dL | 05 Aug 2026') },
    @{ File='04-hba1c-report.jpg'; Style='borderless'; Title='GLYCATED HAEMOGLOBIN REPORT'; Lines=@('NAME OF PATIENT     Synthetic Patient Beta','GLYCATED HAEMOGLOBIN     7.4 %','FACILITY     Demo Care Laboratory','ORDERED BY     Test Physician') },
    @{ File='05-vital-sign-form.png'; Style='twocolumn'; Title='VITAL SIGN FORM'; Lines=@('Patient Name: Synthetic Patient Gamma','Blood Pressure: 118/76 mmHg','Heart Rate: 72 bpm','Example Hospital | Test Physician') },
    @{ File='06-blood-pressure-pulse-report.jpg'; Style='bordered'; Title='BP AND PULSE REPORT'; Lines=@('Patient Name: Synthetic Patient Delta','BP: 126/82 mmHg','Pulse: 78 bpm','Recorded at Example Hospital') },
    @{ File='07-medication-table.png'; Style='table'; Title='MEDICATION TABLE'; Lines=@('Patient Name: Synthetic Patient Nu','Medicine | Strength | Frequency | Route | Duration','SyntheticMed Alpha | 5 mg | Once daily | Oral | 7 days','SyntheticMed Beta | 10 mg | Twice daily | Oral | 5 days','SyntheticMed Gamma | 2 mg | At night | Oral | 10 days') },
    @{ File='08-prescription-list.jpg'; Style='borderless'; Title='SYNTHETIC PRESCRIPTION'; Lines=@('Patient Name: Synthetic Patient Epsilon','Medication: SyntheticMed Delta 25 mg once daily','Medication: SyntheticMed Epsilon 10 mg twice daily','Medication: SyntheticMed Zeta 5 mg at night','Prescriber: Test Physician') },
    @{ File='09-diagnosis-form.png'; Style='bordered'; Title='DIAGNOSIS FORM'; Lines=@('Patient Name: Synthetic Patient Zeta','Diagnosis: Synthetic Condition Alpha','Provisional Diagnosis: Synthetic Condition Beta','Example Hospital | Test Physician') },
    @{ File='10-mixed-clinical-summary.jpg'; Style='twocolumn'; Title='MIXED CLINICAL SUMMARY'; Lines=@('Patient Name: Synthetic Patient Eta','HbA1c: 6.9 %','Blood Glucose: 132 mg/dL','Blood Pressure: 122/80 mmHg','Heart Rate: 74 bpm','Medication: SyntheticMed Eta 5 mg once daily','Diagnosis: Synthetic Condition Gamma') },
    @{ File='11-alternate-label-synonyms.png'; Style='borderless'; Title='ALTERNATE LABEL FORM'; Lines=@('NAME OF PATIENT: Synthetic Patient Theta','HEALTH ID: 99-0000-0000-0001','CONTACT: 00000 00000','GLYCATED HAEMOGLOBIN: 7.1 %','PULSE: 76 bpm') },
    @{ File='12-repeated-conflicting-values.jpg'; Style='compact'; Title='REPEATED AND CONFLICTING VALUES'; Lines=@('Patient Name: Synthetic Patient Iota','HbA1c: 7.0 %','HbA1c: 7.8 %','BP: 120/80 mmHg','BP: 138/88 mmHg','Values intentionally retained for review') },
    @{ File='13-incomplete-lab-row.png'; Style='table'; Title='INCOMPLETE LABORATORY ROW'; Lines=@('Patient Name: Synthetic Patient Xi','Investigation | Result | Unit | Reference Range','Blood Glucose | 142 |  | ','Row intentionally omits unit and range') },
    @{ File='14-incomplete-medication-row.jpg'; Style='table'; Title='INCOMPLETE MEDICATION ROW'; Lines=@('Patient Name: Synthetic Patient Omicron','Medicine | Strength | Frequency | Route | Duration','SyntheticMed Kappa | 20 mg |  | Oral | ','Row intentionally omits frequency and duration') },
    @{ File='15-identity-mismatch.png'; Style='bordered'; Title='IDENTITY MISMATCH FIXTURE'; Lines=@('Patient Name: Synthetic Patient Mismatch','Health ID: 99-0000-0000-0015','HbA1c: 6.5 %','Bound identity is Synthetic Patient Bound') }
)

function Add-TextLine {
    param($Graphics, $Text, $Font, $Brush, [float]$X, [float]$Y)
    $Graphics.DrawString($Text, $Font, $Brush, $X, $Y)
}

foreach ($document in $documents) {
    $bitmap = [System.Drawing.Bitmap]::new(1400, 1800)
    $bitmap.SetResolution(150, 150)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.Clear([System.Drawing.Color]::White)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $black = [System.Drawing.Brushes]::Black
    $navy = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(18, 54, 92))
    $red = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(160, 20, 20))
    $header = [System.Drawing.Font]::new('Arial', 30, [System.Drawing.FontStyle]::Bold)
    $title = [System.Drawing.Font]::new('Arial', 24, [System.Drawing.FontStyle]::Bold)
    $body = [System.Drawing.Font]::new('Arial', 20, [System.Drawing.FontStyle]::Regular)
    $twoColumnFont = [System.Drawing.Font]::new('Arial', 16, [System.Drawing.FontStyle]::Regular)
    $mono = [System.Drawing.Font]::new('Consolas', 14, [System.Drawing.FontStyle]::Regular)
    $small = [System.Drawing.Font]::new('Arial', 14, [System.Drawing.FontStyle]::Bold)
    $pen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(60, 60, 60), 2)
    try {
        Add-TextLine $graphics 'NEXA CARE SYNTHETIC BENCHMARK' $header $navy 70 55
        Add-TextLine $graphics 'NOT REAL PATIENT DATA - FOR TEXTRACT QUALIFICATION ONLY' $small $red 72 118
        Add-TextLine $graphics $document.Title $title $black 70 190
        $graphics.DrawLine($pen, 70, 245, 1330, 245)
        $y = 310
        $lineIndex = 0
        $twoColumnIndex = 0
        foreach ($line in $document.Lines) {
            $isIdentityAboveTable = $document.Style -in @('table', 'compact') -and $line.StartsWith('Patient Name:')
            $isTableNote = $document.Style -in @('table', 'compact') -and $line.StartsWith('Row intentionally')
            $isTableCells = $document.Style -in @('table', 'compact') -and $line.Contains('|')
            $isFullWidth = $document.Style -eq 'twocolumn' -and ($line.StartsWith('Patient Name:') -or $line.StartsWith('Medication:') -or $line.StartsWith('Diagnosis:'))
            $font = if ($isTableCells) { $mono } elseif ($document.Style -eq 'twocolumn') { $twoColumnFont } else { $body }
            $x = if ($document.Style -eq 'twocolumn' -and -not $isFullWidth -and $twoColumnIndex % 2 -eq 1) { 720 } else { 92 }
            if ($document.Style -eq 'bordered') {
                $graphics.DrawRectangle($pen, 70, $y - 10, 1260, 72)
            } elseif ($isTableCells) {
                $cells = $line.Split('|')
                $widths = if ($cells.Count -eq 5) { @(360, 180, 300, 180, 240) } elseif ($cells.Count -eq 4) { @(340, 220, 200, 500) } else { @() }
                $cellX = 70
                for ($cellIndex = 0; $cellIndex -lt $cells.Count; $cellIndex += 1) {
                    $cellWidth = if ($widths.Count -eq $cells.Count) { $widths[$cellIndex] } else { 1260 / $cells.Count }
                    $graphics.DrawRectangle($pen, $cellX, $y - 10, $cellWidth, 66)
                    Add-TextLine $graphics $cells[$cellIndex].Trim() $font $black ($cellX + 12) $y
                    $cellX += $cellWidth
                }
            }
            if (-not $isTableCells) { Add-TextLine $graphics $line $font $black $x $y }
            if ($document.Style -eq 'twocolumn') {
                if ($isFullWidth) {
                    $y += 110
                } else {
                    if ($twoColumnIndex % 2 -eq 1) { $y += 110 }
                    $twoColumnIndex += 1
                }
            } elseif ($isIdentityAboveTable -or $isTableNote) {
                $y += 125
            } else {
                $y += if ($document.Style -eq 'compact') { 76 } else { 92 }
            }
            $lineIndex += 1
        }
        Add-TextLine $graphics 'Demo Care Laboratory | Example Hospital | Test Physician' $small $navy 70 1660
        Add-TextLine $graphics ('Corpus file: ' + $document.File) $small $black 70 1705
        $target = Join-Path $OutputDirectory $document.File
        if ([System.IO.Path]::GetExtension($target) -eq '.jpg') {
            $bitmap.Save($target, [System.Drawing.Imaging.ImageFormat]::Jpeg)
        } else {
            $bitmap.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
        }
    } finally {
        $pen.Dispose(); $small.Dispose(); $mono.Dispose(); $twoColumnFont.Dispose(); $body.Dispose()
        $title.Dispose(); $header.Dispose(); $red.Dispose(); $navy.Dispose()
        $graphics.Dispose(); $bitmap.Dispose()
    }
}

Write-Output ("GENERATED_DOCUMENTS={0}" -f $documents.Count)
