#!/bin/bash
# Restore all deleted files from git HEAD into a backup/ directory
# preserving the original folder structure

BACKUP_DIR="backup"

# Remove existing backup if present
rm -rf "$BACKUP_DIR"

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Get list of all deleted/modified files from git status
# These are files that exist in git HEAD but not in working directory
DELETED_FILES=$(git diff HEAD --name-only --diff-filter=D)

# Also include files that exist in HEAD but not in working tree
for f in $(git ls-tree -r HEAD --name-only); do
    if [ ! -e "$f" ]; then
        DELETED_FILES="$DELETED_FILES
$f"
    fi
done

# Deduplicate and process
echo "$DELETED_FILES" | sort -u | while IFS= read -r f; do
    if [ -n "$f" ]; then
        # Create parent directory in backup
        DIR=$(dirname "$BACKUP_DIR/$f")
        mkdir -p "$DIR"
        # Restore file from git
        git show "HEAD:$f" > "$BACKUP_DIR/$f" 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "Restored: $f -> backup/$f"
        fi
    fi
done

echo ""
echo "Done! Summary:"
echo "$(find "$BACKUP_DIR" -type f | wc -l) files restored to $BACKUP_DIR/"