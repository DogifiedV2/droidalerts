from __future__ import annotations

import json
import ssl
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR / "src"))

from droid_alerts.config import AppConfig
from droid_alerts.classifier import Detection
from droid_alerts.limited_deals import (
    LimitedDeal,
    LimitedDealService,
    LIMITED_DEAL_CUSTOM_ALERT_DROIDS,
    LIMITED_DEAL_PRIORITY_COMBOS,
    fetch_current_limited_deal,
    fetch_limited_deal_portrait,
    limited_deal_matches,
    limited_deal_portrait_url,
    next_limited_deal_fetch_at,
    next_limited_deal_retry_at,
    normalize_limited_deal_priority_alerts,
    normalize_limited_deal_target_tiers,
)
from droid_alerts.notifications import (
    load_discord_webhook_for_detection,
    save_discord_webhook,
    save_limited_deal_discord_webhook,
)


def deal_payload(
    *,
    deal_rarity: str = "Rainbow",
    droid: str = "R2",
    droid_id: int = 17,
    droid_class: str = "Epic",
) -> dict[str, object]:
    return {
        "startsAt": "2026-07-18T14:00:00.000Z",
        "endsAt": "2026-07-18T15:00:00.000Z",
        "mutation": deal_rarity,
        "droid": droid,
        "droidId": droid_id,
        "rarity": droid_class,
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
            deal_payload(
                deal_rarity="Default",
                droid="A-LT",
                droid_id=8,
                droid_class="Rare",
            )
        )
        rainbow_deal = LimitedDeal.from_payload(
            deal_payload(
                deal_rarity="Rainbow",
                droid="A-LT",
                droid_id=8,
                droid_class="Rare",
            )
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
                {
                    17: "rainbow",
                    59: "BESKAR",
                    1: "Galactic",
                    7: "Default",
                    8: "Gold",
                    "999": "Gold",
                    "10": "Nope",
                }
            ),
        )
        self.assertEqual(
            [
                ["Rainbow", "Epic"],
                ["Beskar", "Mythic"],
                ["Galactic", "Legendary"],
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

    def test_priority_alerts_follow_the_two_column_display_order(self):
        self.assertEqual(
            (
                ("Rainbow", "Epic"),
                ("Rainbow", "Legendary"),
                ("Rainbow", "Mythic"),
                ("Beskar", "Epic"),
                ("Beskar", "Legendary"),
                ("Beskar", "Mythic"),
                ("Galactic", "Epic"),
                ("Galactic", "Legendary"),
                ("Galactic", "Mythic"),
                ("Diamond", "Mythic"),
            ),
            LIMITED_DEAL_PRIORITY_COMBOS,
        )

    def test_custom_alert_droids_exclude_rare_droids(self):
        self.assertNotIn(
            "Rare",
            {droid.droid_class for droid in LIMITED_DEAL_CUSTOM_ALERT_DROIDS},
        )

    def test_priority_and_per_droid_minimum_rules_are_combined(self):
        deal = LimitedDeal.from_payload(deal_payload())
        self.assertTrue(limited_deal_matches(deal, [["Rainbow", "Epic"]], {}))
        self.assertTrue(limited_deal_matches(deal, [], {"17": "Diamond"}))
        self.assertFalse(limited_deal_matches(deal, [], {"17": "Beskar"}))
        self.assertFalse(limited_deal_matches(deal, [], {"59": "Default"}))

    def test_priority_alerts_exclude_default_and_gold_rarities(self):
        mythic_rarities = {
            deal_rarity
            for deal_rarity, droid_class in LIMITED_DEAL_PRIORITY_COMBOS
            if droid_class == "Mythic"
        }
        self.assertEqual(
            {"Diamond", "Rainbow", "Beskar", "Galactic"},
            mythic_rarities,
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
                "limited_deal_discord_webhook_file": "deals-webhook.txt",
                "limited_deal_discord_env_var": "DEALS_WEBHOOK_URL",
            }
        )
        restored = AppConfig.from_dict(config.to_dict())
        self.assertEqual([["Rainbow", "Epic"]], restored.limited_deal_priority_alerts)
        self.assertEqual({"17": "Rainbow"}, restored.limited_deal_target_tiers)
        self.assertEqual(
            "deals-webhook.txt",
            restored.limited_deal_discord_webhook_file,
        )
        self.assertEqual(
            "DEALS_WEBHOOK_URL",
            restored.limited_deal_discord_env_var,
        )

    def test_limited_deals_can_use_a_separate_discord_webhook(self):
        config = AppConfig()
        normal = Detection(
            droid="Beskar",
            rarity="Mythic",
            row_box=(0, 0, 1, 1),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="watcher",
        )
        limited = Detection(
            droid="R2",
            rarity="Rainbow Epic",
            row_box=(0, 0, 1, 1),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="limited-deal",
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "droid_alerts.notifications.config_dir",
            return_value=Path(directory),
        ):
            save_discord_webhook(config, "https://discord.com/api/webhooks/1/main")
            save_limited_deal_discord_webhook(
                config,
                "https://discord.com/api/webhooks/2/deals",
            )

            normal_url, _ = load_discord_webhook_for_detection(config, normal)
            limited_url, _ = load_discord_webhook_for_detection(config, limited)
            self.assertEqual("https://discord.com/api/webhooks/1/main", normal_url)
            self.assertEqual("https://discord.com/api/webhooks/2/deals", limited_url)

            save_limited_deal_discord_webhook(config, "")
            fallback_url, _ = load_discord_webhook_for_detection(config, limited)
            self.assertEqual("https://discord.com/api/webhooks/1/main", fallback_url)

    def test_explicit_missing_discord_destination_does_not_fall_back_to_main(self):
        config = AppConfig(
            discord_alert_destinations={"limited_deals": "Private Guild"}
        )
        limited = Detection(
            droid="R2",
            rarity="Rainbow Epic",
            row_box=(0, 0, 1, 1),
            droid_score=1.0,
            rarity_score=1.0,
            rarity_margin=1.0,
            score=1.0,
            source="limited-deal",
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "droid_alerts.notifications.config_dir",
            return_value=Path(directory),
        ):
            save_discord_webhook(
                config,
                "https://discord.com/api/webhooks/1/main",
            )
            with self.assertRaisesRegex(ValueError, "Private Guild"):
                load_discord_webhook_for_detection(config, limited)

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
            deal_payload(
                deal_rarity="Default",
                droid="A-LT",
                droid_id=8,
                droid_class="Rare",
            )
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

    def test_a_successful_hour_never_fetches_again_until_next_hour(self):
        now = datetime(2026, 7, 18, 14, 37, 25, tzinfo=timezone.utc)
        attempted = datetime(2026, 7, 18, 14, 0, 10, tzinfo=timezone.utc)
        self.assertEqual(
            datetime(2026, 7, 18, 15, 0, 10, tzinfo=timezone.utc),
            next_limited_deal_fetch_at(now, attempted_hour=attempted),
        )

    def test_failed_fetch_retries_within_the_current_hour(self):
        now = datetime(2026, 7, 18, 14, 37, 25, tzinfo=timezone.utc)
        self.assertEqual(
            datetime(2026, 7, 18, 14, 37, 55, tzinfo=timezone.utc),
            next_limited_deal_retry_at(now),
        )

    def test_service_retries_a_transient_fetch_failure(self):
        now = datetime.now(timezone.utc).replace(minute=30, second=0, microsecond=0)
        attempts = 0
        fetched = threading.Event()

        def fetcher(_url: str) -> LimitedDeal:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TimeoutError("temporary timeout")
            fetched.set()
            return current_deal()

        with tempfile.TemporaryDirectory() as directory, patch(
            "droid_alerts.limited_deals.FETCH_RETRY_SECONDS",
            0,
        ):
            service = LimitedDealService(
                "https://example.test/limited-deal",
                Path(directory) / "limited_deal.json",
                lambda _status: None,
                fetcher=fetcher,
                portrait_fetcher=lambda _url, _deal, cache: cache / "portrait.png",
                clock=lambda: now,
            )
            service.start()
            self.assertTrue(fetched.wait(1.0))
            service.stop()

        self.assertEqual(2, attempts)

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


if __name__ == "__main__":
    unittest.main()
