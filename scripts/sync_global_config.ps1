# Sync Global Config Script for Windows
# Syncs global rules and skills to Antigravity environment

param(
    [switch]$Force = $false
)

$ErrorActionPreference = "Stop"

# Configuration
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$AntigravityDir = "$env:USERPROFILE\.gemini\antigravity"
$SkillsTarget = "$AntigravityDir\skills"

Write-Host "=== Kang IDE Config Sync ===" -ForegroundColor Cyan
Write-Host "Repository: $RepoRoot"
Write-Host "Target: $AntigravityDir"
Write-Host ""

# Create target directories if they don't exist
if (-not (Test-Path $AntigravityDir)) {
    Write-Host "Creating Antigravity directory..." -ForegroundColor Yellow
    New-Item -Path $AntigravityDir -ItemType Directory -Force | Out-Null
}

# Sync skills
$SourceSkills = Join-Path $RepoRoot "skills"
if (Test-Path $SourceSkills) {
    $SkillFolders = Get-ChildItem -Path $SourceSkills -Directory
    foreach ($skill in $SkillFolders) {
        $TargetSkill = Join-Path $SkillsTarget $skill.Name
        if ((Test-Path $TargetSkill) -and -not $Force) {
            Write-Host "Skipping existing skill: $($skill.Name)" -ForegroundColor Gray
        } else {
            Write-Host "Syncing skill: $($skill.Name)" -ForegroundColor Green
            Copy-Item -Path $skill.FullName -Destination $SkillsTarget -Recurse -Force
        }
    }
}

Write-Host ""
Write-Host "=== Sync Complete ===" -ForegroundColor Cyan
Write-Host "To force overwrite existing skills, run with -Force flag"
