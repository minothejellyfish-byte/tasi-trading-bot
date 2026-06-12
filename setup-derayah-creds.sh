#!/bin/bash
# =============================================================================
# Derayah Credentials Setup — One-time, manual run by Amin
# =============================================================================
# Creates ~/.derayah-creds with username + password for auto-recovery.
# Permissions: chmod 600 (only mino user can read).
# Format: JSON with username, password, otp_method ("email"|"sms")
# =============================================================================

set -euo pipefail

CREDS_FILE="$HOME/.derayah-creds"

if [[ -f "$CREDS_FILE" ]]; then
    echo "  ⚠️  $CREDS_FILE already exists. Backing up to ${CREDS_FILE}.bak"
    cp "$CREDS_FILE" "${CREDS_FILE}.bak"
fi

echo ""
echo "  Enter Derayah username (account number, e.g. 1023744 or 20638532):"
read -r USERNAME
echo "  Enter Derayah password:"
read -rs PASSWORD
echo ""
echo "  OTP delivery method: email or sms? (default: email)"
read -r OTP_METHOD
OTP_METHOD=${OTP_METHOD:-email}

cat > "$CREDS_FILE" <<EOF
{
  "username": "$USERNAME",
  "password": "$PASSWORD",
  "otp_method": "$OTP_METHOD",
  "created_at": "$(date -Iseconds)",
  "note": "Auto-recovery credentials for derayah_refresh_cron.sh. chmod 600."
}
EOF

chmod 600 "$CREDS_FILE"
echo "  ✅ $CREDS_FILE created with mode 600"
echo ""
echo "  Username: $USERNAME"
echo "  OTP: $OTP_METHOD"
echo ""
echo "  Test it: python3 -c \"import json; print(json.load(open('$CREDS_FILE'))['username'])\""
