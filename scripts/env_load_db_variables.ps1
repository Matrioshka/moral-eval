# Allowed prefixes for environment variables to load from the .env file
$allowedPrefixes = @("PG", "MORAL_EVALS")

# Read and process the .env file
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') {
        $name = $matches[1].Trim()
        $value = $matches[2].Trim()

        # Check if the variable name starts with any of the allowed prefixes
        foreach ($prefix in $allowedPrefixes) {
            if ($name.StartsWith($prefix)) {
                [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
                Write-Host "Loaded: $name"
                break
            }
        }
    }
}

# Construct the MORAL_EVALS_DATABASE_URL using the newly loaded process variables
# Note: In PowerShell, use $env:VAR_NAME to access environment variables
$dbUrl = "postgresql://$($env:PGUSER):$($env:PGPASSWORD)@$($env:PGHOST):$($env:PGPORT)/$($env:PGDATABASE)"

# Save it to the Process environment so Python can see it
[System.Environment]::SetEnvironmentVariable("MORAL_EVALS_DATABASE_URL", $dbUrl, "Process")

# alternatively you can use the following code to load all environment variables from the .env file without filtering by prefix:
## Install-Module -Name DotEnv -Scope CurrentUser
# Import-Module DotEnv
# Invoke-DotEnv