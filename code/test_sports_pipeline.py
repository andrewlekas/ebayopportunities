"""Regression tests for the 2026-07-28 sports/set-needs overhaul."""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import alerts
import fetch_guide_csv
import main as scanner
from models import Listing, Opportunity, SoldComp, Valuation
from quality import tradeability_rejection
from report import _category
from targets import (configured_scan_entries, structured_target_mismatch)
from valuation.comps import card_number, years_in
from valuation.engine import ValuationEngine
from valuation.identity import identity_of, object_class
from valuation.price_guide import GuideQuote, PriceGuide


class TestSportsIdentityHardening(unittest.TestCase):
    def test_cross_century_seasons_do_not_become_1900(self):
        self.assertEqual(years_in("1999-00 Topps"), {"1999", "2000"})
        self.assertEqual(years_in("2003-04 Exquisite"), {"2003", "2004"})

    def test_sports_serial_is_not_a_card_number(self):
        self.assertIsNone(card_number(
            "2003 Exquisite LeBron James game-used jersey 17/75"))
        self.assertEqual(card_number("Pokemon Charizard 4/102"), "4")
        self.assertIsNone(identity_of("Pokemon Charizard 4/102").serial)
        self.assertEqual(
            identity_of("2003 Exquisite LeBron James #LJ1 17/75").number,
            "LJ1")

    def test_memorabilia_is_not_a_sports_card(self):
        title = "Tiger Woods UDA Tourney Worn Shirt Custom Framed Display"
        self.assertEqual(object_class(title), "memorabilia")
        self.assertEqual(_category("Tiger Woods UDA", title),
                         "Sports Memorabilia")
        self.assertEqual(
            object_class("2003 Upper Deck Tiger Woods Jersey Card #12"),
            "card")


class TestStructuredSportsTargets(unittest.TestCase):
    def test_a_grade_list_becomes_ONE_banded_query(self):
        """2026-08-08. Expanding every grade into its own eBay search made
        35 cards at five grades each cost 175 queries, and fetch was
        already 593s of a 27-minute run. A band is one query whose RESULTS
        are filtered - the same information for a fifth of the network.

        The grade is deliberately absent from the query text: asking eBay
        for "PSA 8" hides the PSA 7 and 9 copies the band also wants."""
        from targets import sports_target_entries, grade_band
        config = {"sports_targets": [{
            "player": "Michael Jordan", "year": 1986, "set": "Fleer",
            "card_number": "57", "grades": ["PSA 8", "PSA 9"]}]}
        entries = sports_target_entries(config)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["query"], "1986 Fleer Michael Jordan #57")
        self.assertEqual((entries[0]["grade_min"], entries[0]["grade_max"]),
                         (8.0, 9.0))

    def test_a_single_grade_widens_by_andrews_rule(self):
        """One lower, two higher is still the card he wants."""
        from targets import grade_band
        self.assertEqual(grade_band({"grade": 8}), (7.0, 10.0))
        self.assertEqual(grade_band({"grade": 2}), (1.0, 4.0))
        self.assertEqual(grade_band({"grade": 9}), (8.0, 10.0))
        self.assertEqual(grade_band({"grade_band": [1, 5]}), (1.0, 5.0))

    def test_the_band_is_enforced_on_returned_titles(self):
        """The query carries no grade, so the band is the only grade test."""
        from targets import structured_target_mismatch as mm
        q = "1986 Fleer Michael Jordan #57"
        ok = "1986 Fleer Michael Jordan #57 PSA 8"
        self.assertIsNone(mm(q, ok, grade_min=7, grade_max=10))
        self.assertIn("below target band",
                      mm(q, "1986 Fleer Michael Jordan #57 PSA 5",
                         grade_min=7, grade_max=10) or "")
        self.assertIn("above target band",
                      mm(q, "1986 Fleer Michael Jordan #57 PSA 10",
                         grade_min=1, grade_max=5) or "")
        self.assertIn("graded card",
                      mm(q, "1986 Fleer Michael Jordan #57 raw",
                         grade_min=7, grade_max=10) or "")

    def test_pokemon_and_other_targets_use_the_same_machinery(self):
        """Non-sports cards are still ONE specific card, so they get the
        same exact-target contract. 2026-08-08: Andrew's list included a
        1931 Wills Cinema Stars Disney and a 1940 Gum Inc Superman #1 -
        the CARD, not the comic."""
        from targets import configured_scan_entries
        config = {
            "pokemon_targets": [{
                "player": "Charizard", "year": 1999,
                "set": "Base Set 1st Edition", "card_number": "4",
                "grade": 8}],
            "other_targets": [{
                "subject": "Superman", "year": 1940, "set": "Gum Inc",
                "card_number": "1", "grade_band": [1, 5]}],
        }
        entries = {e["query"]: e for e in configured_scan_entries(config)}
        chz = entries["1999 Base Set 1st Edition Charizard #4"]
        self.assertEqual((chz["grade_min"], chz["grade_max"]), (7.0, 10.0))
        self.assertTrue(chz["structured_target"])
        sup = entries["1940 Gum Inc Superman #1"]
        self.assertEqual((sup["grade_min"], sup["grade_max"]), (1.0, 5.0))

    def test_a_zero_floor_survives_the_watchlist_merge(self):
        """0.0 == False in Python, so the old merge guard read a zero
        value_floor_override as unset and let a watchlist duplicate
        clobber it to its own floor. Flagged 2026-07-31, bit today."""
        from targets import configured_scan_entries
        config = {
            "set_needs": [{"query": "1999 1st Edition Pokemon Trader PSA 9",
                           "min_value": 0}],
            "watchlist": [{"query": "1999 1st Edition Pokemon Trader PSA 9",
                           "value_floor_override": 9999.0}],
        }
        entries = configured_scan_entries(config)
        trader = [e for e in entries if "Trader" in e["query"]]
        self.assertEqual(len(trader), 1)
        self.assertEqual(trader[0]["value_floor_override"], 0.0,
                         "the explicit zero floor must win")

    def test_structured_targets_carry_the_target_floor(self):
        """Grant Hill PSA 8-10 was valued 154 times on 2026-08-09 and never
        reached the tab: his cards are \$20-\$300 against a \$500 category
        floor. A named target is an explicit want, so it carries its own
        \$100 floor (per-target min_value overrides)."""
        from targets import sports_target_entries
        config = {"sports_targets": [
            {"player": "Grant Hill", "year": 1994, "grade_band": [8, 10]},
            {"player": "Frank Gore", "year": 2005, "grade_band": [8, 10],
             "min_value": 250},
        ]}
        e = sports_target_entries(config)
        self.assertEqual(e[0]["value_floor_override"], 100.0)
        self.assertEqual(e[1]["value_floor_override"], 250.0)

    def test_set_need_merges(self):
        config = {
            "sports_targets": [{
                "player": "Michael Jordan", "year": 1986, "set": "Fleer",
                "card_number": "57", "grades": ["PSA 8", "PSA 9"],
            }],
            "set_needs": [{
                "query": "1999 1st Edition Pokemon Trader PSA 9",
                "min_value": 0,
            }],
            "watchlist": [{
                "query": "1999 1st Edition Pokemon Trader PSA 9",
            }],
        }
        entries = configured_scan_entries(config)
        trader = [entry for entry in entries if "Pokemon Trader" in entry["query"]]
        self.assertEqual(len(trader), 1)
        self.assertTrue(trader[0]["set_need"])
        self.assertEqual(trader[0]["value_floor_override"], 0)

    def test_structured_target_rejects_wrong_card_and_novelty(self):
        target = "1986 Fleer Michael Jordan #57 PSA 8"
        self.assertIsNone(structured_target_mismatch(
            target, "1986 Fleer Michael Jordan #57 PSA 8 Bulls RC"))
        self.assertIsNotNone(structured_target_mismatch(
            target, "1986 Fleer Michael Jordan #21 PSA 8"))
        self.assertIsNotNone(structured_target_mismatch(
            target, "1998 Fleer Michael Jordan #57 PSA 8"))


class TestSportsGuideRouting(unittest.TestCase):
    def _guide(self):
        tmp = tempfile.mkdtemp()
        return PriceGuide({
            "_config_dir": tmp,
            "database": {"file": os.path.join(tmp, "history.db")},
            "api_keys": {"pricecharting": {"token": "test"}},
        })

    def test_sports_cards_only_use_sportscardspro_host(self):
        guide = self._guide()
        called = []

        def quote_from(_ident, host, category=None):
            called.append((host, category))
            return GuideQuote(note="miss")

        guide._quote_from = quote_from
        guide.quote(
            identity_of("1986 Fleer Michael Jordan #57 PSA 8"),
            category="Sports Cards")
        self.assertTrue(called)
        self.assertTrue(all("sportscardspro" in host for host, _ in called))

    def test_a_psp_game_cannot_price_a_tiger_woods_card(self):
        guide = self._guide()
        ident = identity_of(
            "2001 Upper Deck SP Authentic Tiger Woods #1 PSA 8")
        quote = guide._quote_from_rows(ident, [{
            "id": "game-1",
            "product-name": "Tiger Woods PGA Tour 10",
            "console-name": "PSP",
            "genre": "Sports",
            "loose-price": 1200,
            "_guide-host": "pricecharting",
        }], source="local CSV", category="Sports Cards")
        self.assertFalse(quote.landed)


class TestDiscoveryPromotion(unittest.TestCase):
    def test_exact_evidence_promotes_a_specific_discovery_card(self):
        tmp = tempfile.mkdtemp()
        engine = ValuationEngine({
            "_config_dir": tmp,
            "database": {"file": os.path.join(tmp, "history.db")},
            "filters": {"min_value": 500},
            "algorithm": {
                "min_specific_comps": 3,
                "discovery_promotion_specificity_floor": 0.8,
            },
        })
        engine.guide.quote = lambda _ident, category=None: GuideQuote(
            value=1500, match="exact", score=0.95,
            product_name="Michael Jordan #17")
        listing = Listing(
            site="ebay",
            title="1988-89 Fleer Michael Jordan #17 PSA 9",
            url="https://example.test/jordan",
            current_price=800,
            bid_count=5,
            end_time=datetime.now(timezone.utc) + timedelta(hours=4),
            query="Michael Jordan Fleer PSA",
            discovery=True,
            category="Sports Cards",
        )
        comps = [
            SoldComp("1988-89 Fleer Michael Jordan #17 PSA 9", value)
            for value in (1400, 1500, 1600)
        ]
        opp = engine.evaluate(
            listing, comps, specific_comps=comps)
        self.assertTrue(opp.listing.promoted_from_discovery)
        self.assertFalse(opp.listing.discovery)
        self.assertTrue(any(
            "PROMOTED FROM DISCOVERY" in note
            for note in opp.valuation.notes))


class TestValueFloorsAndSetNeeds(unittest.TestCase):
    @staticmethod
    def _opportunity(fair: float, *, set_need=False, override=None):
        listing = Listing(
            site="ebay", title="1999 1st Edition Pokemon Trader PSA 9",
            url="https://example.test/trader", current_price=50,
            listing_type="fixed",
            query="1999 1st Edition Pokemon Trader PSA 9",
            category="Pokemon Cards", set_need=set_need,
            value_floor_override=override,
        )
        return Opportunity(listing, Valuation(
            fair_value=fair, expected_value=25, roi=0.25,
            edge_now=30, confidence=0.8))

    def test_general_floor_is_500_but_set_need_has_no_floor(self):
        config = {
            "filters": {
                "browse_min_value": 500,
                "decision_min_value": 500,
                "max_roi": 2,
            },
            "output": {"min_expected_value": 0, "max_rows": 1000},
        }
        ordinary = self._opportunity(100)
        need = self._opportunity(100, set_need=True, override=0)
        kept, research, _diag = scanner.classify_report_rows(
            [ordinary, need], config)
        self.assertEqual(kept, [need])
        self.assertTrue(any(
            row["stage"] == "Fair-value floor" for row in research))

    def test_browse_row_below_decision_floor_is_quarantined(self):
        config = {
            "filters": {
                "browse_min_value": 100,
                "decision_min_value": 500,
                "max_roi": 2,
            },
            "output": {"min_expected_value": 0, "max_rows": 1000},
        }
        row = self._opportunity(300)
        kept, _research, _diag = scanner.classify_report_rows([row], config)
        self.assertEqual(kept, [row])
        self.assertEqual(
            tradeability_rejection(row), "below decision value floor")


class TestTelegramNegativeBinInvariant(unittest.TestCase):
    def test_negative_ev_grail_bin_never_reaches_telegram(self):
        listing = Listing(
            site="ebay", title="Wanted card", url="https://example.test/bin",
            listing_id="negative-bin", current_price=1200,
            listing_type="fixed", query="Wanted card", priority=True,
            grail="Wanted card", grail_score=100,
            created_at=datetime.now(timezone.utc),
        )
        row = Opportunity(listing, Valuation(
            fair_value=1000, expected_value=-300, edge_now=-250,
            roi=-0.25, capture=1, confidence=0.8))
        config = {
            "alerts": {
                "enabled": True, "priority_only": False,
                "min_edge_now": 1, "min_roi": 0, "max_roi": 2,
                "min_capture": 0, "min_confidence": 0,
                "telegram": {"bot_token": "test", "chat_id": "test"},
                "grails": {"enabled": True, "fresh_hours": 24},
            }
        }
        with mock.patch("alerts._send_telegram") as send:
            self.assertEqual(alerts.send_alerts([row], config, ":memory:"), 0)
            send.assert_not_called()


class TestWeeklyCsvFreshness(unittest.TestCase):
    def test_default_refresh_target_is_one_week(self):
        self.assertEqual(fetch_guide_csv.FRESH_HOURS, 168)


if __name__ == "__main__":
    unittest.main(verbosity=2)
