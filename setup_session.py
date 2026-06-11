#!/usr/bin/env python3
"""
LinkedIn 세션 설정 — 최초 1회 실행
브라우저를 GUI 모드로 열어 수동 로그인 후 세션을 저장합니다.

사용법:
    source venv/bin/activate
    python setup_session.py
"""

import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

# 라인 버퍼링 강제 — 백그라운드 실행 시에도 즉시 보이도록
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

SESSION_DIR = Path(__file__).parent / "session"
SESSION_FILE = SESSION_DIR / "linkedin_state.json"

LOGIN_TIMEOUT_SECONDS = 300  # 로그인 대기 최대 5분
POLL_INTERVAL = 2
STABILIZE_SECONDS = 3        # li_at 감지 후 안정화 대기


def has_li_at(cookies):
    """LinkedIn 인증 쿠키 li_at 존재 여부."""
    return any(c.get("name") == "li_at" and c.get("value") for c in cookies)


def main():
    SESSION_DIR.mkdir(exist_ok=True)

    print("🔐 LinkedIn 세션 설정", flush=True)
    print("=" * 50, flush=True)
    print("1. 브라우저가 열립니다", flush=True)
    print("2. LinkedIn에 직접 로그인하세요 (2FA 포함)", flush=True)
    print(f"3. li_at 쿠키 발급 시 자동 감지 (최대 {LOGIN_TIMEOUT_SECONDS//60}분)", flush=True)
    print("=" * 50, flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto("https://www.linkedin.com/login")

        deadline = time.time() + LOGIN_TIMEOUT_SECONDS
        last_url = None
        detected = False
        while time.time() < deadline:
            try:
                url = page.url
                cookies = context.cookies()
            except Exception:
                print("❌ 브라우저 창이 닫혔습니다", flush=True)
                return

            if url != last_url:
                print(f"  ↪︎ {url}", flush=True)
                last_url = url

            if has_li_at(cookies):
                print(f"✓ li_at 쿠키 감지 — {STABILIZE_SECONDS}초 안정화 대기", flush=True)
                time.sleep(STABILIZE_SECONDS)
                detected = True
                break
            time.sleep(POLL_INTERVAL)

        if not detected:
            print(f"⚠️  {LOGIN_TIMEOUT_SECONDS}초 내 li_at 미감지 — 그래도 현재 상태 저장 시도", flush=True)

        try:
            print(f"✅ 최종 URL: {page.url}", flush=True)
            context.storage_state(path=str(SESSION_FILE))
            print(f"💾 세션 저장 완료: {SESSION_FILE}", flush=True)
        except Exception as e:
            print(f"❌ 세션 저장 실패: {e}", flush=True)
            sys.exit(1)
        finally:
            browser.close()

    print("\n🎉 설정 완료! 이제 monitor_linkedin.py를 실행할 수 있습니다", flush=True)


if __name__ == "__main__":
    main()
