import json
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

_LOGGER = logging.getLogger(__name__)

_POSTCODE_PREFIX = re.compile(r"^\d{4}\s*[A-Z]{2}\s+")
_PRIO1_RSS = re.compile(
    r"^(?:A\s*1|P\s*1|PRIO\s*1)(?=[\s,\[])",
    re.IGNORECASE,
)

DIENSTID_LIFELINER = "5"
_LOCAL_TIMEZONE = ZoneInfo("Europe/Amsterdam")

_SERVICE_MAPPING = {
    "1": "politie",
    "2": "brandweer",
    "3": "ambu",
    "4": "kustwacht",
    "5": "lifeliner",
}


class P2000CommunicationError(Exception):
    """Raised when all P2000 data sources are unreachable."""


class P2000ResponseError(ValueError):
    """Raised when a P2000 data source returns an invalid response."""


class P2000Api:
    URL_PRIMARY = "https://beta.alarmeringdroid.nl/api2/find/"
    URL_BACKUP = (
        "https://p2000.brandweer-berkel-enschot.nl/homeassistant/rss.asp"
    )

    def __init__(self, session):
        self._session = session
        self._primary_failed = False

    async def get_data(self, api_filter):
        if not api_filter:
            api_filter = {}

        try:
            json_str = json.dumps(api_filter, separators=(",", ":"))
            url = self.URL_PRIMARY + urllib.parse.quote(json_str, safe="")

            async with self._session.get(
                url, allow_redirects=False, timeout=8
            ) as response:
                if response.status == 200:
                    try:
                        data = await response.json(content_type=None)
                    except Exception:
                        text = await response.text()
                        data = json.loads(text)

                    if self._primary_failed:
                        _LOGGER.info("P2000 primaire API hersteld")
                        self._primary_failed = False

                    # Een bereikbare primaire API zonder match is geen storing.
                    return self._parse_json_response(data, api_filter)

                if not self._primary_failed:
                    _LOGGER.warning(
                        "P2000 API fout (status %s). Overschakelen naar backup",
                        response.status,
                    )
                    self._primary_failed = True

        except Exception as exc:
            if not self._primary_failed:
                _LOGGER.warning(
                    "Fout bij P2000 API: %s. Start backup procedure", exc
                )
                self._primary_failed = True

        return await self._get_rich_rss_backup(api_filter)

    @staticmethod
    def _clean_plaats(plaats):
        return _POSTCODE_PREFIX.sub("", plaats).strip()

    @staticmethod
    def _contains_term(text, term):
        """Match a configured term without matching inside another word."""
        return re.search(
            rf"(?<!\w){re.escape(term)}(?!\w)",
            text,
            re.IGNORECASE,
        ) is not None

    @classmethod
    def _matches_place(cls, plaats, tekst, wanted_places):
        cleaned_place = cls._clean_plaats(plaats).casefold() if plaats else ""
        return any(
            cleaned_place == wanted.casefold()
            or cls._contains_term(tekst, wanted)
            for wanted in wanted_places
        )

    @staticmethod
    def _coordinate(value):
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _primary_datetime(date_value, time_value):
        """Convert the primary API's DD-MM and HH:MM values to ISO 8601."""
        if not date_value:
            return "", ""

        try:
            day, month = (int(part) for part in date_value.split("-", 1))
            now = datetime.now(_LOCAL_TIMEZONE)
            candidates = []
            for year in range(now.year - 4, now.year + 5):
                try:
                    candidates.append(
                        datetime(year, month, day, tzinfo=_LOCAL_TIMEZONE)
                    )
                except ValueError:
                    continue
            if not candidates:
                raise ValueError("Ongeldige datum")
            date = min(candidates, key=lambda candidate: abs(candidate - now))
        except (TypeError, ValueError):
            return date_value, time_value or ""

        if not time_value:
            return date.date().isoformat(), ""

        # Samengevoegde incidenten kunnen een bereik bevatten; gebruik het
        # meest recente tijdstip uit de API-waarde.
        latest_time = time_value.rsplit("-", 1)[-1].strip()
        try:
            hour, minute = (int(part) for part in latest_time.split(":", 1))
            timestamp = date.replace(hour=hour, minute=minute)
        except (TypeError, ValueError):
            return date.date().isoformat(), time_value

        return date.date().isoformat(), timestamp.isoformat()

    @staticmethod
    def _json_capcodes(melding):
        return {
            str(capcode.get("capcode") or "")
            for capcode in (melding.get("capcodes") or [])
            if capcode.get("capcode")
        }

    def _matches_json_filter(
        self,
        melding,
        wanted_places,
        wanted_services,
        wanted_regios,
        wanted_capcodes,
        prio1_only,
        lifeliners_only,
    ):
        """Apply every selected filter to the same individual alert."""
        dienst_id = str(melding.get("dienstid") or "")

        if prio1_only and str(melding.get("prio1") or "") != "1":
            return False

        if lifeliners_only and dienst_id != DIENSTID_LIFELINER:
            return False

        if wanted_places:
            plaats = melding.get("plaats") or ""
            tekst = (
                melding.get("tekstmelding")
                or melding.get("melding")
                or ""
            )
            if not self._matches_place(plaats, tekst, wanted_places):
                return False

        if wanted_regios:
            regio = (melding.get("regio") or "").lower()
            if not any(regio_filter in regio for regio_filter in wanted_regios):
                return False

        if wanted_services:
            service_matches = dienst_id in wanted_services
            if dienst_id == DIENSTID_LIFELINER and "3" in wanted_services:
                service_matches = True
            if not service_matches:
                return False

        if wanted_capcodes:
            if not wanted_capcodes & self._json_capcodes(melding):
                return False

        return True

    def _normalize_json_alert(self, melding):
        alert = dict(melding)
        alert.pop("subitems", None)

        if alert.get("plaats"):
            alert["plaats"] = self._clean_plaats(alert["plaats"])

        latitude = self._coordinate(alert.pop("lat", None))
        longitude = self._coordinate(alert.pop("lon", None))
        capcodes = list(alert.get("capcodes") or [])
        text = alert.get("tekstmelding") or alert.get("melding") or ""
        prio1 = str(alert.get("prio1") or "") == "1"
        date, time = self._primary_datetime(
            alert.get("datum"), alert.get("tijd")
        )

        alert.update(
            {
                "melding": alert.get("melding") or text,
                "tekstmelding": text,
                "capcode": (
                    str(capcodes[0].get("capcode") or "") if capcodes else ""
                ),
                "capcodes": capcodes,
                "prio": "1" if prio1 else "Unknown",
                "datum": date,
                "tijd": time,
                "latitude": latitude,
                "longitude": longitude,
                "source": "primary",
            }
        )
        return alert

    def _parse_json_response(self, data, api_filter):
        if not isinstance(data, dict):
            raise P2000ResponseError("Primaire API-response is geen object")

        if "meldingen" not in data:
            raise P2000ResponseError(
                "Primaire API-response bevat geen 'meldingen'"
            )

        meldingen = data["meldingen"]
        if not isinstance(meldingen, list):
            raise P2000ResponseError(
                "Primaire API-veld 'meldingen' is geen lijst"
            )

        wanted_places = [
            place.lower()
            for place in (
                api_filter.get("woonplaatsen", [])
                + api_filter.get("gemeenten", [])
            )
        ]
        wanted_services = {
            str(service) for service in api_filter.get("diensten", [])
        }
        wanted_regios = [
            regio.lower() for regio in api_filter.get("regios", [])
        ]
        wanted_capcodes = {
            str(capcode) for capcode in api_filter.get("capcodes", [])
        }
        filter_args = (
            wanted_places,
            wanted_services,
            wanted_regios,
            wanted_capcodes,
            bool(api_filter.get("prio1")),
            bool(api_filter.get("lifeliners")),
        )

        # Per incident (in feedvolgorde) de beste subitem kiezen.
        for melding in meldingen:
            items = [melding] + list(melding.get("subitems") or [])
            for skip_reserved in (True, False):
                for item in items:
                    dienst = (item.get("dienst") or "").lower()
                    if skip_reserved and dienst in ("", "gereserveerd"):
                        continue
                    if self._matches_json_filter(item, *filter_args):
                        return self._normalize_json_alert(item)

        return None

    @staticmethod
    def _safe_text(item, tag_name):
        element = item.find(tag_name)
        if element is not None and element.text:
            return element.text.strip()
        return ""

    @staticmethod
    def _parse_rss_datetime(pub_date):
        try:
            timestamp = parsedate_to_datetime(pub_date)
            return timestamp.date().isoformat(), timestamp.isoformat()
        except (TypeError, ValueError, OverflowError):
            return "", pub_date or ""

    def _rss_item(self, item):
        title = self._safe_text(item, "title")
        message = self._safe_text(item, "message")
        return {
            "title": title,
            "message": message,
            "pub_date": self._safe_text(item, "pubDate"),
            "dienst": self._safe_text(item, "Dienst"),
            "code": self._safe_text(item, "code"),
            "regio": self._safe_text(item, "RegName"),
            "latitude": self._coordinate(self._safe_text(item, "lat")),
            "longitude": self._coordinate(self._safe_text(item, "lon")),
            "full_text": f"{title} {message}".lower(),
        }

    @staticmethod
    def _rss_variant_score(variant):
        dienst = variant["dienst"].lower()
        regio = variant["regio"].lower()
        score = 0
        if dienst not in ("", "gereserveerd"):
            score += 4
        if regio not in ("", "gereserveerd"):
            score += 2
        if variant["latitude"] and variant["longitude"]:
            score += 1
        return score

    def _matches_rss_filter(self, variants, api_filter):
        full_text = " ".join(variant["full_text"] for variant in variants)
        diensten = {variant["dienst"].lower() for variant in variants}
        regios = {variant["regio"].lower() for variant in variants}
        capcodes = {variant["code"] for variant in variants if variant["code"]}
        is_lifeliner = (
            any("lifeliner" in dienst for dienst in diensten)
            or "lifeliner" in full_text
        )

        if api_filter.get("prio1") and not any(
            _PRIO1_RSS.match(variant["message"])
            or _PRIO1_RSS.match(variant["title"])
            for variant in variants
        ):
            return False

        if api_filter.get("lifeliners"):
            if not is_lifeliner:
                return False

        wanted_places = [
            place.lower()
            for place in (
                api_filter.get("woonplaatsen", [])
                + api_filter.get("gemeenten", [])
            )
        ]
        if wanted_places and not any(
            self._contains_term(full_text, place) for place in wanted_places
        ):
            return False

        wanted_regios = [
            regio.lower() for regio in api_filter.get("regios", [])
        ]
        if wanted_regios and not any(
            wanted in regio
            for wanted in wanted_regios
            for regio in regios
        ):
            return False

        wanted_capcodes = {
            str(capcode) for capcode in api_filter.get("capcodes", [])
        }
        if wanted_capcodes and not wanted_capcodes & capcodes:
            return False

        wanted_services = {
            str(service) for service in api_filter.get("diensten", [])
        }
        if wanted_services:
            mapped = {
                _SERVICE_MAPPING[service]
                for service in wanted_services
                if service in _SERVICE_MAPPING
            }
            service_matches = any(
                name in dienst for name in mapped for dienst in diensten
            )
            if is_lifeliner and "3" in wanted_services:
                service_matches = True
            if not service_matches:
                return False

        return True

    def _normalize_rss_alert(self, variants):
        best = max(variants, key=self._rss_variant_score)
        capcodes = [
            {"capcode": code, "omschrijving": None}
            for code in dict.fromkeys(
                variant["code"] for variant in variants if variant["code"]
            )
        ]
        text = best["message"] or best["title"]
        date, time = self._parse_rss_datetime(best["pub_date"])
        prio1 = any(
            _PRIO1_RSS.match(variant["message"])
            or _PRIO1_RSS.match(variant["title"])
            for variant in variants
        )

        return {
            "melding": text,
            "tekstmelding": text,
            "tijd": time,
            "datum": date,
            "capcode": capcodes[0]["capcode"] if capcodes else "",
            "capcodes": capcodes,
            "dienst": best["dienst"] or "RSS",
            "regio": best["regio"],
            "prio": "1" if prio1 else "Unknown",
            "prio1": "1" if prio1 else "0",
            "latitude": best["latitude"],
            "longitude": best["longitude"],
            "source": "rss",
        }

    def _parse_rss_response(self, content, api_filter):
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise P2000CommunicationError(
                f"Backup RSS XML parse fout: {exc}"
            ) from exc

        incidents = {}
        for item in root.findall(".//item"):
            variant = self._rss_item(item)
            text = variant["message"] or variant["title"]
            key = (text, variant["pub_date"])
            incidents.setdefault(key, []).append(variant)

        for variants in incidents.values():
            if self._matches_rss_filter(variants, api_filter):
                return self._normalize_rss_alert(variants)

        return None

    async def _get_rich_rss_backup(self, api_filter):
        try:
            async with self._session.get(
                self.URL_BACKUP, allow_redirects=True, timeout=10
            ) as response:
                if response.status != 200:
                    raise P2000CommunicationError(
                        f"Backup RSS HTTP fout: status {response.status}"
                    )

                return self._parse_rss_response(
                    await response.text(), api_filter
                )

        except P2000CommunicationError:
            raise
        except Exception as exc:
            raise P2000CommunicationError(
                f"Fout bij backup RSS: {exc}"
            ) from exc
