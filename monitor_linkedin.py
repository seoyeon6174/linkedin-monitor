#!/usr/bin/env python3
"""
LinkedIn 모니터 — 특정 사용자의 새 게시물을 감지하여 Slack으로 알림
저장된 세션 쿠키로 Playwright 브라우저를 띄워 프로필 activity 페이지를 확인합니다.

실행: python monitor_linkedin.py
"""

import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

# ==================== 설정 ====================
MONITOR_PROFILES = [
    # Add LinkedIn profiles to monitor, e.g.:
    # {"name": "Sam Altman", "url": "https://www.linkedin.com/in/samaltman/recent-activity/all/"},
]

SLACK_WEBHOOK_URL = os.getenv("LINKEDIN_SLACK_WEBHOOK", "")

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
SESSION_FILE = BASE_DIR / "session" / "linkedin_state.json"

KST = ZoneInfo("Asia/Seoul")
MIN_TEXT_LENGTH = 30

# 프로필 간 방문 딜레이 (초)
VISIT_DELAY_MIN = 5
VISIT_DELAY_MAX = 10


# ==================== 상태 관리 ====================
def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            print("  ⚠️  state.json 손상 - 초기화합니다")
    return {}


def save_state(state):
    state["_updated_at"] = datetime.now(KST).isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ==================== LinkedIn 게시물 파싱 ====================
def parse_posts(page):
    """현재 페이지에서 게시물 목록을 파싱합니다."""
    posts = []

    # 게시물 컨테이너 찾기 — LinkedIn은 여러 클래스명 변형을 사용
    post_elements = page.query_selector_all("div.feed-shared-update-v2[data-urn]")

    # 폴백: data-urn이 없는 경우 다른 셀렉터 시도
    if not post_elements:
        post_elements = page.query_selector_all("div[data-urn^='urn:li:activity']")

    for el in post_elements:
        try:
            post = _parse_single_post(el)
            if post:
                posts.append(post)
        except Exception as e:
            print(f"  ⚠️  게시물 파싱 오류: {e}")
            continue

    return posts


def _parse_single_post(el):
    """단일 게시물 요소에서 데이터를 추출합니다."""
    # ID 추출 (data-urn 속성)
    urn = el.get_attribute("data-urn") or ""
    # urn:li:activity:1234567890 형태에서 숫자 부분 추출
    activity_match = re.search(r"activity:(\d+)", urn)
    post_id = activity_match.group(1) if activity_match else urn

    if not post_id:
        return None

    # 텍스트 본문 추출
    text = ""
    text_el = el.query_selector(
        "span.break-words span[dir='ltr'], "
        "div.feed-shared-text span.break-words, "
        "div.update-components-text span.break-words"
    )
    if text_el:
        text = text_el.inner_text().strip()

    # 좋아요 수 추출
    like_count = 0
    like_el = el.query_selector(
        "span.social-details-social-counts__reactions-count, "
        "button[aria-label*='reaction'] span, "
        "span.reactions-count"
    )
    if like_el:
        like_text = like_el.inner_text().strip().replace(",", "")
        try:
            like_count = int(like_text)
        except ValueError:
            pass

    # 타임스탬프 추출
    timestamp = ""
    time_el = el.query_selector(
        "span.update-components-actor__sub-description span[aria-hidden='true'], "
        "time, "
        "span.feed-shared-actor__sub-description span[aria-hidden='true']"
    )
    if time_el:
        timestamp = time_el.inner_text().strip()

    # 게시물 링크
    permalink = (
        f"https://www.linkedin.com/feed/update/urn:li:activity:{post_id}/"
        if activity_match
        else ""
    )

    return {
        "id": post_id,
        "text": text,
        "like_count": like_count,
        "timestamp": timestamp,
        "permalink": permalink,
    }


# ==================== 세션 검증 ====================
def check_session_valid(page):
    """로그인 상태를 확인합니다. 리다이렉트 감지."""
    url = page.url
    if any(keyword in url for keyword in ["/login", "/authwall", "/checkpoint"]):
        return False
    return True


# ==================== Slack 알림 ====================
def send_slack_notification(posts, profile_name):
    """새 게시물을 Slack으로 알림합니다."""
    if not SLACK_WEBHOOK_URL:
        print("  ⚠️  SLACK_WEBHOOK_URL 미설정 - 콘솔 출력만 합니다")
        for p in posts:
            print(f"  📝 {profile_name}: {p['text'][:100]}")
        return

    for post in posts:
        text_preview = post["text"][:500] + ("..." if len(post["text"]) > 500 else "")

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*LinkedIn {profile_name}*\n{text_preview if text_preview else '(미디어 게시물)'}",
                },
            },
        ]

        context_parts = []
        if post["like_count"]:
            context_parts.append(f"likes {post['like_count']:,}")
        if post["timestamp"]:
            context_parts.append(post["timestamp"])
        if post["permalink"]:
            context_parts.append(f"<{post['permalink']}|View>")

        if context_parts:
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": " · ".join(context_parts)}],
                }
            )

        payload = {
            "text": f"New LinkedIn post from {profile_name}: {post['text'][:100]}",
            "blocks": blocks,
        }

        try:
            resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
            resp.raise_for_status()
            print(f"  ✅ Slack 알림 전송: post {post['id']}")
        except requests.exceptions.RequestException as e:
            print(f"  ❌ Slack 전송 실패: {e}")


def send_error_notification(error_msg):
    """에러를 Slack으로 알림합니다."""
    if not SLACK_WEBHOOK_URL:
        return
    payload = {
        "text": f"LinkedIn Monitor Error: {error_msg}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*LinkedIn Monitor Error*\n```{error_msg}```",
                },
            },
        ],
    }
    try:
        requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
    except requests.exceptions.RequestException:
        pass


# ==================== 프로필 확인 ====================
def check_profile(page, profile, state):
    """단일 프로필의 새 게시물을 확인합니다."""
    name = profile["name"]
    url = profile["url"]
    # URL slug을 state_key로 사용 (한글 이름은 모두 '_'로 변환되어 충돌)
    slug_match = re.search(r"/in/([^/]+)", url)
    state_key = (
        slug_match.group(1) if slug_match else re.sub(r"[^a-zA-Z0-9]", "_", name)
    )

    print(f"  🔍 {name} 확인 중...")

    page.goto(url, wait_until="domcontentloaded")

    # 세션 유효성 확인
    if not check_session_valid(page):
        raise RuntimeError(
            f"세션 만료됨 - setup_session.py를 다시 실행하세요 (URL: {page.url})"
        )

    # 페이지 로딩 대기 — 게시물이 나타날 때까지
    try:
        page.wait_for_selector(
            "div.feed-shared-update-v2, div[data-urn^='urn:li:activity']",
            timeout=15000,
        )
    except Exception:
        print(f"  ⚠️  게시물 로딩 타임아웃 - 게시물이 없거나 셀렉터 변경됨")
        return state

    # 약간의 스크롤로 추가 로딩 트리거
    page.evaluate("window.scrollBy(0, 500)")
    time.sleep(2)

    posts = parse_posts(page)

    if not posts:
        print("  ℹ️  게시물 없음 또는 파싱 실패")
        return state

    print(f"  📊 {len(posts)}개 게시물 발견")

    # 새 게시물 필터링
    last_seen_id = state.get(f"{state_key}_last_id")
    if last_seen_id:
        new_posts = []
        found_last = False
        for p in posts:
            if p["id"] == last_seen_id:
                found_last = True
                break
            new_posts.append(p)
        # last_seen_id가 페이지에 없으면 알림 폭탄 방지
        if not found_last and len(new_posts) > 5:
            print(f"  ⚠️  last_seen_id가 페이지에 없음 - 최신 3개만 알림")
            new_posts = new_posts[:3]
    else:
        # 첫 실행: 최신 1개만 알림
        new_posts = posts[:1] if posts else []
        print("  ℹ️  첫 실행 - 최신 1개만 알림")

    # 짧은 포스트 필터 (30자 미만 제외)
    notify_posts = [p for p in new_posts if len(p["text"]) >= MIN_TEXT_LENGTH]

    if notify_posts:
        skipped = len(new_posts) - len(notify_posts)
        skip_msg = f" (짧은 포스트 {skipped}개 제외)" if skipped else ""
        print(f"  🆕 새 게시물 {len(notify_posts)}개 알림!{skip_msg}")
        send_slack_notification(notify_posts, name)
    else:
        print("  ℹ️  새 게시물 없음")

    # 가장 최근 게시물 ID 저장
    if posts:
        state[f"{state_key}_last_id"] = posts[0]["id"]

    return state


# ==================== 메인 ====================
def main():
    now = datetime.now(KST)
    print(f"🚀 LinkedIn Monitor [{now.strftime('%Y-%m-%d %H:%M:%S')}]")

    # 세션 파일 확인
    if not SESSION_FILE.exists():
        print("❌ 세션 파일이 없습니다. setup_session.py를 먼저 실행하세요")
        send_error_notification("세션 파일 없음 - setup_session.py 실행 필요")
        sys.exit(1)

    try:
        state = load_state()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                storage_state=str(SESSION_FILE),
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            for i, profile in enumerate(MONITOR_PROFILES):
                try:
                    state = check_profile(page, profile, state)
                except RuntimeError as e:
                    # 세션 만료 — 알림 후 즉시 종료
                    print(f"  ❌ {e}")
                    send_error_notification(str(e))
                    browser.close()
                    sys.exit(1)
                except Exception as e:
                    print(f"  ❌ {profile['name']} 오류: {e}")
                    send_error_notification(f"{profile['name']}: {e}")

                # 프로필 간 랜덤 딜레이
                if i < len(MONITOR_PROFILES) - 1:
                    delay = random.uniform(VISIT_DELAY_MIN, VISIT_DELAY_MAX)
                    print(f"  ⏳ {delay:.1f}초 대기...")
                    time.sleep(delay)

            browser.close()

        save_state(state)
        print("✅ 완료")

    except Exception as e:
        print(f"❌ 치명적 오류: {e}")
        send_error_notification(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
