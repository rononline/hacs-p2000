import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
import urllib.parse

_LOGGER = logging.getLogger(__name__)

_POSTCODE_PREFIX = re.compile(r'^\d{4}\s*[A-Z]{2}\s+')
_PRIO1_RSS = re.compile(r'^[A-Z]\s*1[\s,\[]', re.IGNORECASE)

DIENSTID_LIFELINER = "5"


class P2000CommunicationError(Exception):
    """Raised when all P2000 data sources are unreachable."""


class P2000Api:
    URL_PRIMARY = "https://beta.alarmeringdroid.nl/api2/find/"
    URL_BACKUP = "https://p2000.brandweer-berkel-enschot.nl/homeassistant/rss.asp"

    def __init__(self, session):
        self._session = session

    async def get_data(self, api_filter):
        if not api_filter:
            api_filter = {}

        try:
            json_str = json.dumps(api_filter, separators=(",", ":"))
            url = self.URL_PRIMARY + urllib.parse.quote(json_str)

            async with self._session.get(url, allow_redirects=False, timeout=8) as response:
                if response.status == 200:
                    try:
                        data = await response.json(content_type=None)
                    except Exception:
                        text = await response.text()
                        data = json.loads(text)
                    # Primaire API bereikbaar: direct returnen, geen RSS-fallback
                    return self._parse_json_response(data, api_filter)
                else:
                    _LOGGER.warning(
                        "P2000 API fout (Status %s). Overschakelen naar backup.",
                        response.status,
                    )

        except Exception as exc:
            _LOGGER.warning("Fout bij P2000 API: %s. Start backup procedure.", exc)

        return await self._get_rich_rss_backup(api_filter)

    # --- helpers voor hoofd- én subitem-analyse ---

    def _clean_plaats(self, plaats: str) -> str:
        return _POSTCODE_PREFIX.sub('', plaats).strip()

    def _all_dienst_ids(self, melding):
        """Verzamel dienstids van hoofdmelding én alle subitems."""
        ids = {str(melding.get("dienstid") or "")}
        for sub in (melding.get("subitems") or []):
            ids.add(str(sub.get("dienstid") or ""))
        ids.discard("")
        return ids

    def _all_capcodes(self, melding):
        """Verzamel capcodes van hoofdmelding én alle subitems."""
        codes = {c.get("capcode", "") for c in (melding.get("capcodes") or [])}
        for sub in (melding.get("subitems") or []):
            codes |= {c.get("capcode", "") for c in (sub.get("capcodes") or [])}
        codes.discard("")
        return codes

    def _has_prio1(self, melding):
        if melding.get("prio1") == "1":
            return True
        return any(sub.get("prio1") == "1" for sub in (melding.get("subitems") or []))

    def _is_lifeliner(self, melding):
        return DIENSTID_LIFELINER in self._all_dienst_ids(melding)

    def _matches_filter(self, melding, wanted_places, wanted_services, wanted_regios,
                        wanted_capcodes, prio1_only, lifeliners_only):
        if prio1_only and not self._has_prio1(melding):
            return False

        if lifeliners_only and not self._is_lifeliner(melding):
            return False

        if wanted_places:
            plaats = (melding.get("plaats") or "").lower()
            tekst = (melding.get("melding") or melding.get("tekstmelding") or "").lower()
            if not any(p in plaats or p in tekst for p in wanted_places):
                return False

        if wanted_regios:
            regio = (melding.get("regio") or "").lower()
            if not any(r in regio for r in wanted_regios):
                return False

        if wanted_services:
            if not any(code in self._all_dienst_ids(melding) for code in wanted_services):
                return False

        if wanted_capcodes:
            if not wanted_capcodes & self._all_capcodes(melding):
                return False

        return True

    def _parse_json_response(self, data, api_filter):
        meldingen = data.get("meldingen") or []
        if not meldingen:
            return None

        wanted_places = [
            p.lower()
            for p in api_filter.get("woonplaatsen", []) + api_filter.get("gemeenten", [])
        ]
        wanted_services = [str(s) for s in api_filter.get("diensten", [])]
        wanted_regios = [r.lower() for r in api_filter.get("regios", [])]
        wanted_capcodes = set(api_filter.get("capcodes", []))
        prio1_only = bool(api_filter.get("prio1"))
        lifeliners_only = bool(api_filter.get("lifeliners"))

        for melding in meldingen:
            if melding.get("plaats"):
                melding["plaats"] = self._clean_plaats(melding["plaats"])

        filter_args = (wanted_places, wanted_services, wanted_regios,
                       wanted_capcodes, prio1_only, lifeliners_only)

        # Twee passes: eerst voorkeur voor meldingen met een concrete dienst
        match = None
        for skip_gereserveerd in (True, False):
            for melding in meldingen:
                dienst = (melding.get("dienst") or "").lower()
                if skip_gereserveerd and dienst in ("gereserveerd", ""):
                    continue
                if self._matches_filter(melding, *filter_args):
                    match = melding
                    break
            if match:
                break

        if match:
            if "lat" in match:
                match["latitude"] = match.pop("lat")
            if "lon" in match:
                match["longitude"] = match.pop("lon")
        return match

    # --- RSS backup ---

    def _safe_text(self, item, tag_name):
        el = item.find(tag_name)
        if el is not None and el.text:
            return el.text
        return ""

    def _parse_rss_date(self, pub_date):
        try:
            return parsedate_to_datetime(pub_date).strftime("%Y-%m-%d")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d")

    async def _get_rich_rss_backup(self, api_filter):
        wanted_services = [str(s) for s in api_filter.get("diensten", [])]
        wanted_cities = [
            g.lower()
            for g in api_filter.get("gemeenten", []) + api_filter.get("woonplaatsen", [])
        ]
        wanted_regios = [r.lower() for r in api_filter.get("regios", [])]
        wanted_capcodes = set(api_filter.get("capcodes", []))
        prio1_only = bool(api_filter.get("prio1"))
        lifeliners_only = bool(api_filter.get("lifeliners"))
        service_mapping = {
            "1": "politie",
            "2": "brandweer",
            "3": "ambu",
            "4": "kustwacht",
            "5": "lifeliner",
        }

        try:
            async with self._session.get(
                self.URL_BACKUP, allow_redirects=True, timeout=10
            ) as response:
                if response.status != 200:
                    raise P2000CommunicationError(
                        f"Backup RSS HTTP fout: status {response.status}"
                    )

                content = await response.text()
                try:
                    root = ET.fromstring(content)
                except ET.ParseError as exc:
                    raise P2000CommunicationError(f"Backup RSS XML parse fout: {exc}") from exc

                for item in root.findall(".//item"):
                    title = self._safe_text(item, "title")
                    message = self._safe_text(item, "message")
                    pub_date = self._safe_text(item, "pubDate")
                    rss_dienst = self._safe_text(item, "Dienst").lower()
                    code = self._safe_text(item, "code")
                    reg_name = self._safe_text(item, "RegName")
                    latitude = self._safe_text(item, "lat") or None
                    longitude = self._safe_text(item, "lon") or None

                    full_text = (title + " " + message).lower()

                    if prio1_only:
                        if not (_PRIO1_RSS.match(title) or _PRIO1_RSS.match(message)):
                            continue

                    if lifeliners_only:
                        if "lifeliner" not in rss_dienst and "lifeliner" not in full_text:
                            continue

                    if wanted_cities:
                        if not any(city in full_text for city in wanted_cities):
                            continue

                    if wanted_regios:
                        if not any(r in reg_name.lower() for r in wanted_regios):
                            continue

                    if wanted_capcodes:
                        if not code or code not in wanted_capcodes:
                            continue

                    if wanted_services:
                        if not any(
                            service_mapping.get(c, "") in rss_dienst
                            for c in wanted_services
                        ):
                            continue

                    return {
                        "melding": message or title,
                        "tekstmelding": message or title,
                        "tijd": pub_date,
                        "datum": self._parse_rss_date(pub_date),
                        "capcode": code or "RSS-BACKUP",
                        "dienst": self._safe_text(item, "Dienst") or "RSS",
                        "regio": reg_name,
                        "prio": "Unknown",
                        "latitude": latitude,
                        "longitude": longitude,
                    }

                return None

        except P2000CommunicationError:
            raise
        except Exception as exc:
            raise P2000CommunicationError(f"Fout bij backup RSS: {exc}") from exc
