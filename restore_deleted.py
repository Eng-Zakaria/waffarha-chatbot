#!/usr/bin/env python3
"""
Restore all deleted files from git HEAD into a backup/ directory
preserving the original folder structure
"""
import os
import subprocess
from pathlib import Path

BACKUP_DIR = Path("backup")
REPO_ROOT = Path.cwd()

# Remove existing backup if present
if BACKUP_DIR.exists():
    import shutil
    shutil.rmtree(BACKUP_DIR)

BACKUP_DIR.mkdir(parents=True)

# Get list of all files that exist in git HEAD
result = subprocess.run(
    ["git", "ls-tree", "-r", "HEAD", "--name-only"],
    cwd=REPO_ROOT,
    capture_output=True,
    text=True
)

if result.returncode != 0:
    print(f"Error getting file list: {result.stderr}")
    exit(1)

all_files = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]

# Filter to files that DON'T exist in working directory
deleted_files = []
for f in all_files:
    filepath = REPO_ROOT / f
    if not filepath.exists():
        deleted_files.append(f)

print(f"Found {len(deleted_files)} files missing from working directory")

# Restore each file
restored = 0
for f in deleted_files:
    try:
        # Create parent directory in backup
        backup_path = BACKUP_DIR / f
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        # Restore file from git
        result = subprocess.run(
            ["git", "show", f"HEAD:{f}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            # Write to backup
            with open(backup_path, 'w', encoding='utf-8') as out:
                out.write(result.stdout)
            print(f"Restored: {f} -> backup/{f}")
            restored += 1
        else:
            print(f"Failed: {f} - {result.stderr.strip()}")
    except Exception as e:
        print(f"Error restoring {f}: {e}")

print(f"\nDone! {restored} files restored to {BACKUP_DIR}/")