"""Regression tests for the P2000 response parsers."""

import importlib.util
import unittest
from pathlib import Path


API_PATH = (
    Path(__file__).parents[1] / "custom_components" / "p2000" / "api.py"
)
SPEC = importlib.util.spec_from_file_location("p2000_api", API_PATH)
API_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(API_MODULE)


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
        self.assertEqual("52.0", result["latitude"])
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
        self.assertIsNone(self.api._parse_json_response([], {}))
        self.assertIsNone(
            self.api._parse_json_response({"meldingen": "invalid"}, {})
        )


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
        self.assertEqual("52.3", result["latitude"])
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
        self.assertEqual("52.3", result["latitude"])

    def test_lifeliner_filter(self):
        result = self.api._parse_rss_response(
            self.rss, {"lifeliners": 1, "diensten": ["3"]}
        )

        self.assertEqual("A1 Lifeliner naar Gouda", result["melding"])
        self.assertEqual("1", result["prio"])

    def test_invalid_xml_raises_communication_error(self):
        with self.assertRaises(API_MODULE.P2000CommunicationError):
            self.api._parse_rss_response("<rss>", {})


if __name__ == "__main__":
    unittest.main()
