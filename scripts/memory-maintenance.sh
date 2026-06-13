#!/bin/bash
# memory-maintenance wrapper
# Weekdays (Mon-Fri): 05:00-09:00, Weekends: 07:00-10:00 (Asia/Shanghai)
# Also checks idle > 2 hours

export TZ="Asia/Shanghai"
DAY=$(date +%u)   # 1=Monday, 7=Sunday
HOUR=$(date +%-H)

# Time window check
if [ "$DAY" -ge 1 ] && [ "$DAY" -le 5 ]; then
    # Weekday: 05-09
    if [ "$HOUR" -lt 5 ] || [ "$HOUR" -ge 9 ]; then
        exit 0
    fi
else
    # Weekend: 07-10
    if [ "$HOUR" -lt 7 ] || [ "$HOUR" -ge 10 ]; then
        exit 0
    fi
fi

# Idle check: last activity > 2 hours ago?
NOW=$(date +%s)
STATE_FILE=~/.hermes/profiles/nova/memory/.maintenance-state.json
AUDIT_FILE=~/.hermes/profiles/nova/memory/.audit.jsonl

LAST_EPOCH=""
if [ -f "$STATE_FILE" ]; then
    LAST_TS=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('last_run',''))" 2>/dev/null)
    if [ -n "$LAST_TS" ]; then
        LAST_EPOCH=$(python3 -c "from datetime import datetime; print(int(datetime.fromisoformat('$LAST_TS').timestamp()))" 2>/dev/null)
    fi
fi
if [ -f "$AUDIT_FILE" ]; then
    LAST_AUDIT=$(tail -1 "$AUDIT_FILE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ts',''))" 2>/dev/null)
    if [ -n "$LAST_AUDIT" ]; then
        AUDIT_EPOCH=$(python3 -c "from datetime import datetime; print(int(datetime.fromisoformat('$LAST_AUDIT').timestamp()))" 2>/dev/null)
        if [ -n "$AUDIT_EPOCH" ]; then
            LAST_EPOCH="$AUDIT_EPOCH"
        fi
    fi
fi
[ -z "$LAST_EPOCH" ] && LAST_EPOCH=$((NOW - 7200))

DIFF=$((NOW - LAST_EPOCH))
[ "$DIFF" -lt 7200 ] && exit 0

# In window and idle — run
python3 ~/.hermes/profiles/nova/scripts/memory-maintenance.py
exit $?
