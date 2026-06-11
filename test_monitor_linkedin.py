"""linkedin_monitor core logic tests"""

import json
import re
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import monitor_linkedin as m

KST = ZoneInfo("Asia/Seoul")


# ==================== State management tests ====================
class TestStateManagement:
    """state.json load/save tests"""

    def test_load_missing_state(self, tmp_path):
        with patch.object(m, "STATE_FILE", tmp_path / "state.json"):
            assert m.load_state() == {}

    def test_load_valid_state(self, tmp_path):
        state_file = tmp_path / "state.json"
        state_file.write_text('{"janedoe_last_id": "123456"}')
        with patch.object(m, "STATE_FILE", state_file):
            state = m.load_state()
            assert state["janedoe_last_id"] == "123456"

    def test_load_corrupted_state(self, tmp_path):
        """Corrupted JSON should return empty dict"""
        state_file = tmp_path / "state.json"
        state_file.write_text("{broken json!!!")
        with patch.object(m, "STATE_FILE", state_file):
            assert m.load_state() == {}

    def test_save_state_adds_timestamp(self, tmp_path):
        state_file = tmp_path / "state.json"
        with patch.object(m, "STATE_FILE", state_file):
            m.save_state({"test_key": "value"})
            saved = json.loads(state_file.read_text())
            assert "_updated_at" in saved
            assert saved["test_key"] == "value"


# ==================== Session validation tests ====================
class TestSessionValidation:
    """check_session_valid() tests"""

    def test_valid_session(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/in/janedoe/recent-activity/all/"
        assert m.check_session_valid(page) is True

    def test_login_redirect_detected(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/login?trk=guest_homepage"
        assert m.check_session_valid(page) is False

    def test_authwall_redirect_detected(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/authwall?sessionRedirect=..."
        assert m.check_session_valid(page) is False

    def test_checkpoint_redirect_detected(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/checkpoint/challengePage"
        assert m.check_session_valid(page) is False

    def test_signup_redirect_detected(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/signup/cold-join"
        assert m.check_session_valid(page) is False

    def test_uas_redirect_detected(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/uas/login"
        assert m.check_session_valid(page) is False

    def test_locale_subdomain_guest_redirect_detected(self):
        """kr.linkedin.com 등 로케일 서브도메인 = 게스트 페이지로 떨어짐"""
        page = MagicMock()
        page.url = "https://kr.linkedin.com/in/janedoe"
        assert m.check_session_valid(page) is False

    def test_locale_subdomain_de_detected(self):
        page = MagicMock()
        page.url = "https://de.linkedin.com/in/someone"
        assert m.check_session_valid(page) is False

    def test_www_subdomain_still_valid(self):
        """www.linkedin.com은 인증 사용자에게 정상 — 로케일 정규식에 안 걸려야 함"""
        page = MagicMock()
        page.url = "https://www.linkedin.com/in/janedoe/recent-activity/all/"
        assert m.check_session_valid(page) is True


class TestLooksLikeGuestPage:
    """soft block 감지 — URL은 그대로지만 콘텐츠가 게스트 마크업"""

    def test_guest_markers_detected(self):
        html = '<a class="..." data-tracking-control-name="public_profile_guest_nav_menu_topContent">'
        assert m.looks_like_guest_page(html) is True

    def test_signin_modal_detected(self):
        html = '<div class="contextual-sign-in-modal top-card__logo-modal">'
        assert m.looks_like_guest_page(html) is True

    def test_nav_signin_detected(self):
        html = '<a data-tracking-control-name="public_profile_nav-header-signin">'
        assert m.looks_like_guest_page(html) is True

    def test_authenticated_html_not_guest(self):
        html = '<div class="feed-shared-update-v2"><div data-urn="urn:li:activity:123">post</div></div>'
        assert m.looks_like_guest_page(html) is False

    def test_empty_html_not_guest(self):
        assert m.looks_like_guest_page("") is False
        assert m.looks_like_guest_page(None) is False


# ==================== navigate_with_retry tests ====================
class TestNavigateWithRetry:
    """Retry logic tests"""

    @patch("monitor_linkedin.time.sleep")
    def test_success_on_first_try(self, mock_sleep):
        page = MagicMock()
        m.navigate_with_retry(page, "https://linkedin.com/test")
        page.goto.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("monitor_linkedin.time.sleep")
    def test_success_on_retry(self, mock_sleep):
        """Should succeed after one failed attempt"""
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        page = MagicMock()
        page.goto.side_effect = [PlaywrightTimeout("timeout"), None]
        m.navigate_with_retry(page, "https://linkedin.com/test")
        assert page.goto.call_count == 2

    @patch("monitor_linkedin.time.sleep")
    def test_all_retries_exhausted(self, mock_sleep):
        """Should raise after all retries fail"""
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        page = MagicMock()
        page.goto.side_effect = PlaywrightTimeout("timeout")
        with pytest.raises(PlaywrightTimeout):
            m.navigate_with_retry(page, "https://linkedin.com/test")
        assert page.goto.call_count == 1 + m.GOTO_MAX_RETRIES


# ==================== Slack notification tests ====================
class TestSlackNotification:
    """Slack notification formatting tests"""

    @patch.object(
        m, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T000/B000/test"
    )
    @patch("monitor_linkedin.is_dry_run", return_value=False)
    @patch("monitor_linkedin.requests.post")
    def test_post_with_all_fields(self, mock_post, _mock_dry_run):
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        posts = [
            {
                "id": "12345",
                "text": "오늘 좋은 인사이트를 공유합니다." * 3,
                "like_count": 42,
                "timestamp": "2시간 전",
                "permalink": "https://www.linkedin.com/feed/update/urn:li:activity:12345/",
            }
        ]
        m.send_slack_notification(posts, "테스트유저")
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        assert "테스트유저" in payload["blocks"][0]["text"]["text"]

    @patch.object(
        m, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T000/B000/test"
    )
    @patch("monitor_linkedin.is_dry_run", return_value=False)
    @patch("monitor_linkedin.requests.post")
    def test_post_without_likes(self, mock_post, _mock_dry_run):
        """Posts with 0 likes should not show like count in context"""
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        posts = [
            {
                "id": "99",
                "text": "짧지만 알림 가능한 텍스트입니다." * 2,
                "like_count": 0,
                "timestamp": "1시간 전",
                "permalink": "",
            }
        ]
        m.send_slack_notification(posts, "유저")
        payload = mock_post.call_args[1]["json"]
        # context block should not contain "likes" since count is 0
        if len(payload["blocks"]) > 1:
            context_text = payload["blocks"][1]["elements"][0]["text"]
            assert "likes" not in context_text

    @patch.object(m, "SLACK_WEBHOOK_URL", "")
    @patch.object(
        m, "DISCORD_WEBHOOK_THREADS", "https://discord.com/api/webhooks/000/test"
    )
    @patch("monitor_linkedin.is_dry_run", return_value=False)
    @patch("monitor_linkedin.requests.post")
    def test_discord_sent_even_without_slack(self, mock_post, _mock_dry_run):
        """Slack webhook 미설정이어도 Discord 알림은 독립적으로 전송"""
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        posts = [
            {
                "id": "77",
                "text": "Discord 단독 알림 경로 검증용 텍스트입니다." * 2,
                "like_count": 3,
                "timestamp": "1시간 전",
                "permalink": "",
            }
        ]
        m.send_slack_notification(posts, "유저")
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == "https://discord.com/api/webhooks/000/test"


# ==================== Error notification tests ====================
class TestErrorNotification:
    """Error notification tests"""

    @patch.object(
        m, "DISCORD_WEBHOOK_ERRORS", "https://discord.com/api/webhooks/000/test"
    )
    @patch("monitor_linkedin.is_dry_run", return_value=False)
    @patch("monitor_linkedin.requests.post")
    def test_error_sent_to_dev_webhook(self, mock_post, _mock_dry_run):
        m.send_error_notification("test error")
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        assert "Error" in payload["content"]

    @patch.object(
        m, "DISCORD_WEBHOOK_ERRORS", "https://discord.com/api/webhooks/000/test"
    )
    @patch("monitor_linkedin.is_dry_run", return_value=False)
    @patch("monitor_linkedin.requests.post")
    def test_error_network_failure_silent(self, mock_post, _mock_dry_run):
        """Network failure should not propagate"""
        import requests as req

        mock_post.side_effect = req.exceptions.RequestException("network down")
        m.send_error_notification("test error")  # should not raise


# ==================== Profile state key tests ====================
class TestProfileStateKey:
    """URL slug extraction for state key"""

    def test_slug_extracted_from_url(self):
        profile = {
            "name": "Jane Doe",
            "url": "https://www.linkedin.com/in/janedoe/recent-activity/all/",
        }
        assert m.get_profile_state_key(profile) == "janedoe"

    def test_name_fallback_when_no_slug(self):
        """URL에 /in/ slug가 없으면 이름을 영숫자로 치환해 키로 사용"""
        profile = {"name": "Jane Doe", "url": "https://www.linkedin.com/feed/"}
        assert m.get_profile_state_key(profile) == "Jane_Doe"

    def test_all_profiles_have_valid_slugs(self):
        for profile in m.MONITOR_PROFILES:
            match = re.search(r"/in/([^/]+)", profile["url"])
            assert match is not None, f"No slug in {profile['url']}"


class TestSeenIdsHelpers:
    """seen_ids helper tests"""

    def test_merge_seen_ids_keeps_latest_and_limit(self):
        merged = m.merge_seen_ids(
            observed_ids=["4", "3", "2"],
            existing_ids=["2", "1", "0"],
            limit=4,
        )
        assert merged == ["4", "3", "2", "1"]

    def test_normalize_seen_ids_handles_invalid_values(self):
        assert m.normalize_seen_ids(None) == []
        assert m.normalize_seen_ids(["1", "", None, "1", 2]) == ["1", "2"]

    def test_parse_absolute_timestamp(self):
        ts = (datetime.now(KST) - timedelta(hours=1)).strftime(m.TIMESTAMP_FORMAT)
        parsed = m.parse_absolute_timestamp(ts)
        assert parsed is not None
        assert parsed.tzinfo == KST
        assert m.parse_absolute_timestamp("Edited · 6h") is None


class TestCheckProfileLogic:
    """check_profile() policy tests"""

    def _profile(self):
        return {
            "name": "Jane Doe",
            "url": "https://www.linkedin.com/in/johndoe/recent-activity/all/",
        }

    def _mock_page(self):
        page = MagicMock()
        page.url = "https://www.linkedin.com/in/johndoe/recent-activity/all/"
        return page

    def _post(self, post_id, hours_ago=1, text_len=40, timestamp=None):
        if timestamp is None:
            timestamp = (datetime.now(KST) - timedelta(hours=hours_ago)).strftime(
                m.TIMESTAMP_FORMAT
            )
        return {
            "id": post_id,
            "text": "가" * text_len,
            "like_count": 1,
            "timestamp": timestamp,
            "raw_timestamp": timestamp,
            "permalink": f"https://www.linkedin.com/feed/update/urn:li:activity:{post_id}/",
        }

    @patch("monitor_linkedin.send_slack_notification")
    @patch("monitor_linkedin.send_error_notification")
    @patch("monitor_linkedin.parse_posts")
    @patch("monitor_linkedin.navigate_with_retry")
    def test_warmup_mode_stores_state_without_alert(
        self,
        _mock_nav,
        mock_parse_posts,
        mock_error,
        mock_slack,
    ):
        page = self._mock_page()
        profile = self._profile()
        state = {}
        mock_parse_posts.return_value = [self._post("1001"), self._post("1000")]

        result = m.check_profile(page, profile, state)

        assert result["johndoe_last_id"] == "1001"
        assert result["johndoe_seen_ids"] == ["1001", "1000"]
        mock_slack.assert_not_called()
        mock_error.assert_not_called()

    @patch("monitor_linkedin.send_slack_notification")
    @patch("monitor_linkedin.send_error_notification")
    @patch("monitor_linkedin.parse_posts")
    @patch("monitor_linkedin.navigate_with_retry")
    def test_anchor_missing_enters_safe_mode(
        self,
        _mock_nav,
        mock_parse_posts,
        mock_error,
        mock_slack,
    ):
        page = self._mock_page()
        profile = self._profile()
        state = {
            "johndoe_last_id": "9999",
            "johndoe_seen_ids": ["9999", "9998"],
        }
        mock_parse_posts.return_value = [self._post("1002"), self._post("1001")]

        result = m.check_profile(page, profile, state)

        mock_slack.assert_not_called()
        mock_error.assert_called_once()
        assert "기준 게시물(anchor) 미발견" in mock_error.call_args[0][0]
        assert result["johndoe_last_id"] == "1002"
        assert result["johndoe_seen_ids"] == ["1002", "1001", "9999", "9998"]

    @patch("monitor_linkedin.send_slack_notification")
    @patch("monitor_linkedin.send_error_notification")
    @patch("monitor_linkedin.parse_posts")
    @patch("monitor_linkedin.navigate_with_retry")
    def test_recent_guard_only_notifies_recent_unseen_posts(
        self,
        _mock_nav,
        mock_parse_posts,
        mock_error,
        mock_slack,
    ):
        page = self._mock_page()
        profile = self._profile()
        state = {
            "johndoe_last_id": "anchor",
            "johndoe_seen_ids": ["anchor"],
        }
        mock_parse_posts.return_value = [
            self._post("new_recent", hours_ago=1),
            self._post("anchor", hours_ago=2),
            self._post("old_post", hours_ago=30),
        ]

        m.check_profile(page, profile, state)

        mock_error.assert_not_called()
        mock_slack.assert_called_once()
        notified_posts, notified_profile = mock_slack.call_args[0]
        assert notified_profile == "Jane Doe"
        assert [p["id"] for p in notified_posts] == ["new_recent"]

    @patch("monitor_linkedin.send_slack_notification")
    @patch("monitor_linkedin.send_error_notification")
    @patch("monitor_linkedin.parse_posts")
    @patch("monitor_linkedin.navigate_with_retry")
    def test_unparsable_timestamp_is_skipped_and_warned(
        self,
        _mock_nav,
        mock_parse_posts,
        mock_error,
        mock_slack,
    ):
        page = self._mock_page()
        profile = self._profile()
        state = {
            "johndoe_last_id": "anchor",
            "johndoe_seen_ids": ["anchor"],
        }
        bad_post = self._post("bad_ts", timestamp="Edited · 6h")
        mock_parse_posts.return_value = [bad_post, self._post("anchor", hours_ago=2)]

        m.check_profile(page, profile, state)

        mock_slack.assert_not_called()
        mock_error.assert_called_once()
        assert "시간 파싱 실패" in mock_error.call_args[0][0]


# ==================== Relative timestamp conversion tests ====================
class TestConvertRelativeTimestamp:
    """상대시간 → 절대시간 변환 테스트"""

    def test_hours(self):
        result = m.convert_relative_timestamp("6h")
        expected = (datetime.now(KST) - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")
        assert result == expected

    def test_days(self):
        result = m.convert_relative_timestamp("2d")
        expected = (datetime.now(KST) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
        assert result == expected

    def test_weeks(self):
        result = m.convert_relative_timestamp("1w")
        expected = (datetime.now(KST) - timedelta(weeks=1)).strftime("%Y-%m-%d %H:%M")
        assert result == expected

    def test_minutes(self):
        result = m.convert_relative_timestamp("30m")
        expected = (datetime.now(KST) - timedelta(minutes=30)).strftime(
            "%Y-%m-%d %H:%M"
        )
        assert result == expected

    def test_months(self):
        result = m.convert_relative_timestamp("3mo")
        expected = (datetime.now(KST) - timedelta(days=90)).strftime("%Y-%m-%d %H:%M")
        assert result == expected

    def test_years(self):
        result = m.convert_relative_timestamp("1yr")
        expected = (datetime.now(KST) - timedelta(days=365)).strftime("%Y-%m-%d %H:%M")
        assert result == expected

    def test_korean_hours(self):
        result = m.convert_relative_timestamp("6시간 전")
        expected = (datetime.now(KST) - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M")
        assert result == expected

    def test_korean_days(self):
        result = m.convert_relative_timestamp("2일 전")
        expected = (datetime.now(KST) - timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
        assert result == expected

    def test_now(self):
        result = m.convert_relative_timestamp("now")
        expected = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        assert result == expected

    def test_now_korean(self):
        result = m.convert_relative_timestamp("방금")
        expected = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        assert result == expected

    def test_unknown_fallback(self):
        """파싱 불가 문자열은 원본 그대로 반환"""
        assert m.convert_relative_timestamp("Edited · 6h") == "Edited · 6h"
        assert m.convert_relative_timestamp("") == ""
        assert m.convert_relative_timestamp("unknown text") == "unknown text"
