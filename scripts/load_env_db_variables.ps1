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

# alternatively you can use the following code to load all environment variables from the .env file without filtering by prefix:
## Install-Module -Name DotEnv -Scope CurrentUser
# Import-Module DotEnv
# Invoke-DotEnv