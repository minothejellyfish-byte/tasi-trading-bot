#!/bin/bash
# Backup wrapper — Call this before ANY file edit in tasi-exec
# Usage: ./backups/.backup_before_edit.sh <file_to_edit>

FILE="$1"

if [ -z "$FILE" ]; then
    echo "Usage: ./backups/.backup_before_edit.sh <file>"
    exit 1
fi

if [ ! -f "$FILE" ]; then
    echo "Error: File not found: $FILE"
    exit 1
fi

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BASENAME=$(basename "$FILE")
BACKUP="backups/${BASENAME}.backup-${TIMESTAMP}"

cp "$FILE" "$BACKUP"
echo "✅ Backup created: $BACKUP"
echo "   To restore: cp $BACKUP $FILE"
