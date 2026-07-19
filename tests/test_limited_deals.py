from __future__ import annotations

import json
import ssl
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.config import AppConfig
from droid_alerts.classifier import Detection
from droid_alerts.gui import (
    DISCORD_COMMUNITY_URL,
    TRACKER_URL,
    WIKI_URL,
    DroidAlertsApp,
)
from droid_alerts.limited_deals import (
    LimitedDeal,
    LimitedDealService,
    LIMITED_DEAL_PRIORITY_COMBOS,
    fetch_current_limited_deal,
    fetch_limited_deal_portrait,
    limited_deal_matches,
    limited_deal_portrait_url,
    next_limited_deal_fetch_at,
    normalize_limited_deal_priority_alerts,
    normalize_limited_deal_target_tiers,
)
from droid_alerts.notifications import alert_title, event_text
from droid_alerts.popup import _title_lines


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class ImmediateThread:
    def __init__(self, *, target, args=(), kwargs=None, **_options):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


def deal_payload(
    *,
    mutation: str = "Rainbow",
    droid: str = "R2",
    droid_id: int = 17,
    rarity: str = "Epic",
) -> dict[str, object]:
    return {
        "startsAt": "2026-07-18T14:00:00.000Z",
        "endsAt": "2026-07-18T15:00:00.000Z",
        "mutation": mutation,
        "droid": droid,
        "droidId": droid_id,
        "rarity": rarity,
    }


def current_deal() -> LimitedDeal:
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    return LimitedDeal.from_payload(
        {
            **deal_payload(),
            "startsAt": start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "endsAt": end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
    )


class LimitedDealRuleTests(unittest.TestCase):
    def test_portrait_urls_match_the_website_assets(self):
        default_deal = LimitedDeal.from_payload(
            deal_payload(mutation="Default", droid="A-LT", droid_id=8, rarity="Rare")
        )
        rainbow_deal = LimitedDeal.from_payload(
            deal_payload(mutation="Rainbow", droid="A-LT", droid_id=8, rarity="Rare")
        )
        endpoint = "https://gonk.tools/api/droid-alerts/limited-deal"

        self.assertEqual(
            "https://gonk.tools/droids/T_Portrait_ALT_Common.png",
            limited_deal_portrait_url(endpoint, default_deal),
        )
        self.assertEqual(
            "https://gonk.tools/droids/rarities/T_Portrait_ALT_Rainbow.png",
            limited_deal_portrait_url(endpoint, rainbow_deal),
        )

    def test_rule_normalization_keeps_only_supported_values(self):
        self.assertEqual(
            {"17": "Rainbow", "59": "Beskar"},
            normalize_limited_deal_target_tiers(
                {17: "rainbow", "59": "BESKAR", "999": "Gold", "7": "Nope"}
            ),
        )
        self.assertEqual(
            [
                ["Rainbow", "Epic"],
                ["Galactic", "Legendary"],
                ["Beskar", "Mythic"],
            ],
            normalize_limited_deal_priority_alerts(
                [
                    ["beskar", "mythic"],
                    ["Rainbow", "Epic"],
                    ["galactic", "legendary"],
                    ["Gold", "Rare"],
                ]
            ),
        )

    def test_priority_and_per_droid_minimum_rules_are_combined(self):
        deal = LimitedDeal.from_payload(deal_payload())
        self.assertTrue(limited_deal_matches(deal, [["Rainbow", "Epic"]], {}))
        self.assertTrue(limited_deal_matches(deal, [], {"17": "Diamond"}))
        self.assertFalse(limited_deal_matches(deal, [], {"17": "Beskar"}))
        self.assertFalse(limited_deal_matches(deal, [], {"59": "Default"}))

    def test_priority_alerts_include_every_mythic_mutation(self):
        mythic_mutations = {
            mutation
            for mutation, rarity in LIMITED_DEAL_PRIORITY_COMBOS
            if rarity == "Mythic"
        }
        self.assertEqual(
            {"Default", "Gold", "Diamond", "Rainbow", "Beskar", "Galactic"},
            mythic_mutations,
        )

    def test_priority_alerts_include_all_galactic_target_classes(self):
        self.assertTrue(
            {
                ("Galactic", "Epic"),
                ("Galactic", "Legendary"),
                ("Galactic", "Mythic"),
            }.issubset(set(LIMITED_DEAL_PRIORITY_COMBOS))
        )

    def test_config_round_trips_limited_deal_rules(self):
        config = AppConfig.from_dict(
            {
                "limited_deal_priority_alerts": [list(LIMITED_DEAL_PRIORITY_COMBOS[0])],
                "limited_deal_target_tiers": {"17": "Rainbow"},
            }
        )
        restored = AppConfig.from_dict(config.to_dict())
        self.assertEqual([["Rainbow", "Epic"]], restored.limited_deal_priority_alerts)
        self.assertEqual({"17": "Rainbow"}, restored.limited_deal_target_tiers)

    def test_limited_deal_alert_copy_is_distinct_from_spawn_and_belt_alerts(self):
        detection = Detection(
            droid="R2",
            rarity="Rainbow Epic",
            row_box=(0, 0, 0, 0),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="limited-deal",
        )
        self.assertEqual("Limited Deal: Rainbow Epic R2", event_text(detection))
        self.assertEqual("Droid Alerts Limited Deal", alert_title(detection))
        self.assertEqual(
            ["RAINBOW EPIC", "R2"],
            ["".join(text for text, _color in line) for line in _title_lines(detection)],
        )


class LimitedDealSchedulingTests(unittest.TestCase):
    def test_fetch_uses_a_bundled_ca_context(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({"deal": deal_payload()}).encode()

        with patch(
            "droid_alerts.limited_deals.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            deal = fetch_current_limited_deal("https://example.test/limited-deal")

        self.assertEqual("R2", deal.droid)
        context = urlopen.call_args.kwargs["context"]
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertTrue(context.get_ca_certs())

    def test_portrait_download_is_cached_as_a_valid_png(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"\x89PNG\r\n\x1a\nwebsite-icon"
        deal = LimitedDeal.from_payload(
            deal_payload(mutation="Default", droid="A-LT", droid_id=8, rarity="Rare")
        )

        with tempfile.TemporaryDirectory() as directory, patch(
            "droid_alerts.limited_deals.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            first = fetch_limited_deal_portrait(
                "https://gonk.tools/api/droid-alerts/limited-deal",
                deal,
                Path(directory),
            )
            second = fetch_limited_deal_portrait(
                "https://gonk.tools/api/droid-alerts/limited-deal",
                deal,
                Path(directory),
            )
            self.assertEqual(first, second)
            self.assertEqual(1, urlopen.call_count)
            self.assertTrue(first.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_fetch_waits_until_ten_seconds_after_a_new_hour(self):
        before = datetime(2026, 7, 18, 14, 0, 5, tzinfo=timezone.utc)
        self.assertEqual(
            datetime(2026, 7, 18, 14, 0, 10, tzinfo=timezone.utc),
            next_limited_deal_fetch_at(before),
        )

    def test_missing_current_hour_cache_fetches_immediately_after_second_ten(self):
        after = datetime(2026, 7, 18, 14, 37, 25, tzinfo=timezone.utc)
        self.assertEqual(after, next_limited_deal_fetch_at(after))

    def test_an_attempted_hour_never_fetches_again_until_next_hour(self):
        now = datetime(2026, 7, 18, 14, 37, 25, tzinfo=timezone.utc)
        attempted = datetime(2026, 7, 18, 14, 0, 10, tzinfo=timezone.utc)
        self.assertEqual(
            datetime(2026, 7, 18, 15, 0, 10, tzinfo=timezone.utc),
            next_limited_deal_fetch_at(now, attempted_hour=attempted),
        )

    def test_current_cache_is_shown_then_refreshed_when_the_app_starts(self):
        now = datetime(2026, 7, 18, 14, 30, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "limited_deal.json"
            cache_path.write_text(
                json.dumps({"deal": deal_payload(), "lastAlertedStartsAt": ""}),
                encoding="utf-8",
            )
            fetch_called = threading.Event()
            refresh_published = threading.Event()
            statuses = []
            fetch_count = 0

            def fetcher(_url: str) -> LimitedDeal:
                nonlocal fetch_count
                fetch_count += 1
                fetch_called.set()
                return LimitedDeal.from_payload(deal_payload())

            def record_status(status) -> None:
                statuses.append(status)
                if status.source == "network":
                    refresh_published.set()

            service = LimitedDealService(
                "https://example.test/limited-deal",
                cache_path,
                record_status,
                fetcher=fetcher,
                portrait_fetcher=lambda _url, _deal, cache: cache / "portrait.png",
                clock=lambda: now,
            )
            service.start()
            self.assertTrue(fetch_called.wait(1.0))
            self.assertTrue(refresh_published.wait(1.0))
            service.stop()
            self.assertEqual(1, fetch_count)
            self.assertEqual("cache", statuses[0].source)
            self.assertEqual("network", statuses[-1].source)
            self.assertEqual("R2", service.current_deal.droid)

    def test_startup_fetch_does_not_wait_for_second_ten(self):
        now = datetime(2026, 7, 18, 14, 0, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            fetch_called = threading.Event()

            def fetcher(_url: str) -> LimitedDeal:
                fetch_called.set()
                return LimitedDeal.from_payload(deal_payload())

            service = LimitedDealService(
                "https://example.test/limited-deal",
                Path(directory) / "limited_deal.json",
                lambda _status: None,
                fetcher=fetcher,
                portrait_fetcher=lambda _url, _deal, cache: cache / "portrait.png",
                clock=lambda: now,
            )
            service.start()
            self.assertTrue(fetch_called.wait(1.0))
            service.stop()


class LimitedDealAlertDeliveryTests(unittest.TestCase):
    def test_first_time_popup_is_triggered_only_when_limited_deals_tab_opens(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.root = Mock()
        app.notebook = Mock()
        app.limited_deals_tab = object()
        app.show_limited_deals_intro_if_needed = Mock()

        app.root.nametowidget.return_value = object()
        DroidAlertsApp._on_limited_deals_tab_opened(app)
        app.show_limited_deals_intro_if_needed.assert_not_called()

        app.root.nametowidget.return_value = app.limited_deals_tab
        DroidAlertsApp._on_limited_deals_tab_opened(app)
        app.show_limited_deals_intro_if_needed.assert_called_once_with()

    def test_sidebar_external_links_open_the_requested_destinations(self):
        urls = (DISCORD_COMMUNITY_URL, TRACKER_URL, WIKI_URL)
        self.assertEqual(
            (
                "https://discord.gg/ZmFPjS4784",
                "https://gonk.tools/tracker",
                "https://gonk.tools/wiki",
            ),
            urls,
        )
        with patch("droid_alerts.gui.webbrowser.open") as open_browser:
            for url in urls:
                DroidAlertsApp._open_sidebar_link(url)
        self.assertEqual(
            list(urls),
            [call.args[0] for call in open_browser.call_args_list],
        )

    def test_first_time_popup_uses_requested_copy_and_is_remembered(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app._setup_dialog = Mock(return_value={})
        config = AppConfig(limited_deals_intro_shown=False)

        with (
            patch("droid_alerts.gui.load_config", return_value=config),
            patch("droid_alerts.gui.save_config") as save,
        ):
            DroidAlertsApp.show_limited_deals_intro_if_needed(app)

        self.assertTrue(config.limited_deals_intro_shown)
        save.assert_called_once_with(config)
        call = app._setup_dialog.call_args
        self.assertEqual("Limited Deals", call.args[0])
        self.assertEqual(
            "Get alerted when the limited deal is something you want. "
            "Pick a range of droids, or select any droid you want! "
            "Alert takes around 10 seconds",
            call.kwargs["intro"],
        )

    def test_closing_first_time_popup_does_not_acknowledge_it(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app._setup_dialog = Mock(return_value=None)
        config = AppConfig(limited_deals_intro_shown=False)

        with (
            patch("droid_alerts.gui.load_config", return_value=config),
            patch("droid_alerts.gui.save_config") as save,
        ):
            DroidAlertsApp.show_limited_deals_intro_if_needed(app)

        self.assertFalse(config.limited_deals_intro_shown)
        save.assert_not_called()

    def test_countdown_switches_to_getting_state_at_the_hour(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.current_limited_deal = LimitedDeal.from_payload(deal_payload())
        app.limited_deal_offer_var = FakeVar("R2 - Rainbow Epic")
        app.limited_deal_timer_var = FakeVar()
        app._set_limited_deal_portrait = Mock()

        DroidAlertsApp._render_limited_deal_countdown(
            app,
            datetime(2026, 7, 18, 14, 59, 50, tzinfo=timezone.utc),
        )
        self.assertEqual("00:00:10", app.limited_deal_timer_var.value)
        self.assertEqual("R2 - Rainbow Epic", app.limited_deal_offer_var.value)

        DroidAlertsApp._render_limited_deal_countdown(
            app,
            datetime(2026, 7, 18, 15, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual("00:00:00", app.limited_deal_timer_var.value)
        self.assertEqual("Getting limited deal...", app.limited_deal_offer_var.value)
        app._set_limited_deal_portrait.assert_called_once_with(None)

    def test_matching_deal_is_alerted_only_once(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        deal = current_deal()
        service = Mock()
        service.was_alerted.side_effect = (False, True)
        app.limited_deal_service = service
        app.current_limited_deal = deal
        app._send_limited_deal_alert = Mock()
        config = AppConfig(limited_deal_priority_alerts=[["Rainbow", "Epic"]])

        with patch("droid_alerts.gui.load_config", return_value=config):
            DroidAlertsApp._evaluate_current_limited_deal(app)
            DroidAlertsApp._evaluate_current_limited_deal(app)

        service.mark_alerted.assert_called_once_with(deal)
        app._send_limited_deal_alert.assert_called_once_with(deal, config)

    def test_matching_deal_uses_every_enabled_alert_channel(self):
        app = DroidAlertsApp.__new__(DroidAlertsApp)
        app.root = object()
        app.detail_var = FakeVar()
        app.channel_status_vars = {
            label: FakeVar()
            for label in ("Popup", "Sound", "Discord", "ntfy", "Pushover")
        }
        app._current_monitor_info = lambda: None
        app._post_to_ui = lambda callback: callback()
        app.refresh_logs = Mock()

        config = AppConfig(
            popup_enabled=True,
            sound_enabled=True,
            discord_enabled=True,
            ntfy_enabled=True,
            phone_alerts_enabled=True,
        )
        policy = SimpleNamespace(notify=Mock())
        delivered = SimpleNamespace(success=True, message="Delivered")

        with (
            patch("droid_alerts.gui.AlertPolicy", return_value=policy),
            patch("droid_alerts.gui.show_popup") as popup,
            patch(
                "droid_alerts.gui.load_discord_webhook",
                return_value=("https://example.test", "test"),
            ),
            patch("droid_alerts.gui.ntfy_configured", return_value=True),
            patch(
                "droid_alerts.gui.load_phone_alert_credentials",
                return_value=({"token": "t", "user": "u"}, "test"),
            ),
            patch("droid_alerts.gui.send_discord_alert", return_value=delivered) as discord,
            patch("droid_alerts.gui.send_ntfy_alert", return_value=delivered) as ntfy,
            patch("droid_alerts.gui.send_phone_alert", return_value=delivered) as phone,
            patch("droid_alerts.gui.append_event") as append,
            patch("droid_alerts.gui.threading.Thread", ImmediateThread),
        ):
            DroidAlertsApp._send_limited_deal_alert(app, current_deal(), config)

        policy.notify.assert_called_once()
        popup.assert_called_once()
        discord.assert_called_once()
        ntfy.assert_called_once()
        phone.assert_called_once()
        detection = popup.call_args.args[0]
        self.assertEqual("limited-deal", detection.source)
        self.assertEqual("Rainbow Epic", detection.rarity)
        self.assertEqual(4, append.call_count)
        self.assertTrue(
            all(call.args[0]["source"] == "limited_deal" for call in append.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
