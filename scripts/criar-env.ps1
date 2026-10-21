$ErrorActionPreference = "Stop"
$raiz = Split-Path $PSScriptRoot -Parent
function Segredo { -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ }) }
$linhas = (Get-Content (Join-Path $raiz ".env.example")) -replace "^DELCO_DB_PASSWORD=.*", "DELCO_DB_PASSWORD=$(Segredo)"
[IO.File]::WriteAllLines((Join-Path $raiz ".env"), $linhas)
Write-Host "Arquivo .env criado com uma senha aleatoria para o MySQL."
