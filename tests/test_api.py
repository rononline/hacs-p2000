"""Regression tests for the P2000 response parsers."""

import importlib.util
import json
import unittest
from pathlib import Path


API_PATH = (
    Path(__file__).parents[1] / "custom_components" / "p2000" / "api.py"
)
SPEC = importlib.util.spec_from_file_location("p2000_api", API_PATH)
API_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(API_MODULE)


class FakeResponse:
    def __init__(self, status, data=None, text=None):
        self.status = status
        self._data = data
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def json(self, content_type=None):
        return self._data

    async def text(self):
        if self._text is not None:
            return self._text
        return json.dumps(self._data)


class FakeSession:
    def __init__(self, responses):
        self._responses = iter(responses)

    def get(self, *args, **kwargs):
        return next(self._responses)


class TestApiFallback(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_primary_schema_uses_rss_backup(self):
        rss = """\
<rss><channel><item>
  <title>0102998 - A1 Teststraat Amsterdam</title>
  <code>0102998</code>
  <message>A1 Teststraat Amsterdam</message>
  <RegName>Amsterdam Amstelland</RegName>
  <Dienst>Brandweerdiensten</Dienst>
  <pubDate>Wed, 24 Jun 2026 13:38:16 +0100</pubDate>
</item></channel></rss>
"""
        api = API_MODULE.P2000Api(
            FakeSession(
                [
                    FakeResponse(200, data=[]),
                    FakeResponse(200, text=rss),
                ]
            )
        )

        with self.assertLogs(level="WARNING"):
            result = await api.get_data({})

        self.assertEqual("rss", result["source"])


class TestJsonParser(unittest.TestCase):
    def setUp(self):
        self.api = API_MODULE.P2000Api(None)
        self.data = {
            "meldingen": [
                {
                    "melding": "Gezamenlijk incident",
                    "tekstmelding": "Gezamenlijk incident",
                    "dienstid": "1",
                    "dienst": "Politie",
                    "regio": "Eenheid Midden-Nederland",
                    "plaats": "3421BV  Oudewater",
                    "prio1": "0",
                    "capcodes": [{"capcode": "POLITIE"}],
                    "subitems": [
                        {
                            "melding": "Brandweer naar Oudewater",
                            "tekstmelding": "Brandweer naar Oudewater",
                            "dienstid": "2",
                            "dienst": "Brandweer",
                            "regio": "Utrecht",
                            "plaats": "3421BV  Oudewater",
                            "prio1": "1",
                            "capcodes": [{"capcode": "BRAND"}],
                            "lat": "52.0",
                            "lon": "4.0",
                        },
                        {
                            "melding": "Lifeliner naar Oudewater",
                            "tekstmelding": "Lifeliner naar Oudewater",
                            "dienstid": "5",
                            "dienst": "Lifeliner",
                            "regio": "Rotterdam-Rijnmond",
                            "plaats": "3421BV  Oudewater",
                            "prio1": "1",
                            "capcodes": [{"capcode": "HELI"}],
                        },
                    ],
                }
            ]
        }

    def test_returns_the_matching_subitem(self):
        result = self.api._parse_json_response(
            self.data,
            {
                "diensten": ["2"],
                "capcodes": ["BRAND"],
                "regios": ["Utrecht"],
                "prio1": 1,
            },
        )

        self.assertEqual("Brandweer", result["dienst"])
        self.assertEqual("Brandweer naar Oudewater", result["melding"])
        self.assertEqual("Oudewater", result["plaats"])
        self.assertEqual("BRAND", result["capcode"])
        self.assertEqual(52.0, result["latitude"])
        self.assertEqual("primary", result["source"])

    def test_does_not_mix_filters_between_subitems(self):
        result = self.api._parse_json_response(
            self.data,
            {"diensten": ["2"], "capcodes": ["HELI"]},
        )

        self.assertIsNone(result)

    def test_lifeliner_filter_uses_service_id(self):
        result = self.api._parse_json_response(
            self.data, {"lifeliners": 1}
        )

        self.assertEqual("Lifeliner", result["dienst"])
        self.assertEqual("HELI", result["capcode"])

    def test_lifeliner_is_part_of_ambulance_service(self):
        result = self.api._parse_json_response(
            self.data, {"lifeliners": 1, "diensten": ["3"]}
        )

        self.assertEqual("Lifeliner", result["dienst"])

    def test_invalid_primary_payload_is_ignored(self):
        with self.assertRaises(API_MODULE.P2000ResponseError):
            self.api._parse_json_response([], {})
        with self.assertRaises(API_MODULE.P2000ResponseError):
            self.api._parse_json_response({"meldingen": "invalid"}, {})

    def test_empty_primary_feed_is_a_valid_response(self):
        self.assertIsNone(
            self.api._parse_json_response({"meldingen": []}, {})
        )

    def test_place_filter_does_not_match_inside_another_word(self):
        data = {
            "meldingen": [
                {
                    "melding": "Melding aan de Edeseweg",
                    "dienstid": "2",
                    "dienst": "Brandweer",
                    "regio": "Gelderland",
                    "plaats": "Wageningen",
                    "prio1": "1",
                    "capcodes": [],
                }
            ]
        }

        self.assertIsNone(
            self.api._parse_json_response(data, {"woonplaatsen": ["Ede"]})
        )

    def test_exact_primary_place_still_matches(self):
        result = self.api._parse_json_response(
            self.data, {"woonplaatsen": ["Oudewater"]}
        )

        self.assertIsNotNone(result)

    def test_primary_date_time_and_coordinates_are_normalized(self):
        melding = {
            "melding": "Testmelding",
            "tekstmelding": "Testmelding",
            "dienstid": "2",
            "dienst": "Brandweer",
            "datum": "24-06",
            "tijd": "13:20 - 13:22",
            "lat": "52.123",
            "lon": "4.456",
            "prio1": "1",
            "capcodes": [],
        }

        result = self.api._normalize_json_alert(melding)

        self.assertRegex(result["datum"], r"^\d{4}-06-24$")
        self.assertRegex(
            result["tijd"],
            r"^\d{4}-06-24T13:22:00[+-]\d{2}:\d{2}$",
        )
        self.assertEqual(52.123, result["latitude"])
        self.assertEqual(4.456, result["longitude"])

    def test_leap_day_is_normalized(self):
        date, timestamp = self.api._primary_datetime("29-02", "12:34")

        self.assertRegex(date, r"^\d{4}-02-29$")
        self.assertIn("T12:34:00", timestamp)

    def test_newest_incident_wins_over_older_concrete_service(self):
        data = {
            "meldingen": [
                {
                    "melding": "Nieuwste generieke melding",
                    "dienstid": "2",
                    "dienst": "Gereserveerd",
                    "regio": "Utrecht",
                    "plaats": "Utrecht",
                    "prio1": "1",
                    "capcodes": [],
                },
                {
                    "melding": "Oudere concrete melding",
                    "dienstid": "2",
                    "dienst": "Brandweer",
                    "regio": "Utrecht",
                    "plaats": "Utrecht",
                    "prio1": "1",
                    "capcodes": [],
                },
            ]
        }

        result = self.api._parse_json_response(data, {"diensten": ["2"]})

        self.assertEqual("Nieuwste generieke melding", result["melding"])


class TestRssParser(unittest.TestCase):
    def setUp(self):
        self.api = API_MODULE.P2000Api(None)
        self.rss = """\
<rss>
  <channel>
    <item>
      <title>2029577 - Prio 1 Incidentstraat Amsterdam</title>
      <code>2029577</code>
      <message>Prio 1 Incidentstraat Amsterdam</message>
      <lon></lon>
      <lat></lat>
      <RegName>Gereserveerd</RegName>
      <Dienst>Gereserveerd</Dienst>
      <pubDate>Wed, 24 Jun 2026 13:38:16 +0100</pubDate>
    </item>
    <item>
      <title>0102998 - Prio 1 Incidentstraat Amsterdam</title>
      <code>0102998</code>
      <message>Prio 1 Incidentstraat Amsterdam</message>
      <lon>4.8</lon>
      <lat>52.3</lat>
      <RegName>Amsterdam Amstelland</RegName>
      <Dienst>Brandweerdiensten</Dienst>
      <pubDate>Wed, 24 Jun 2026 13:38:16 +0100</pubDate>
    </item>
    <item>
      <title>1420059 - A1 Lifeliner naar Gouda</title>
      <code>1420059</code>
      <message>A1 Lifeliner naar Gouda</message>
      <RegName>Rotterdam Rijnmond</RegName>
      <Dienst>Lifeliner</Dienst>
      <pubDate>Wed, 24 Jun 2026 13:37:00 +0100</pubDate>
    </item>
  </channel>
</rss>
"""

    def test_recognizes_prio_word_and_groups_duplicate_items(self):
        result = self.api._parse_rss_response(
            self.rss, {"prio1": 1, "diensten": ["2"]}
        )

        self.assertEqual("Brandweerdiensten", result["dienst"])
        self.assertEqual("1", result["prio"])
        self.assertEqual("2026-06-24", result["datum"])
        self.assertEqual(52.3, result["latitude"])
        self.assertEqual(
            "2026-06-24T13:38:16+01:00", result["tijd"]
        )
        self.assertEqual(
            {"2029577", "0102998"},
            {entry["capcode"] for entry in result["capcodes"]},
        )
        self.assertEqual("rss", result["source"])

    def test_capcode_filter_matches_grouped_incident(self):
        result = self.api._parse_rss_response(
            self.rss, {"capcodes": ["2029577"]}
        )

        self.assertEqual("Brandweerdiensten", result["dienst"])
        self.assertEqual(52.3, result["latitude"])

    def test_lifeliner_filter(self):
        result = self.api._parse_rss_response(
            self.rss, {"lifeliners": 1, "diensten": ["3"]}
        )

        self.assertEqual("A1 Lifeliner naar Gouda", result["melding"])
        self.assertEqual("1", result["prio"])

    def test_invalid_xml_raises_communication_error(self):
        with self.assertRaises(API_MODULE.P2000CommunicationError):
            self.api._parse_rss_response("<rss>", {})

    def test_prio_filter_rejects_unknown_letter_one_codes(self):
        rss = self.rss.replace(
            "Prio 1 Incidentstraat Amsterdam",
            "B1 Incidentstraat Amsterdam",
        )

        result = self.api._parse_rss_response(rss, {"prio1": 1})

        self.assertEqual("A1 Lifeliner naar Gouda", result["melding"])

    def test_rss_place_filter_does_not_match_inside_another_word(self):
        result = self.api._parse_rss_response(
            self.rss, {"woonplaatsen": ["Dam"]}
        )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
