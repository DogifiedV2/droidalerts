from __future__ import annotations

import json
import ssl
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import certifi

from . import __version__


LIMITED_DEAL_FAMILY_ORDER = (
    "Default",
    "Gold",
    "Diamond",
    "Rainbow",
    "Beskar",
    "Galactic",
)
LIMITED_DEAL_FAMILY_RANK = {
    family: index for index, family in enumerate(LIMITED_DEAL_FAMILY_ORDER)
}
LIMITED_DEAL_TARGET_LABELS = {
    family: f"{family}+" if family != LIMITED_DEAL_FAMILY_ORDER[-1] else family
    for family in LIMITED_DEAL_FAMILY_ORDER
}
LIMITED_DEAL_TARGET_FAMILIES_BY_LABEL = {
    label: family for family, label in LIMITED_DEAL_TARGET_LABELS.items()
}
LIMITED_DEAL_RARITIES = ("Rare", "Epic", "Legendary", "Mythic")
LIMITED_DEAL_PRIORITY_COMBOS = (
    ("Rainbow", "Epic"),
    ("Rainbow", "Legendary"),
    ("Beskar", "Epic"),
    ("Beskar", "Legendary"),
    ("Default", "Mythic"),
    ("Gold", "Mythic"),
    ("Diamond", "Mythic"),
    ("Rainbow", "Mythic"),
    ("Beskar", "Mythic"),
    ("Galactic", "Mythic"),
)
FETCH_SECOND = 10
REQUEST_TIMEOUT_SECONDS = 4.0
USER_AGENT = f"DroidAlerts/{__version__}"


@dataclass(frozen=True)
class LimitedDealDroid:
    id: int
    name: str
    rarity: str
    portrait_key: str


# This is the public rotating pool needed by the offline alert-rule picker.
# Future shop offers remain server-only; the app receives only the live offer.
LIMITED_DEAL_DROIDS = (
    LimitedDealDroid(1, "BDX Explorer", "Rare", "BDX"),
    LimitedDealDroid(7, "2BB", "Rare", "2BB"),
    LimitedDealDroid(8, "A-LT", "Rare", "ALT"),
    LimitedDealDroid(10, "B1 Heavy", "Epic", "B1Heavy"),
    LimitedDealDroid(11, "B1 Security", "Rare", "B1Security"),
    LimitedDealDroid(12, "Groundmech", "Epic", "B2"),
    LimitedDealDroid(13, "BB", "Epic", "BB"),
    LimitedDealDroid(14, "BB9", "Legendary", "BB9"),
    LimitedDealDroid(17, "R2", "Epic", "R2"),
    LimitedDealDroid(20, "R4", "Rare", "R4"),
    LimitedDealDroid(22, "R6", "Epic", "R6"),
    LimitedDealDroid(23, "R7", "Legendary", "R7"),
    LimitedDealDroid(25, "R9", "Rare", "R9"),
    LimitedDealDroid(26, "ARG", "Rare", "ARG19"),
    LimitedDealDroid(27, "L0", "Epic", "L0"),
    LimitedDealDroid(30, "Trak-R", "Epic", "TrakR"),
    LimitedDealDroid(31, "Proto-Roller", "Legendary", "ProtoRoller"),
    LimitedDealDroid(32, "Orb-Walker", "Epic", "OrbWalker"),
    LimitedDealDroid(34, "B2 Super", "Epic", "B2Super"),
    LimitedDealDroid(35, "B2 Heavy", "Epic", "B2Heavy"),
    LimitedDealDroid(36, "B2-RP", "Legendary", "B2Rocket"),
    LimitedDealDroid(39, "Senate Hovercam", "Rare", "SenateHovercam"),
    LimitedDealDroid(40, "B-U4D", "Rare", "BR72"),
    LimitedDealDroid(43, "Nav-Ex", "Rare", "Combo01"),
    LimitedDealDroid(44, "Bal-Core", "Rare", "Combo02"),
    LimitedDealDroid(45, "Strike-Orb", "Epic", "Combo04"),
    LimitedDealDroid(46, "Amp Walker", "Epic", "Combo05"),
    LimitedDealDroid(47, "Mecha-Droid", "Legendary", "Combo06"),
    LimitedDealDroid(48, "Cyclo-Grav", "Legendary", "Combo08"),
    LimitedDealDroid(49, "Util-Tec", "Epic", "Combo09"),
    LimitedDealDroid(50, "Vect-ARM", "Rare", "Combo11"),
    LimitedDealDroid(51, "Roll-R", "Rare", "Combo12"),
    LimitedDealDroid(52, "Haul-R", "Epic", "Combo13"),
    LimitedDealDroid(53, "Opti-STRK", "Legendary", "Combo14"),
    LimitedDealDroid(54, "Hov-R", "Rare", "Combo15"),
    LimitedDealDroid(55, "LNG-Shot", "Epic", "Combo16"),
    LimitedDealDroid(56, "Sen-Tri", "Epic", "Combo17"),
    LimitedDealDroid(57, "Mono-WLKR", "Legendary", "Combo18"),
    LimitedDealDroid(58, "Opti-Pod", "Epic", "Combo19"),
    LimitedDealDroid(59, "IG", "Mythic", "IG"),
    LimitedDealDroid(60, "Gunrunner", "Epic", "Smuggler"),
    LimitedDealDroid(61, "Snow Mouse", "Mythic", "SnowMouse"),
    LimitedDealDroid(63, "DRFT-R", "Mythic", "Combo20"),
    LimitedDealDroid(64, "Cyclens", "Mythic", "Combo21"),
    LimitedDealDroid(65, "KX", "Mythic", "KX"),
    LimitedDealDroid(66, "RIC", "Mythic", "Ric"),
    LimitedDealDroid(67, "Loadlifter", "Mythic", "LoadLifter"),
    LimitedDealDroid(68, "LEP", "Mythic", "LEP"),
    LimitedDealDroid(69, "RIC-1200", "Mythic", "Ric1200"),
    LimitedDealDroid(70, "MO-Trak", "Mythic", "Combo22"),
    LimitedDealDroid(71, "TRI-TEK", "Mythic", "Combo23"),
)
LIMITED_DEAL_DROIDS_BY_ID = {droid.id: droid for droid in LIMITED_DEAL_DROIDS}
_FAMILY_BY_CASEFOLD = {
    family.casefold(): family for family in LIMITED_DEAL_FAMILY_ORDER
}
_PRIORITY_COMBOS = frozenset(LIMITED_DEAL_PRIORITY_COMBOS)


@dataclass(frozen=True)
class LimitedDeal:
    starts_at: str
    ends_at: str
    mutation: str
    droid: str
    droid_id: int
    rarity: str

    @classmethod
    def from_payload(cls, value: object) -> "LimitedDeal":
        if not isinstance(value, Mapping):
            raise ValueError("Limited Deal response must contain a deal object")
        if set(value) != {"startsAt", "endsAt", "mutation", "droid", "droidId", "rarity"}:
            raise ValueError("Limited Deal response exposed unexpected deal data")
        mutation = _FAMILY_BY_CASEFOLD.get(str(value.get("mutation") or "").casefold())
        rarity = str(value.get("rarity") or "").strip().title()
        droid = str(value.get("droid") or "").strip()
        try:
            droid_id = int(value.get("droidId"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Limited Deal response has an invalid droid ID") from exc
        starts_at = _normalized_utc_iso(value.get("startsAt"))
        ends_at = _normalized_utc_iso(value.get("endsAt"))
        if mutation is None or rarity not in LIMITED_DEAL_RARITIES:
            raise ValueError("Limited Deal response has an invalid rarity")
        if not droid or len(droid) > 64:
            raise ValueError("Limited Deal response has an invalid droid name")
        catalog_droid = LIMITED_DEAL_DROIDS_BY_ID.get(droid_id)
        if catalog_droid is None:
            raise ValueError("Limited Deal response contains an unknown droid")
        if catalog_droid.name != droid or catalog_droid.rarity != rarity:
            raise ValueError("Limited Deal response does not match the rotating droid catalog")
        start_time = _parse_utc(starts_at)
        end_time = _parse_utc(ends_at)
        if (
            start_time.minute != 0
            or start_time.second != 0
            or start_time.microsecond != 0
            or end_time - start_time != timedelta(hours=1)
        ):
            raise ValueError("Limited Deal response has an invalid time window")
        return cls(
            starts_at=starts_at,
            ends_at=ends_at,
            mutation=mutation,
            droid=droid,
            droid_id=droid_id,
            rarity=rarity,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "startsAt": self.starts_at,
            "endsAt": self.ends_at,
            "mutation": self.mutation,
            "droid": self.droid,
            "droidId": self.droid_id,
            "rarity": self.rarity,
        }

    def is_current(self, now: datetime | None = None) -> bool:
        current = _as_utc(now or datetime.now(timezone.utc))
        return _parse_utc(self.starts_at) <= current < _parse_utc(self.ends_at)


@dataclass(frozen=True)
class LimitedDealStatus:
    deal: LimitedDeal | None
    source: str
    error: str
    checked_at: datetime | None
    next_fetch_at: datetime
    portrait_path: Path | None = None


def normalize_limited_deal_target_tiers(raw_targets: object) -> dict[str, str]:
    if not isinstance(raw_targets, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for raw_id, raw_family in raw_targets.items():
        try:
            droid_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        family = _FAMILY_BY_CASEFOLD.get(str(raw_family or "").strip().casefold())
        if droid_id in LIMITED_DEAL_DROIDS_BY_ID and family is not None:
            normalized[str(droid_id)] = family
    return {
        str(droid.id): normalized[str(droid.id)]
        for droid in LIMITED_DEAL_DROIDS
        if str(droid.id) in normalized
    }


def normalize_limited_deal_priority_alerts(raw_alerts: object) -> list[list[str]]:
    if not isinstance(raw_alerts, Sequence) or isinstance(raw_alerts, (str, bytes)):
        return []
    selected: set[tuple[str, str]] = set()
    for raw_combo in raw_alerts:
        if not isinstance(raw_combo, Sequence) or isinstance(raw_combo, (str, bytes)):
            continue
        if len(raw_combo) != 2:
            continue
        family = _FAMILY_BY_CASEFOLD.get(str(raw_combo[0] or "").strip().casefold())
        rarity = str(raw_combo[1] or "").strip().title()
        combo = (family or "", rarity)
        if combo in _PRIORITY_COMBOS:
            selected.add(combo)
    return [list(combo) for combo in LIMITED_DEAL_PRIORITY_COMBOS if combo in selected]


def limited_deal_target_label(family: object) -> str:
    canonical = _FAMILY_BY_CASEFOLD.get(str(family or "").strip().casefold())
    return LIMITED_DEAL_TARGET_LABELS.get(canonical, "Off")


def limited_deal_matches(
    deal: LimitedDeal,
    priority_alerts: object,
    target_tiers: object,
) -> bool:
    normalized_priority = {
        tuple(combo) for combo in normalize_limited_deal_priority_alerts(priority_alerts)
    }
    if (deal.mutation, deal.rarity) in normalized_priority:
        return True
    normalized_targets = normalize_limited_deal_target_tiers(target_tiers)
    minimum = normalized_targets.get(str(deal.droid_id))
    if minimum is None:
        return False
    return LIMITED_DEAL_FAMILY_RANK[deal.mutation] >= LIMITED_DEAL_FAMILY_RANK[minimum]


def next_limited_deal_fetch_at(
    now: datetime,
    *,
    attempted_hour: datetime | None = None,
) -> datetime:
    current = _as_utc(now)
    hour = current.replace(minute=0, second=0, microsecond=0)
    attempted = (
        _as_utc(attempted_hour).replace(minute=0, second=0, microsecond=0)
        if attempted_hour
        else None
    )
    scheduled = hour + timedelta(seconds=FETCH_SECOND)
    if attempted == hour:
        return hour + timedelta(hours=1, seconds=FETCH_SECOND)
    return scheduled if current < scheduled else current


def fetch_current_limited_deal(endpoint_url: str) -> LimitedDeal:
    request = urllib.request.Request(
        endpoint_url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    tls_context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(
        request,
        timeout=REQUEST_TIMEOUT_SECONDS,
        context=tls_context,
    ) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
    if not isinstance(payload, Mapping) or set(payload) != {"deal"}:
        raise ValueError("Limited Deal response exposed unexpected data")
    return LimitedDeal.from_payload(payload["deal"])


def limited_deal_portrait_url(endpoint_url: str, deal: LimitedDeal) -> str:
    droid = LIMITED_DEAL_DROIDS_BY_ID[deal.droid_id]
    if deal.mutation == "Default":
        path = f"/droids/T_Portrait_{droid.portrait_key}_Common.png"
    else:
        path = (
            f"/droids/rarities/T_Portrait_{droid.portrait_key}_{deal.mutation}.png"
        )
    return urljoin(endpoint_url, path)


def fetch_limited_deal_portrait(
    endpoint_url: str,
    deal: LimitedDeal,
    cache_dir: Path,
) -> Path:
    cache_path = cache_dir / f"{deal.droid_id}_{deal.mutation.casefold()}.png"
    if _valid_png(cache_path):
        return cache_path

    request = urllib.request.Request(
        limited_deal_portrait_url(endpoint_url, deal),
        headers={"Accept": "image/png", "User-Agent": USER_AGENT},
        method="GET",
    )
    tls_context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(
        request,
        timeout=REQUEST_TIMEOUT_SECONDS,
        context=tls_context,
    ) as response:
        image = response.read(2_000_001)
    if len(image) > 2_000_000 or not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Limited Deal portrait response was not a valid PNG")

    cache_dir.mkdir(parents=True, exist_ok=True)
    temp = cache_path.with_suffix(".png.tmp")
    temp.write_bytes(image)
    temp.replace(cache_path)
    return cache_path


class LimitedDealService:
    """Fetch one current-only deal per shop hour and persist a local cache."""

    def __init__(
        self,
        endpoint_url: str,
        cache_path: Path,
        callback: Callable[[LimitedDealStatus], None],
        *,
        fetcher: Callable[[str], LimitedDeal] = fetch_current_limited_deal,
        portrait_fetcher: Callable[[str, LimitedDeal, Path], Path] = (
            fetch_limited_deal_portrait
        ),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._endpoint_url = endpoint_url.strip()
        self._cache_path = cache_path
        self._callback = callback
        self._fetcher = fetcher
        self._portrait_fetcher = portrait_fetcher
        self._clock = clock
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._deal: LimitedDeal | None = None
        self._last_alerted_starts_at = ""

    def start(self) -> None:
        with self._lock:
            if not self._endpoint_url or self._thread is not None:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="DroidAlertsLimitedDeals",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.3)
        with self._lock:
            if self._thread is thread:
                self._thread = None

    @property
    def current_deal(self) -> LimitedDeal | None:
        with self._lock:
            return self._deal

    def was_alerted(self, deal: LimitedDeal) -> bool:
        with self._lock:
            return self._last_alerted_starts_at == deal.starts_at

    def mark_alerted(self, deal: LimitedDeal) -> None:
        with self._lock:
            self._last_alerted_starts_at = deal.starts_at
            self._write_cache_locked()

    def _run(self) -> None:
        now = _as_utc(self._clock())
        attempted_hour: datetime | None = None
        cached = self._read_cache(now)
        if cached is not None:
            cached_portrait = self._cached_portrait(cached)
            self._publish(
                LimitedDealStatus(
                    deal=cached,
                    source="cache",
                    error="",
                    checked_at=None,
                    next_fetch_at=now,
                    portrait_path=cached_portrait,
                )
            )

        fetch_on_start = True
        while not self._stop_event.is_set():
            now = _as_utc(self._clock())
            due = (
                now
                if fetch_on_start
                else next_limited_deal_fetch_at(now, attempted_hour=attempted_hour)
            )
            fetch_on_start = False
            delay = max(0.0, (due - now).total_seconds())
            if delay > 0 and self._stop_event.wait(delay):
                return
            now = _as_utc(self._clock())
            attempted_hour = now.replace(minute=0, second=0, microsecond=0)
            try:
                deal = self._fetcher(self._endpoint_url)
                if not deal.is_current(now):
                    raise ValueError("Server returned a Limited Deal outside the current hour")
            except (OSError, ValueError, urllib.error.URLError, TimeoutError) as exc:
                current_deal = self.current_deal
                if current_deal is not None and not current_deal.is_current(now):
                    current_deal = None
                self._publish(
                    LimitedDealStatus(
                        deal=current_deal,
                        source="network",
                        error=str(exc) or "Could not fetch the current Limited Deal",
                        checked_at=now,
                        next_fetch_at=next_limited_deal_fetch_at(now, attempted_hour=attempted_hour),
                        portrait_path=(
                            self._cached_portrait(current_deal)
                            if current_deal is not None
                            else None
                        ),
                    )
                )
                continue

            with self._lock:
                self._deal = deal
                self._write_cache_locked()
            portrait_path = self._cached_portrait(deal)
            next_fetch_at = next_limited_deal_fetch_at(
                now,
                attempted_hour=attempted_hour,
            )
            self._publish(
                LimitedDealStatus(
                    deal=deal,
                    source="network",
                    error="",
                    checked_at=now,
                    next_fetch_at=next_fetch_at,
                    portrait_path=portrait_path,
                )
            )
            if portrait_path is None:
                try:
                    downloaded = self._portrait_fetcher(
                        self._endpoint_url,
                        deal,
                        self._portrait_cache_dir,
                    )
                except (OSError, ValueError, urllib.error.URLError, TimeoutError):
                    downloaded = None
                if downloaded is not None and _valid_png(downloaded):
                    self._publish(
                        LimitedDealStatus(
                            deal=deal,
                            source="portrait",
                            error="",
                            checked_at=now,
                            next_fetch_at=next_fetch_at,
                            portrait_path=downloaded,
                        )
                    )

    @property
    def _portrait_cache_dir(self) -> Path:
        return self._cache_path.parent / "limited_deal_icons"

    def _cached_portrait(self, deal: LimitedDeal) -> Path | None:
        path = self._portrait_cache_dir / f"{deal.droid_id}_{deal.mutation.casefold()}.png"
        return path if _valid_png(path) else None

    def _read_cache(self, now: datetime) -> LimitedDeal | None:
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, Mapping):
                return None
            deal = LimitedDeal.from_payload(payload.get("deal"))
            last_alerted = str(payload.get("lastAlertedStartsAt") or "")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        with self._lock:
            self._last_alerted_starts_at = last_alerted
            if deal.is_current(now):
                self._deal = deal
                return deal
        return None

    def _write_cache_locked(self) -> None:
        if self._deal is None:
            return
        payload = {
            "deal": self._deal.to_payload(),
            "lastAlertedStartsAt": self._last_alerted_starts_at,
        }
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._cache_path.with_suffix(self._cache_path.suffix + ".tmp")
            temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temp.replace(self._cache_path)
        except OSError:
            return

    def _publish(self, status: LimitedDealStatus) -> None:
        if self._stop_event.is_set():
            return
        try:
            self._callback(status)
        except Exception:
            return


def _parse_utc(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _normalized_utc_iso(value: object) -> str:
    try:
        parsed = _parse_utc(str(value or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("Limited Deal response has an invalid timestamp") from exc
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _valid_png(path: Path) -> bool:
    try:
        return path.is_file() and path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False
