#!/bin/bash
# LinkedIn Monitor — launchd/cron 래퍼
# 트리거 후 0~10분 랜덤 대기 후 실행 (예측 가능한 접근 패턴 회피)
# 실제 실행 시점: 트리거 시각 + 0~10분

RANDOM_DELAY=$((RANDOM % 601))
echo "$(date '+%Y-%m-%d %H:%M:%S') 🕐 ${RANDOM_DELAY}초 대기..."
sleep $RANDOM_DELAY

cd "$(dirname "$0")"

# 시크릿: 스크립트 옆 .env 파일 또는 환경 변수
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

source venv/bin/activate
python3 monitor_linkedin.py
