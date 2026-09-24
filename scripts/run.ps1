<#
.SYNOPSIS
    Lance ProjectMgr en local sous Windows (venv + dépendances + serveur uvicorn).

.DESCRIPTION
    - Crée le virtualenv .venv s'il n'existe pas.
    - Installe/rafraîchit les dépendances (requirements.txt, +requirements-dev.txt avec -Dev).
    - Crée .env depuis .env.example s'il n'existe pas, avec un SECRET_KEY généré aléatoirement.
    - Démarre uvicorn en mode --reload.

.PARAMETER Dev
    Installe aussi requirements-dev.txt (nécessaire pour lancer les tests).

.PARAMETER Port
    Port d'écoute (défaut : 8000).

.PARAMETER SkipInstall
    Ne réinstalle pas les dépendances (démarrage plus rapide si rien n'a changé).

.EXAMPLE
    .\scripts\run.ps1
.EXAMPLE
    .\scripts\run.ps1 -Dev -Port 8080
#>
[CmdletBinding()]
param(
    [switch]$Dev,
    [int]$Port = 8000,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

# se placer à la racine du projet (parent du dossier scripts/) quel que soit le cwd d'appel
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$VenvPath = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Création du virtualenv .venv..." -ForegroundColor Cyan
    python -m venv $VenvPath
}

if (-not $SkipInstall) {
    Write-Host "Installation des dépendances..." -ForegroundColor Cyan
    & $VenvPython -m pip install --upgrade pip --quiet
    & $VenvPython -m pip install -r requirements.txt --quiet
    if ($Dev) {
        & $VenvPython -m pip install -r requirements-dev.txt --quiet
    }
}

$EnvFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "Création de .env depuis .env.example..." -ForegroundColor Cyan
    Copy-Item (Join-Path $RepoRoot ".env.example") $EnvFile

    # génère un SECRET_KEY aléatoire plutôt que de garder la valeur d'exemple
    $randomBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($randomBytes)
    $secretKey = -join ($randomBytes | ForEach-Object { $_.ToString("x2") })
    (Get-Content $EnvFile) -replace '^SECRET_KEY=.*', "SECRET_KEY=$secretKey" | Set-Content $EnvFile -Encoding utf8

    Write-Host "-> .env créé. Pense à changer ADMIN_USERNAME/ADMIN_PASSWORD si besoin." -ForegroundColor Yellow
}

Write-Host "Démarrage du serveur sur http://127.0.0.1:$Port ..." -ForegroundColor Green
& $VenvPython -m uvicorn app.main:app --reload --port $Port
