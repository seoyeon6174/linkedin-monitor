#!/usr/bin/env python3
"""
LinkedIn 세션 설정 — 최초 1회 실행
브라우저를 GUI 모드로 열어 수동 로그인 후 세션을 저장합니다.

사용법:
    source venv/bin/activate
    python setup_session.py
"""

from pathlib import Path
from playwright.sync_api import sync_playwright

SESSION_DIR = Path(__file__).parent / "session"
SESSION_FILE = SESSION_DIR / "linkedin_state.json"


def main():
    SESSION_DIR.mkdir(exist_ok=True)

    print("🔐 LinkedIn 세션 설정")
    print("=" * 50)
    print("1. 브라우저가 열립니다")
    print("2. LinkedIn에 직접 로그인하세요 (2FA 포함)")
    print("3. 로그인 완료 후 피드가 보이면 터미널에서 Enter를 누르세요")
    print("=" * 50)

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

        input("\n✅ 로그인 완료 후 Enter를 누르세요... ")

        # 로그인 확인: 피드 또는 프로필 요소 존재 여부
        url = page.url
        if "feed" in url or "mynetwork" in url or "in/" in url:
            print("✅ 로그인 확인됨")
        else:
            print(f"⚠️  현재 URL: {url}")
            print("   로그인이 완료되지 않았을 수 있습니다")
            confirm = input("   그래도 세션을 저장할까요? (y/N): ")
            if confirm.lower() != "y":
                print("❌ 취소됨")
                browser.close()
                return

        # 세션 저장
        context.storage_state(path=str(SESSION_FILE))
        print(f"💾 세션 저장 완료: {SESSION_FILE}")

        browser.close()

    print("\n🎉 설정 완료! 이제 monitor_linkedin.py를 실행할 수 있습니다")


if __name__ == "__main__":
    main()
