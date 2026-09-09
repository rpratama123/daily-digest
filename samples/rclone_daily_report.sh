#!/bin/bash

# ==========================================
# Telegram Configuration
# ==========================================
# ⚠️ REPLACE THESE AFTER REGENERATING YOUR TOKEN VIA BOTFATHER
BOT_TOKEN=""
CHAT_ID=""
TELEGRAM_API_URL="https://api.telegram.org/bot${BOT_TOKEN}/sendMessage"

# ==========================================
# Script Configuration
# ==========================================
LOG_DIR="/mnt/user/mediaserver/downloads"
LOG_FILES=(
    "rclone-appdata-backup.log"
    "rclone-cikopopup-backup.log"
    "rclone-photos-backup.log"
    "rclone-cikonet-backup.log"
    "rclone-paperlessngx-backup.log"
    "rclone-stash-backup.log"
)

# Date format matches rclone log output (YYYY/MM/DD)
TODAY=$(date +%Y/%m/%d)
MESSAGE="📊 *Rclone Backup Report ($TODAY)*"

for log in "${LOG_FILES[@]}"; do
    FILE_PATH="${LOG_DIR}/${log}"
    
    # Format name visually: "rclone-appdata-backup.log" -> "Appdata Backup"
    PRETTY_NAME=$(echo "$log" | sed -E 's/rclone-(.*)-backup\.log/\1/' | awk '{for(i=1;i<=NF;i++)sub(/./,toupper(substr($i,1,1)),$i)}1')
    PRETTY_NAME="${PRETTY_NAME} Backup"
    
    MESSAGE="${MESSAGE}

📁 *${PRETTY_NAME}*"

    if [ ! -f "$FILE_PATH" ]; then
        MESSAGE="${MESSAGE}
⚠️ Log file not found."
        continue
    fi

    # Extract ONLY today's log entries. 
    # Because rclone's "Transferred:" lines don't have a date prefix, this AWK command
    # toggles capture ON when it sees today's date, and OFF if it sees a different date.
    TODAY_LOG=$(awk -v date="$TODAY" '
    /^[0-9]{4}\/[0-9]{2}\/[0-9]{2}/ {
        if (index($0, date) == 1) {
            flag=1
        } else {
            flag=0
        }
    }
    flag {print}
    ' "$FILE_PATH")

    # Evaluate today's log
    if [ -z "$TODAY_LOG" ]; then
        MESSAGE="${MESSAGE}
⚠️ No log entries found for today."
    elif echo "$TODAY_LOG" | grep -q "There was nothing to transfer"; then
        MESSAGE="${MESSAGE}
✅ No data transferred."
    else
        # Find the final "Transferred: <amount> / <total>" line
        LAST_TRANSFERRED=$(echo "$TODAY_LOG" | awk '/^Transferred:.*(B \/|B,)/ {last=$0} END {print last}')
        
        if [ -n "$LAST_TRANSFERRED" ]; then
            # Extract exactly the data amount transferred (e.g., "42.407 MiB" or "1.402 GiB")
            DATA_AMOUNT=$(echo "$LAST_TRANSFERRED" | awk -F',' '{print $1}' | sed -E 's/^Transferred:[ \t]*//' | awk -F' / ' '{print $1}')
            MESSAGE="${MESSAGE}
✅ Data transferred: ${DATA_AMOUNT}"
        else
            MESSAGE="${MESSAGE}
⚠️ Backup ran, but transfer size is unknown."
        fi
    fi
done

# Send the compiled message to Telegram safely
curl -s -X POST "$TELEGRAM_API_URL" \
    -d chat_id="$CHAT_ID" \
    -d parse_mode="Markdown" \
    --data-urlencode text="$MESSAGE" > /dev/null

echo "Telegram report successfully sent!"