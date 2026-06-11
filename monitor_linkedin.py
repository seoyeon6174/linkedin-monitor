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
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ==================== 설정 ====================
MONITOR_PROFILES = [
    # Add LinkedIn profiles to monitor, e.g.:
    # {"name": "Sam Altman", "url": "https://www.linkedin.com/in/samaltman/recent-activity/all/"},
]

SLACK_WEBHOOK_URL = os.getenv("LINKEDIN_SLACK_WEBHOOK", "")
DISCORD_WEBHOOK_THREADS = os.getenv("DISCORD_WEBHOOK_THREADS", "")
DISCORD_WEBHOOK_ERRORS = os.getenv("DISCORD_WEBHOOK_ERRORS", "")

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
SESSION_FILE = BASE_DIR / "session" / "linkedin_state.json"

KST = ZoneInfo("Asia/Seoul")
MIN_TEXT_LENGTH = 30
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M"
MAX_POSTS_TO_CRAWL = 8
SEEN_IDS_LIMIT = 8
RECENT_HOURS = 24
PARSE_SCROLL_STEP = 800
PARSE_SCROLL_ATTEMPTS = 3

# 프로필 간 방문 딜레이 (초)
VISIT_DELAY_MIN = 5
VISIT_DELAY_MAX = 10

# page.goto 재시도 설정
GOTO_TIMEOUT_MS = 45000  # 45s (기본 30s → 여유 확보)
GOTO_MAX_RETRIES = 2  # 최대 3회 시도
GOTO_RETRY_DELAYS = [5, 10]  # 재시도 간 대기 (초)


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


def is_dry_run():
    """DRY_RUN 모드 여부를 반환합니다."""
    value = os.getenv("LINKEDIN_DRY_RUN", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_profile_state_key(profile):
    """프로필 URL slug를 상태 키 접두사로 사용합니다."""
    slug_match = re.search(r"/in/([^/]+)", profile["url"])
    if slug_match:
        return slug_match.group(1)
    return re.sub(r"[^a-zA-Z0-9]", "_", profile["name"])


def normalize_seen_ids(raw_ids):
    """state에서 읽은 seen_ids를 문자열 리스트(중복 제거)로 정규화합니다."""
    if not isinstance(raw_ids, list):
        return []
    normalized = []
    seen = set()
    for item in raw_ids:
        if not item:
            continue
        item_str = str(item)
        if item_str in seen:
            continue
        seen.add(item_str)
        normalized.append(item_str)
    return normalized


def merge_seen_ids(observed_ids, existing_ids, limit=SEEN_IDS_LIMIT):
    """관측된 ID + 기존 ID를 합쳐 최신 우선으로 seen_ids를 유지합니다."""
    merged = []
    seen = set()
    for post_id in observed_ids + existing_ids:
        if not post_id or post_id in seen:
            continue
        seen.add(post_id)
        merged.append(post_id)
        if len(merged) >= limit:
            break
    return merged


def parse_absolute_timestamp(timestamp_str):
    """여러 절대 시간 형식을 지원합니다: YYYY-MM-DD HH:MM, YYYY-MM-DD HH:MM:SS, 기타 변형."""
    if not timestamp_str:
        return None

    # 지원하는 형식들 (우선순위 순)
    formats = [
        TIMESTAMP_FORMAT,  # 기본: 2024-05-15 14:30
        "%Y-%m-%d %H:%M:%S",  # 초 포함: 2024-05-15 14:30:45
        "%Y년 %m월 %d일 %H:%M",  # 한국식: 2024년 05월 15일 14:30
        "%Y-%m-%d",  # 날짜만: 2024-05-15
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(timestamp_str.strip(), fmt)
            return parsed.replace(tzinfo=KST)
        except ValueError:
            continue

    return None


# ==================== LinkedIn 게시물 파싱 ====================
def parse_posts(page, limit=MAX_POSTS_TO_CRAWL):
    """현재 페이지에서 게시물 목록을 파싱합니다."""
    posts_by_id = {}

    for attempt in range(PARSE_SCROLL_ATTEMPTS):
        # 게시물 컨테이너 찾기 — LinkedIn은 여러 클래스명 변형을 사용
        post_elements = page.query_selector_all("div.feed-shared-update-v2[data-urn]")

        # 폴백: data-urn이 없는 경우 다른 셀렉터 시도
        if not post_elements:
            post_elements = page.query_selector_all("div[data-urn^='urn:li:activity']")

        for el in post_elements:
            try:
                post = _parse_single_post(el)
                if post and post["id"] not in posts_by_id:
                    posts_by_id[post["id"]] = post
                    if len(posts_by_id) >= limit:
                        return list(posts_by_id.values())[:limit]
            except Exception as e:
                print(f"  ⚠️  게시물 파싱 오류: {e}")
                continue

        if attempt < PARSE_SCROLL_ATTEMPTS - 1:
            page.evaluate(f"window.scrollBy(0, {PARSE_SCROLL_STEP})")
            time.sleep(1)

    return list(posts_by_id.values())[:limit]


def convert_relative_timestamp(relative_str):
    """LinkedIn 상대시간(예: '6h', '2d', '1w')을 KST 절대시간 문자열로 변환."""
    now = datetime.now(KST)

    # "now" / "방금"
    if relative_str.strip().lower() in ("now", "방금"):
        return now.strftime(TIMESTAMP_FORMAT)

    # 영어 약어: "30m", "6h", "2d", "1w", "3mo", "1yr"
    en_match = re.match(r"(\d+)\s*(mo|yr|m|h|d|w)\b", relative_str.strip())
    # 한국어: "30분 전", "6시간 전", "2일 전", "1주 전", "3개월 전", "1년 전"
    ko_match = re.match(r"(\d+)\s*(분|시간|일|주|개월|년)", relative_str.strip())

    match = en_match or ko_match
    if not match:
        return relative_str  # fallback: 원본 그대로

    value = int(match.group(1))
    unit = match.group(2)

    unit_map = {
        "m": "minutes",
        "분": "minutes",
        "h": "hours",
        "시간": "hours",
        "d": "days",
        "일": "days",
        "w": "weeks",
        "주": "weeks",
        "mo": "months",
        "개월": "months",
        "yr": "years",
        "년": "years",
    }

    key = unit_map.get(unit)
    if not key:
        return relative_str

    if key == "months":
        delta = timedelta(days=value * 30)
    elif key == "years":
        delta = timedelta(days=value * 365)
    elif key == "weeks":
        delta = timedelta(weeks=value)
    else:
        delta = timedelta(**{key: value})

    return (now - delta).strftime(TIMESTAMP_FORMAT)


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
        "span.social-details-social-counts__social-proof-fallback-number, "
        "span.social-details-social-counts__reactions-count, "
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
    raw_timestamp = ""
    time_el = el.query_selector(
        "span.update-components-actor__sub-description, "
        "span.feed-shared-actor__sub-description, "
        "time"
    )
    if time_el:
        raw_timestamp = time_el.inner_text().strip().split(chr(10))[0].strip()
        timestamp = convert_relative_timestamp(raw_timestamp)

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
        "raw_timestamp": raw_timestamp,
        "permalink": permalink,
    }


# ==================== 세션 검증 ====================
# 게스트 공개 프로필로 보내질 때 LinkedIn이 사용하는 로케일 서브도메인 패턴.
# 인증 사용자에겐 항상 www.linkedin.com을 서빙함.
GUEST_LOCALE_HOST_RE = re.compile(
    r"^https?://(?!www\.)[a-z]{2,3}\.linkedin\.com/", re.IGNORECASE
)

# 인증된 페이지 어디에도 등장하지 않는 게스트 전용 마커.
# 로그인 풀려서 게스트로 떨어졌을 때 응답 HTML에 다수 등장.
GUEST_HTML_MARKERS = (
    "public_profile_guest_nav_menu",
    "contextual-sign-in-modal",
    "nav-header-signin",
)


def check_session_valid(page):
    """로그인 상태를 확인합니다. URL 기반 1차 판정.

    감지하는 케이스:
    - /login, /authwall, /checkpoint, /signup, /uas/ 리다이렉트
    - kr.linkedin.com 등 로케일 서브도메인(게스트 전용)
    """
    url = page.url
    if any(
        keyword in url
        for keyword in ["/login", "/authwall", "/checkpoint", "/signup", "/uas/"]
    ):
        return False
    if GUEST_LOCALE_HOST_RE.match(url):
        return False
    return True


def looks_like_guest_page(html):
    """페이지 HTML에 게스트 전용 마커가 있는지 검사. URL이 변하지 않는 soft block 감지용."""
    if not html:
        return False
    return any(marker in html for marker in GUEST_HTML_MARKERS)


# ==================== Slack 알림 ====================
def send_slack_notification(posts, profile_name):
    """새 게시물을 Slack으로 알림합니다."""
    if is_dry_run():
        for p in posts:
            print(f"  🧪 DRY_RUN 사용자 알림 스킵: {profile_name} / post {p['id']}")
        return

    if not SLACK_WEBHOOK_URL:
        print("  ⚠️  SLACK_WEBHOOK_URL 미설정 - 콘솔 출력만 합니다")
        for p in posts:
            print(f"  📝 {profile_name}: {p['text'][:100]}")
            send_discord_notification(p, profile_name)
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

        send_discord_notification(post, profile_name)


def send_discord_notification(post, profile_name):
    """단일 새 게시물을 Discord(#스레드)로 알림합니다. dual-send."""
    if is_dry_run():
        print(f"  🧪 DRY_RUN Discord 알림 스킵: {profile_name} / post {post['id']}")
        return
    if not DISCORD_WEBHOOK_THREADS:
        return
    text_preview = post["text"][:1500] + ("..." if len(post["text"]) > 1500 else "")
    context_parts = []
    if post.get("like_count"):
        context_parts.append(f"👍 {post['like_count']:,}")
    if post.get("timestamp"):
        context_parts.append(post["timestamp"])
    if post.get("permalink"):
        context_parts.append(f"[원문]({post['permalink']})")
    footer = " · ".join(context_parts)

    body = f"**LinkedIn — {profile_name}**\n{text_preview if text_preview else '(미디어 게시물)'}"
    if footer:
        body += f"\n_{footer}_"

    try:
        resp = requests.post(
            DISCORD_WEBHOOK_THREADS,
            json={"content": body[:1900]},
            timeout=10,
        )
        resp.raise_for_status()
        print(f"  ✅ Discord 알림 전송: post {post['id']}")
    except requests.exceptions.RequestException as e:
        print(f"  ❌ Discord 전송 실패: {e}")


def send_error_notification(error_msg):
    """에러를 Discord 에러 채널로 알림합니다 (DISCORD_WEBHOOK_ERRORS)."""
    if is_dry_run():
        print(f"  🧪 DRY_RUN 에러 알림 스킵: {error_msg}")
        return

    if not DISCORD_WEBHOOK_ERRORS:
        return
    body = f"⚠️ **LinkedIn Monitor Error**\n```{error_msg}```"
    try:
        requests.post(
            DISCORD_WEBHOOK_ERRORS,
            json={"content": body[:1900]},
            timeout=10,
        )
    except requests.exceptions.RequestException:
        pass


def navigate_with_retry(page, url):
    """page.goto를 재시도 로직과 함께 실행합니다."""
    last_error = None
    for attempt in range(1 + GOTO_MAX_RETRIES):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
            return
        except PlaywrightTimeout as e:
            last_error = e
            if attempt < GOTO_MAX_RETRIES:
                delay = GOTO_RETRY_DELAYS[attempt]
                print(
                    f"  ⚠️  page.goto 타임아웃 (시도 {attempt + 1}/{1 + GOTO_MAX_RETRIES}) - {delay}초 후 재시도..."
                )
                time.sleep(delay)
            else:
                print(f"  ❌ page.goto {1 + GOTO_MAX_RETRIES}회 시도 모두 실패")
    raise last_error


# ==================== 프로필 확인 ====================
def check_profile(page, profile, state):
    """단일 프로필의 새 게시물을 확인합니다."""
    name = profile["name"]
    url = profile["url"]
    state_key = get_profile_state_key(profile)
    last_id_key = f"{state_key}_last_id"
    seen_ids_key = f"{state_key}_seen_ids"
    last_seen_id = state.get(last_id_key)
    seen_ids = normalize_seen_ids(state.get(seen_ids_key))
    warmup_mode = not seen_ids

    print(f"  🔍 {name} 확인 중...")

    navigate_with_retry(page, url)

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
        # 셀렉터 못 찾았으니 (1) 진짜 게시물이 없거나, (2) 게스트로 떨어졌는데 URL이 안 바뀌었거나, (3) 셀렉터 변경.
        # HTML 마커로 게스트 케이스 감별.
        try:
            page_html = page.content()
        except Exception:
            page_html = ""
        if looks_like_guest_page(page_html):
            raise RuntimeError(
                f"세션 만료 감지(게스트 페이지) - setup_session.py 재실행 필요 "
                f"(profile={name}, URL: {page.url})"
            )
        print(f"  ⚠️  게시물 로딩 타임아웃 - 게시물이 없거나 셀렉터 변경됨")
        send_error_notification(
            f"⚠️ {name}: 게시물 로딩 타임아웃 (15초 초과)\n→ 보통 LinkedIn 서버 지연. 연속 발생 시 셀렉터 변경 확인"
        )
        return state

    # 약간의 스크롤로 추가 로딩 트리거
    page.evaluate("window.scrollBy(0, 500)")
    time.sleep(1)

    posts = parse_posts(page, limit=MAX_POSTS_TO_CRAWL)

    if not posts:
        print("  ℹ️  게시물 없음 또는 파싱 실패")
        send_error_notification(
            f"⚠️ {name}: 게시물 0개 파싱됨\n→ 페이지는 로딩됐으나 게시물 DOM 없음. 연속 시 셀렉터 점검"
        )
        return state

    print(f"  📊 {len(posts)}개 게시물 발견 (수집 상한 {MAX_POSTS_TO_CRAWL})")
    observed_ids = [post["id"] for post in posts]

    if warmup_mode:
        state[seen_ids_key] = merge_seen_ids(observed_ids, seen_ids, SEEN_IDS_LIMIT)
        state[last_id_key] = observed_ids[0]
        print("  ℹ️  첫 실행 워밍업 - 기준만 저장 (알림 없음)")
        return state

    anchor_missing = bool(last_seen_id and last_seen_id not in observed_ids)
    if anchor_missing:
        print("  ⚠️  last_seen_id 미발견 - 안전모드로 사용자 알림 생략")
        send_error_notification(
            f"⚠️ {name}: 기준 게시물(anchor) 미발견 — 안전모드 전환\n→ 수집 {len(observed_ids)}개 중 last_seen_id 없음. 사용자 알림 생략됨"
        )
        state[seen_ids_key] = merge_seen_ids(observed_ids, seen_ids, SEEN_IDS_LIMIT)
        state[last_id_key] = observed_ids[0]
        return state

    now = datetime.now(KST)
    notify_posts = []
    skipped_seen = 0
    skipped_short = 0
    skipped_old = 0
    skipped_unparsable = 0

    for post in posts:
        if post["id"] in seen_ids:
            skipped_seen += 1
            continue

        if len(post["text"]) < MIN_TEXT_LENGTH:
            skipped_short += 1
            continue

        post_dt = parse_absolute_timestamp(post["timestamp"])
        if not post_dt:
            skipped_unparsable += 1
            continue

        if now - post_dt > timedelta(hours=RECENT_HOURS):
            skipped_old += 1
            continue

        notify_posts.append(post)

    if skipped_unparsable:
        send_error_notification(
            f"⚠️ {name}: 시간 파싱 실패 {skipped_unparsable}건\n→ 새로운 시간 표기 형식 가능. parse_absolute_timestamp 업데이트 검토"
        )

    if notify_posts:
        print(
            f"  🆕 새 게시물 {len(notify_posts)}개 알림! "
            f"(seen 제외 {skipped_seen}, 짧은글 {skipped_short}, 24h 초과 {skipped_old})"
        )
        send_slack_notification(notify_posts, name)
    else:
        print(
            "  ℹ️  새 게시물 없음 "
            f"(seen 제외 {skipped_seen}, 짧은글 {skipped_short}, 24h 초과 {skipped_old}, 시간파싱불가 {skipped_unparsable})"
        )

    state[seen_ids_key] = merge_seen_ids(observed_ids, seen_ids, SEEN_IDS_LIMIT)
    state[last_id_key] = observed_ids[0]

    return state


# ==================== 메인 ====================
def main():
    now = datetime.now(KST)
    print(f"🚀 LinkedIn Monitor [{now.strftime('%Y-%m-%d %H:%M:%S')}]")
    if is_dry_run():
        print("🧪 DRY_RUN 모드 활성화 - Slack 전송 없이 동작합니다")

    # 세션 파일 확인
    if not SESSION_FILE.exists():
        print("❌ 세션 파일이 없습니다. setup_session.py를 먼저 실행하세요")
        send_error_notification("🚨 세션 파일 없음\n→ setup_session.py 실행 필요")
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
                    send_error_notification(
                        f"🚨 {profile['name']}: 예기치 않은 오류\n→ {e}"
                    )

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
        send_error_notification(f"🚨 치명적 오류로 모니터 중단\n→ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
