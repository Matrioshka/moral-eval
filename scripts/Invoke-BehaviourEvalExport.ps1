param(
    [Parameter(Mandatory = $true)]
    [string]$DatasetVersion,

    [string]$Model = "openai/gpt-4.1-mini",

    [string]$EvalFile = "src/moral_sycophancy_eval/behaviour.py",

    [string]$LogDir = "logs",

    [string]$Csv,

    [int]$Limit = 0,

    [switch]$IncludeReviewColumns
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function New-SafeName {
    param([string]$Value)
    return ($Value -replace '[\\/:\s]+', '-' -replace '[^A-Za-z0-9_.-]', '')
}

$started = Get-Date

Write-Host "Running Inspect eval..." -ForegroundColor Cyan
Write-Host "Model:           $Model"
Write-Host "Dataset version: $DatasetVersion"

$inspectArgs = @(
    "eval",
    $EvalFile,
    "--model",
    $Model,
    "-T",
    "dataset_version=$DatasetVersion"
)

if ($Limit -gt 0) {
    $inspectArgs += @("--limit", "$Limit")
}

& inspect @inspectArgs

if ($LASTEXITCODE -ne 0) {
    throw "Inspect eval failed with exit code $LASTEXITCODE. Export skipped."
}

Write-Host "Evaluation completed. Finding new log..." -ForegroundColor Green

$newLogs = Get-ChildItem -Path (Join-Path $LogDir "*.eval") |
    Where-Object { $_.LastWriteTime -ge $started.AddSeconds(-5) } |
    Sort-Object LastWriteTime -Descending

if (-not $newLogs -or $newLogs.Count -eq 0) {
    throw "No new .eval log found in '$LogDir' after eval start time. Export skipped."
}

$latestLog = $newLogs[0].FullName

if ([string]::IsNullOrWhiteSpace($Csv)) {
    $safeModel = New-SafeName $Model
    $safeDataset = New-SafeName $DatasetVersion
    $suffix = if ($Limit -gt 0) { "limit$Limit" } else { "full" }

    $Csv = "tmp/${safeDataset}_${safeModel}_${suffix}_export.csv"
}

Write-Host "Exporting latest log:" -ForegroundColor Cyan
Write-Host $latestLog
Write-Host "CSV output:"
Write-Host $Csv

$exportArgs = @(
    "src/moral_sycophancy_eval/export_behaviour_outputs.py",
    $latestLog,
    "--csv",
    $Csv
)

if ($IncludeReviewColumns) {
    $exportArgs += "--include-review-columns"
}

& python @exportArgs

if ($LASTEXITCODE -ne 0) {
    throw "Export failed with exit code $LASTEXITCODE."
}

Write-Host "Export completed successfully." -ForegroundColor Green
Write-Host "CSV: $Csv"