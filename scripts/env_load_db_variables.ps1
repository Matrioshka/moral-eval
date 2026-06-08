# Load .env variables into the current PowerShell process environment.
# Variables whose names contain API_KEY are intentionally skipped.

Get-Content .env | ForEach-Object {
    $line = $_.Trim()

    if (-not $line -or $line.StartsWith("#")) {
        return
    }

    if ($line -match '^([^=]+)=(.*)$') {
        $name = $matches[1].Trim()
        $value = $matches[2].Trim().Trim('"').Trim("'")

        if ($name -match 'API_KEY') {
            Write-Host "Skipped: $name"
            return
        }

        [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
        Write-Host "Loaded: $name"
    }
}
