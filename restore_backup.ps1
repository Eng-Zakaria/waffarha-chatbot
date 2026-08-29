# PowerShell script to restore all deleted files from git HEAD into a backup/ directory
# Run this from the project root: C:\Users\devza\Work\Waffarha\waffarha-chatbot

$repoRoot = "C:\Users\devza\Work\Waffarha\waffarha-chatbot"
$backupDir = Join-Path $repoRoot "backup"

# Remove existing backup if present
if (Test-Path $backupDir) {
    Remove-Item $backupDir -Recurse -Force
}
New-Item -ItemType Directory -Path $backupDir | Out-Null

# Get list of all files that exist in git HEAD
$gitFiles = git -C $repoRoot ls-tree -r HEAD --name-only

$deletedFiles = @()
foreach ($f in $gitFiles) {
    $fullPath = Join-Path $repoRoot $f
    if (-not (Test-Path $fullPath)) {
        $deletedFiles += $f
    }
}

Write-Host "Found $($deletedFiles.Count) files missing from working directory"

$restored = 0
foreach ($f in $deletedFiles) {
    try {
        # Create parent directory in backup
        $backupPath = Join-Path $backupDir $f
        $backupParent = Split-Path $backupPath -Parent
        if (-not (Test-Path $backupParent)) {
            New-Item -ItemType Directory -Path $backupParent -Force | Out-Null
        }

        # Restore file from git
        $content = git -C $repoRoot show "HEAD:$f"
        if ($LASTEXITCODE -eq 0) {
            # Write to backup
            [System.IO.File]::WriteAllText($backupPath, $content, [System.Text.Encoding]::UTF8)
            Write-Host "Restored: $f -> backup/$f"
            $restored++
        } else {
            Write-Host "Failed: $f"
        }
    } catch {
        Write-Host "Error restoring $f : $_"
    }
}

Write-Host "`nDone! $restored files restored to $backupDir/"