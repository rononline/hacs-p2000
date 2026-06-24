import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
import urllib.parse

_LOGGER = logging.getLogger(__name__)

_POSTCODE_PREFIX = re.compile(r'^\d{4}\s*[A-Z]{2}\s+')


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

        # Alleen hier bij een communicatiefout met de primaire API
        return await self._get_rich_rss_backup(api_filter)

    def _clean_plaats(self, plaats: str) -> str:
        return _POSTCODE_PREFIX.sub('', plaats).strip()

    def _parse_json_response(self, data, api_filter):
        meldingen = data.get("meldingen") or []
        if not meldingen:
            return None

        wanted_places = [
            p.lower()
            for p in api_filter.get("woonplaatsen", []) + api_filter.get("gemeenten", [])
        ]
        wanted_services = api_filter.get("diensten", [])
        wanted_regios = [r.lower() for r in api_filter.get("regios", [])]
        wanted_capcodes = set(api_filter.get("capcodes", []))
        prio1_only = bool(api_filter.get("prio1"))
        lifeliners_only = bool(api_filter.get("lifeliners"))

        for melding in meldingen:
            if melding.get("plaats"):
                melding["plaats"] = self._clean_plaats(melding["plaats"])

            if prio1_only and melding.get("prio1") != "1":
                continue

            if lifeliners_only and not melding.get("lifeliner"):
                continue

            if wanted_places:
                plaats = (melding.get("plaats") or "").lower()
                tekst = (melding.get("melding") or melding.get("tekstmelding") or "").lower()
                if not any(p in plaats or p in tekst for p in wanted_places):
                    continue

            if wanted_regios:
                regio = (melding.get("regio") or "").lower()
                if not any(r in regio for r in wanted_regios):
                    continue

            if wanted_services:
                dienst_id = str(melding.get("dienstid") or "")
                if not any(str(code) == dienst_id for code in wanted_services):
                    continue

            if wanted_capcodes:
                melding_capcodes = {
                    c.get("capcode", "") for c in (melding.get("capcodes") or [])
                }
                if not wanted_capcodes & melding_capcodes:
                    continue

            if "lat" in melding:
                melding["latitude"] = melding.pop("lat")
            if "lon" in melding:
                melding["longitude"] = melding.pop("lon")
            return melding

        return None

    def _safe_text(self, item, tag_name):
        el = item.find(tag_name)
        if el is not None and el.text:
            return el.text
        return ""

    async def _get_rich_rss_backup(self, api_filter):
        wanted_services = api_filter.get("diensten", [])
        wanted_cities = [
            g.lower()
            for g in api_filter.get("gemeenten", []) + api_filter.get("woonplaatsen", [])
        ]
        wanted_regios = [r.lower() for r in api_filter.get("regios", [])]
        wanted_capcodes = set(api_filter.get("capcodes", []))
        service_mapping = {
            "1": "politie",
            "2": "brandweer",
            "3": "ambu",
            "4": "kustwacht",
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
                            service_mapping.get(str(c), "") in rss_dienst
                            for c in wanted_services
                        ):
                            continue

                    return {
                        "melding": message or title,
                        "tekstmelding": message or title,
                        "tijd": pub_date,
                        "datum": datetime.now().strftime("%Y-%m-%d"),
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
