<#
.SYNOPSIS
  Health-check the local PostgreSQL provenance layer.

.DESCRIPTION
  Run this on each machine after loading .env variables. The script checks Docker,
  the configured PostgreSQL container, the expected database objects, broad trace
  counts, reporting-safe trace counts, and source-file inventory hashes.

  It does not print PGPASSWORD and does not mutate the database.

.EXAMPLE
  .\scripts\load_env_db_variables.ps1
  .\scripts\test_postgres_provenance_layer.ps1

.EXAMPLE
  .\scripts\test_postgres_provenance_layer.ps1 -OutDir tmp\provenance_health_checks
#>

[CmdletBinding()]
param(
    [string]$OutDir = "tmp/provenance_health_checks",
    [string]$RawSchema = $(if ($env:MORAL_EVALS_RAW_SCHEMA) { $env:MORAL_EVALS_RAW_SCHEMA } else { "public" }),
    [string]$OpSchema = $(if ($env:MORAL_EVALS_OP_SCHEMA) { $env:MORAL_EVALS_OP_SCHEMA } else { "public" }),
    [string]$RptSchema = $(if ($env:MORAL_EVALS_RPT_SCHEMA) { $env:MORAL_EVALS_RPT_SCHEMA } else { "public" }),
    [switch]$SkipIngestSourcePlan
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Section {
    param([string]$Title)
    $line = "=" * 80
    Write-Output ""
    Write-Output $line
    Write-Output $Title
    Write-Output $line
}

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found on PATH: $Name"
    }
}

function Run-CommandText {
    param(
        [string]$Label,
        [scriptblock]$Command
    )
    Write-Section $Label
    try {
        $output = & $Command 2>&1
        if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) {
            Write-Output $output
            throw "Command failed for section: $Label"
        }
        Write-Output $output
    }
    catch {
        Write-Output "ERROR: $($_.Exception.Message)"
        throw
    }
}

function Query-PostgresCsv {
    param([string]$Sql)
    docker exec -i $env:PGCONTAINER psql -U $env:PGUSER -d $env:PGDATABASE -A -F "," -c $Sql
}

function Query-PostgresTable {
    param([string]$Sql)
    docker exec -i $env:PGCONTAINER psql -U $env:PGUSER -d $env:PGDATABASE -c $Sql
}

function Quote-PgIdentifier {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) {
        throw "PostgreSQL identifier must not be blank."
    }
    '"' + $Name.Replace('"', '""') + '"'
}

function Quote-PgLiteral {
    param([string]$Value)
    "'" + $Value.Replace("'", "''") + "'"
}

function Pg-Relation {
    param(
        [string]$Schema,
        [string]$Name
    )
    "$(Quote-PgIdentifier $Schema).$(Quote-PgIdentifier $Name)"
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

# Load local .env helper if the user has not already loaded the variables.
$loader = Join-Path $PSScriptRoot "load_env_db_variables.ps1"
if ((-not $env:PGCONTAINER -or -not $env:PGDATABASE -or -not $env:PGUSER) -and (Test-Path $loader)) {
    . $loader | Out-Null
}

$requiredEnv = @("PGCONTAINER", "PGDATABASE", "PGUSER")
foreach ($name in $requiredEnv) {
    if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
        throw "Missing required environment variable: $name. Run .\scripts\load_env_db_variables.ps1 first."
    }
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$machine = $env:COMPUTERNAME
$reportPath = Join-Path $OutDir "provenance_health_${machine}_${timestamp}.txt"
$inventoryPath = Join-Path $OutDir "provenance_source_inventory_${machine}_${timestamp}.csv"
$modelCountsPath = Join-Path $OutDir "provenance_dataset_model_counts_${machine}_${timestamp}.csv"

$SourceFile = Pg-Relation $RawSchema "source_file"
$Dataset = Pg-Relation $RawSchema "dataset"
$DatasetCase = Pg-Relation $RawSchema "dataset_case"
$ModelResponse = Pg-Relation $RawSchema "model_response"
$ManualScore = Pg-Relation $RawSchema "manual_score"
$DeterministicScore = Pg-Relation $RawSchema "deterministic_score"
$CaseRunTrace = Pg-Relation $RptSchema "case_run_trace"
$CaseRunTraceReporting = Pg-Relation $RptSchema "case_run_trace_reporting"
$RptSchemaLiteral = Quote-PgLiteral $RptSchema

$reportLines = New-Object System.Collections.Generic.List[string]

function Add-Report {
    param([string[]]$Lines)
    foreach ($line in $Lines) {
        $reportLines.Add($line)
        Write-Output $line
    }
}

Require-Command docker
Require-Command python

Add-Report @(
    "Provenance layer health check",
    "Timestamp: $(Get-Date -Format o)",
    "Machine: $machine",
    "Repo: $repoRoot",
    "PGCONTAINER: $env:PGCONTAINER",
    "PGDATABASE: $env:PGDATABASE",
    "PGUSER: $env:PGUSER",
    "PGPASSWORD: <redacted>",
    "Raw schema: $RawSchema",
    "Operational schema: $OpSchema",
    "Reporting schema: $RptSchema"
)

Run-CommandText "Docker container status" {
    docker ps -a --filter "name=$env:PGCONTAINER" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
} | ForEach-Object { $reportLines.Add($_) }

Run-CommandText "Python version" { python --version } | ForEach-Object { $reportLines.Add($_) }

if (-not $SkipIngestSourcePlan) {
    Run-CommandText "Ingest source plan counts" {
        $sourcePlan = python .\scripts\ingest_eval_artifacts_to_postgres.py --list-sources
        $sections = @{}
        $current = $null
        foreach ($line in $sourcePlan) {
            if ($line -match '^\[(.+)\]$') {
                $current = $Matches[1]
                if (-not $sections.ContainsKey($current)) { $sections[$current] = 0 }
            }
            elseif ($current -and $line.Trim().Length -gt 0) {
                $sections[$current] += 1
            }
        }
        $sections.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name),$($_.Value)" }
    } | ForEach-Object { $reportLines.Add($_) }
}

Run-CommandText "Database object existence" {
    Query-PostgresTable "select table_name from information_schema.views where table_schema = $RptSchemaLiteral and table_name in ('case_run_trace','case_run_trace_reporting') order by table_name;"
} | ForEach-Object { $reportLines.Add($_) }

Run-CommandText "Core table/view counts" {
    Query-PostgresTable "
select
  (select count(*) from $SourceFile) as source_files,
  (select count(*) from $DatasetCase) as dataset_cases,
  (select count(*) from $ModelResponse) as model_responses,
  (select count(*) from $ManualScore) as manual_scores,
  (select count(*) from $DeterministicScore) as deterministic_scores,
  (select count(*) from $CaseRunTrace) as case_trace_rows,
  (select count(*) from $CaseRunTraceReporting) as reporting_trace_rows;
"
} | ForEach-Object { $reportLines.Add($_) }

Run-CommandText "Source-file kind totals" {
    Query-PostgresTable "
select file_kind, count(*) as files, coalesce(sum(record_count),0) as record_count
from $SourceFile
group by file_kind
order by file_kind;
"
} | ForEach-Object { $reportLines.Add($_) }

Run-CommandText "Case origin totals" {
    Query-PostgresTable "
select case_origin, is_canonical_dataset_item, count(*)
from $CaseRunTrace
group by case_origin, is_canonical_dataset_item
order by case_origin, is_canonical_dataset_item;
"
} | ForEach-Object { $reportLines.Add($_) }

Run-CommandText "Reporting-view guard checks" {
    Query-PostgresTable "
select
  (select count(*) from $CaseRunTraceReporting where not is_canonical_dataset_item) as noncanonical_reporting_rows,
  (select count(*) from $CaseRunTraceReporting where source_files::text ilike '%smoke%') as smoke_reporting_rows,
  (select count(*) from $CaseRunTraceReporting where dataset_version ilike '%summary%') as summary_reporting_rows,
  (select count(*) from $CaseRunTraceReporting where dataset_version ilike '%expansion_candidates%') as expansion_candidate_reporting_rows,
  (select count(*) from $CaseRunTraceReporting where dataset_version ilike '%rewrite_candidate%') as rewrite_candidate_reporting_rows;
"
} | ForEach-Object { $reportLines.Add($_) }

Run-CommandText "Top dataset/model trace counts" {
    Query-PostgresTable "
select dataset_version, coalesce(model_name, '<null>') as model_name, count(*)
from $CaseRunTrace
group by dataset_version, model_name
order by dataset_version, model_name
limit 100;
"
} | ForEach-Object { $reportLines.Add($_) }

Query-PostgresCsv "select file_kind, file_path, content_sha256, record_count from $SourceFile order by file_path;" | Set-Content -Encoding UTF8 $inventoryPath
Query-PostgresCsv "select dataset_version, coalesce(model_name, '<null>') as model_name, count(*) from $CaseRunTrace group by dataset_version, model_name order by dataset_version, model_name;" | Set-Content -Encoding UTF8 $modelCountsPath

Add-Report @(
    "",
    "Inventory CSV: $inventoryPath",
    "Dataset/model counts CSV: $modelCountsPath",
    "Report file: $reportPath",
    "",
    "Compare two machines with:",
    "Compare-Object (Get-Content .\laptop_inventory.csv) (Get-Content .\desktop_inventory.csv)",
    "Compare-Object (Get-Content .\laptop_dataset_model_counts.csv) (Get-Content .\desktop_dataset_model_counts.csv)"
)

$reportLines | Set-Content -Encoding UTF8 $reportPath

Write-Output ""
Write-Output "Health check complete."
Write-Output "Report: $reportPath"
Write-Output "Inventory: $inventoryPath"
Write-Output "Dataset/model counts: $modelCountsPath"
