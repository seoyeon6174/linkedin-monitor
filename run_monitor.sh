#!/bin/bash
# LinkedIn Monitor — cron 래퍼
# cron에서 매시 :20에 호출 → 0~30분 랜덤 대기 후 실행
# 실제 실행 시점: :20~:50 사이

RANDOM_DELAY=$((RANDOM % 1800))
echo "$(date '+%Y-%m-%d %H:%M:%S') 🕐 ${RANDOM_DELAY}초 대기..."
sleep $RANDOM_DELAY

export LINKEDIN_SLACK_WEBHOOK="${LINKEDIN_SLACK_WEBHOOK:?Set LINKEDIN_SLACK_WEBHOOK before running}"

cd "$(dirname "$0")"
source venv/bin/activate
python monitor_linkedin.py
