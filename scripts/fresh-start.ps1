<#
.SYNOPSIS
    Detach a freshly cloned copy of this template from its origin and reset
    its git history to a single clean commit.

.DESCRIPTION
    There is no git "post-clone" hook (the .git directory is never transferred
    by a clone), so this is run manually once after cloning the template:

        pwsh ./scripts/fresh-start.ps1

    It removes the existing .git directory (dropping all history AND the
    original remote), re-initialises the repo on branch `dev` (the team trunk;
    there is no `main`), and creates a single "Initial commit" so the new
    project starts from a clean slate.

.PARAMETER Force
    Skip the interactive confirmation prompt.
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# Resolve the repo root (parent of this script's directory) so the script
# works regardless of the current working directory.
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path (Join-Path $repoRoot '.git'))) {
    Write-Error "No .git directory found in $repoRoot - nothing to reset."
}

$origin = (& git remote get-url origin 2>$null)

Write-Host ""
Write-Host "This will PERMANENTLY delete all git history in:" -ForegroundColor Yellow
Write-Host "  $repoRoot"
if ($origin) { Write-Host "and detach it from origin ($origin)." }
Write-Host "The working tree (your files) is left untouched." -ForegroundColor Yellow
Write-Host ""

if (-not $Force) {
    $answer = Read-Host "Type 'yes' to continue"
    if ($answer -ne 'yes') {
        Write-Host "Aborted. Nothing changed." -ForegroundColor Cyan
        return
    }
}

# Dropping .git removes both the history and every configured remote.
Remove-Item -Recurse -Force (Join-Path $repoRoot '.git')

# `dev` is the team trunk; there is no `main`.
& git init -b dev
& git add -A
& git commit -m "Initial commit" | Out-Null

Write-Host ""
Write-Host "Done. Fresh history on branch 'dev', no remote configured." -ForegroundColor Green
Write-Host "Add your own remote when ready, e.g.:" -ForegroundColor Cyan
Write-Host "  git remote add origin <your-new-repo-url>"
Write-Host "  git push -u origin dev"
