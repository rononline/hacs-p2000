import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
import urllib.parse

_LOGGER = logging.getLogger(__name__)

_POSTCODE_PREFIX = re.compile(r'^\d{4}\s*[A-Z]{2}\s+')


class P2000Api:
    URL_PRIMARY = "https://beta.alarmeringdroid.nl/api2/find/"
    URL_BACKUP = "http://p2000.brandweer-berkel-enschot.nl/homeassistant/rss.asp"

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
                        data = await response.json()
                    except Exception:
                        text = await response.text()
                        data = json.loads(text)

                    result = self._parse_json_response(data, api_filter)
                    if result:
                        return result
                else:
                    _LOGGER.warning(
                        "P2000 API fout (Status %s). Overschakelen naar Rijke RSS Backup.",
                        response.status,
                    )

        except Exception as exc:
            _LOGGER.warning("Fout bij P2000 API: %s. Start backup procedure.", exc)

        return await self._get_rich_rss_backup(api_filter)

    def _clean_plaats(self, plaats: str) -> str:
        """Strip postcode prefix, e.g. '3079XH  Rotterdam' → 'Rotterdam'."""
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

        for melding in meldingen:
            if melding.get("plaats"):
                melding["plaats"] = self._clean_plaats(melding["plaats"])

            if prio1_only and melding.get("prio1") != "1":
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
                    return None

                content = await response.text()
                try:
                    root = ET.fromstring(content)
                except ET.ParseError:
                    _LOGGER.error("Kon RSS XML niet parsen.")
                    return None

                items = root.findall(".//item")
                if not items:
                    return None

                for item in items:
                    title = self._safe_text(item, "title")
                    description = self._safe_text(item, "description")
                    pub_date = self._safe_text(item, "pubDate")
                    rss_dienst = self._safe_text(item, "dienst").lower()
                    latitude = self._safe_text(item, "lat") or None
                    longitude = self._safe_text(item, "lon") or None

                    full_text = (title + " " + description).lower()

                    if wanted_cities:
                        if not any(city in full_text for city in wanted_cities):
                            continue

                    if wanted_services:
                        if not any(
                            service_mapping.get(str(code), "") in rss_dienst
                            or service_mapping.get(str(code), "") in full_text
                            for code in wanted_services
                        ):
                            continue

                    return {
                        "melding": description or title,
                        "tijd": pub_date,
                        "datum": datetime.now().strftime("%Y-%m-%d"),
                        "capcode": "RSS-BACKUP",
                        "dienst": self._safe_text(item, "dienst") or "RSS",
                        "prio": "Unknown",
                        "latitude": latitude,
                        "longitude": longitude,
                    }

                return None

        except Exception as exc:
            _LOGGER.error("Fout bij verwerken backup RSS: %s", exc)
            return None
