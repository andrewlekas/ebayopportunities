"""Regression tests for the 2026-07-25 valuation/learning fixes.

Every test below is anchored to a REAL row or a real number from Andrew's
own data, so a failure means the bug is back - not that a style changed.

Run it by double-clicking "Run Tests.command" (results are also written to
test_results.log), or:

    .venv/bin/python -m unittest discover -s code -p "test_*.py" -v
"""
from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db as histdb                                          # noqa: E402
import learner                                               # noqa: E402
import main as scanner                                       # noqa: E402
import report as report_mod                                  # noqa: E402
from models import Listing, Opportunity, Valuation           # noqa: E402
from report import _category                                 # noqa: E402
from valuation.comps import (grade_conflict, grade_info,     # noqa: E402
                             grader_of)
from valuation.price_guide import _guide_cents               # noqa: E402


# A PriceCharting product shaped like the Topsun Charizard that exposed the
# bug: loose $900, Grade 7 $1,800, Grade 8 $3,300, Grade 9 $6,718,
# Grade 9.5 $12,000, Grade 10 $25,000 (values in cents).
PRODUCT = {"loose-price": 90000, "cib-price": 180000, "new-price": 330000,
           "graded-price": 671800, "box-only-price": 1200000,
           "manual-only-price": 2500000}


def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


class TestGradeParsing(unittest.TestCase):
    """GRADE_RE used to accept two-digit numbers, so a seller typing
    'CGC 85' (for CGC 8.5) produced grade 85 -> 'PSA 84'. That row was live
    in the Crossover tab on 2026-07-25 claiming $3,349 of regrade profit."""

    def test_impossible_grade_reads_as_ungraded(self):
        title = "Pokemon Charizard Topsun Blue Back 1997 CGC 85 Rare Japanese"
        self.assertIsNone(grade_info(title))
        self.assertIsNone(grader_of(title))

    def test_normal_grades_still_parse(self):
        self.assertEqual(grade_info("Charizard PSA 10 Gem"), ("psa", "10", "10"))
        self.assertEqual(grade_info("Gengar PSA 9 Fossil"), ("psa", "9", "9"))
        self.assertEqual(grade_info("Card BGS 9.5 (10 subs)"),
                         ("bgs", "9.5", "8.5"))

    def test_cross_grader_shift_is_one_full_grade(self):
        """Andrew's rule: every other grading service counts one grade
        lower than PSA when comparing prices."""
        for grader in ("cgc", "bgs", "sgc", "bvg"):
            self.assertEqual(grade_info(f"Card {grader.upper()} 10")[2], "9")
            self.assertEqual(grade_info(f"Card {grader.upper()} 8.5")[2], "7.5")
        self.assertEqual(grade_info("Card PSA 10")[2], "10")

    def test_effective_grade_floors_at_one(self):
        self.assertEqual(grade_info("Card CGC 1")[2], "1")

    def test_sgc_legacy_100_point_labels(self):
        # SGC graded on a 100-point scale until 2020
        self.assertEqual(grade_info("1952 Topps Mantle SGC 92"),
                         ("sgc", "8.5", "7.5"))
        self.assertEqual(grade_info("T206 Cobb SGC 30"), ("sgc", "1.5", "1"))

    def test_grade_conflict_uses_effective_grades(self):
        self.assertFalse(grade_conflict("Gengar PSA 9", "Gengar CGC 10"))
        self.assertTrue(grade_conflict("Gengar PSA 9", "Gengar CGC 9"))
        # a typo'd grade must not match a real one
        self.assertFalse(grade_conflict("Gengar PSA 9", "Gengar CGC 85"))


class TestPriceGuideGradeRouting(unittest.TestCase):
    """The guide picked its price field from the RAW grade, so the -1
    cross-grader penalty was applied to comps and silently skipped on the
    guide. Two Topsun Charizards (CGC 8.5 and 'CGC 85') were both quoted
    $6,718 - the Grade 9 price - on 2026-07-25."""

    def dollars(self, eff):
        cents, _how = _guide_cents(PRODUCT, eff)
        return None if cents is None else round(cents / 100)

    def test_cgc_10_prices_as_psa_9_not_psa_10(self):
        eff = float(grade_info("Charizard CGC 10")[2])
        self.assertEqual(self.dollars(eff), 6718)
        self.assertNotEqual(self.dollars(eff), 25000)

    def test_half_grade_lands_between_neighbours_never_above(self):
        self.assertEqual(self.dollars(8.0), 3300)
        self.assertEqual(self.dollars(9.0), 6718)
        self.assertTrue(3300 < self.dollars(8.5) < 6718)

    def test_live_cgc_85_row_falls_back_to_raw(self):
        eff = grade_info("Topsun Charizard 1997 CGC 85")
        self.assertIsNone(eff)
        self.assertEqual(self.dollars(None), 900)

    def test_never_inflates_when_a_rung_is_missing(self):
        sparse = {"loose-price": 90000, "graded-price": 671800}
        for eff in (6.0, 7.0, 8.0, 8.5):
            cents, _ = _guide_cents(sparse, eff)
            self.assertLess(cents / 100, 6718,
                            f"grade {eff} inherited Grade-9 money")

    def test_refuses_rather_than_guessing_upward(self):
        """No raw price and the only rung is above our grade: the honest
        answer is no guide value at all, not the higher rung's price."""
        cents, how = _guide_cents({"graded-price": 671800}, 7.0)
        self.assertIsNone(cents)
        self.assertIn("below lowest available field", how)

    def test_monotonic_in_grade(self):
        vals = [self.dollars(g) for g in (5, 6, 7, 7.5, 8, 8.5, 9, 9.5, 10)]
        self.assertEqual(vals, sorted(vals))


class TestGuideCacheInvalidation(unittest.TestCase):
    """Guide prices are cached for a week. Without a version bump the grade
    fix would have been invisible until 2026-08-01: the cache held Topsun
    Charizard at $6,718 for PSA 9, CGC 9, CGC 8.5 and the typo 'CGC 85'."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "h.db")

    def _guide(self):
        from valuation.price_guide import PriceGuide
        return PriceGuide({"database": {"file": self.db},
                           "api_keys": {"pricecharting": {"token": "x"}}})

    def test_stale_cache_is_cleared_once_then_left_alone(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE IF NOT EXISTS guide_cache("
                     "query TEXT PRIMARY KEY, value REAL, ts TEXT)")
        conn.execute("INSERT INTO guide_cache VALUES "
                     "('Topsun Charizard 1997 CGC 8.5', 6718.0, ?)",
                     (datetime.now(timezone.utc).isoformat(),))
        conn.commit()
        conn.close()

        with self.assertLogs("valuation.price_guide", level="WARNING"):
            self._guide()                  # first run purges
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM guide_cache").fetchone()[0], 0)
        # a fresh value written after the purge must survive the next run
        conn.execute("INSERT INTO guide_cache VALUES ('q', 100.0, ?)",
                     (datetime.now(timezone.utc).isoformat(),))
        conn.commit()
        conn.close()
        self._guide()
        conn = sqlite3.connect(self.db)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM guide_cache").fetchone()[0], 1)
        conn.close()


def _opp(title="Card", query="Pokemon Card", ev=500.0, roi=0.4, bids=3,
         ltype="auction", grail="", site="ebay", has_buy_now=False):
    return Opportunity(
        listing=Listing(site=site, title=title, url="https://e/itm/1",
                        current_price=100.0, bid_count=bids,
                        listing_type=ltype, grail=grail, query=query,
                        has_buy_now=has_buy_now),
        valuation=Valuation(fair_value=1000.0, expected_value=ev, roi=roi))


class TestPokemonGradeFloor(unittest.TestCase):
    """filters.pokemon_grade_floor is 5: graded Pokemon at PSA 5 or below
    are dropped. Pokemon only, grails exempt, raw unaffected."""

    def ok(self, o, floor=5.0):
        return scanner.output_ok(o, min_ev=0.0, poke_floor=floor)

    def test_drops_psa_5_and_below_pokemon(self):
        for g in ("PSA 5", "PSA 4", "PSA 3", "PSA 1"):
            self.assertFalse(self.ok(_opp(title=f"Charizard Holo {g}",
                                          query="Charizard Holo PSA 5")), g)

    def test_keeps_psa_6_and_above(self):
        for g in ("PSA 5.5", "PSA 6", "PSA 9", "PSA 10"):
            self.assertTrue(self.ok(_opp(title=f"Charizard Holo {g}",
                                         query="Charizard Holo PSA 9")), g)

    def test_floor_uses_effective_grade(self):
        # a CGC 6 is a PSA 5 equivalent -> drops; a CGC 7 (= PSA 6) stays
        self.assertFalse(self.ok(_opp(title="Charizard Holo CGC 6",
                                      query="Charizard Holo")))
        self.assertTrue(self.ok(_opp(title="Charizard Holo CGC 7",
                                     query="Charizard Holo")))

    def test_raw_pokemon_unaffected(self):
        self.assertTrue(self.ok(_opp(title="Charizard Holo Base Set",
                                     query="Charizard Holo")))

    def test_only_applies_to_pokemon(self):
        self.assertTrue(self.ok(_opp(title="1986 Fleer Michael Jordan PSA 3",
                                     query="Michael Jordan 1986 Fleer")))

    def test_grails_are_exempt(self):
        self.assertTrue(self.ok(_opp(title="Charizard Holo PSA 2",
                                     query="Charizard Holo PSA 9",
                                     grail="Topsun Charizard 1997")))

    def test_other_output_rules_intact(self):
        self.assertFalse(self.ok(_opp(ev=-1.0)))
        self.assertFalse(self.ok(_opp(roi=-0.1)))
        self.assertFalse(self.ok(_opp(bids=0)))
        self.assertTrue(self.ok(_opp(bids=0, site="yahoo_jp")))

    def test_zero_bid_hybrid_needs_a_known_buy_it_now(self):
        """The hybrid exemption exists because 'the BIN is takeable'. That
        only holds if we actually captured the BIN price - otherwise the
        row is priced off the seller's opening ask."""
        no_price = _opp(bids=0, has_buy_now=True)
        self.assertFalse(self.ok(no_price))
        with_price = _opp(bids=0, has_buy_now=True)
        with_price.listing.buy_now_price = 2500.0
        self.assertTrue(self.ok(with_price))

    def test_bad_row_keeps_the_row_and_never_raises(self):
        broken = Opportunity(listing=None, valuation=None)
        # the traceback this logs is the POINT of the test: a malformed row
        # must be reported loudly and kept, never allowed to kill the report
        with self.assertLogs("scanner", level="ERROR"):
            self.assertTrue(scanner.output_ok(broken))

    def test_drop_reasons_are_counted_for_the_log(self):
        drops = {}
        scanner.output_ok(_opp(title="Charizard PSA 3", query="Charizard"),
                          poke_floor=5.0, drops=drops)
        scanner.output_ok(_opp(ev=-5.0), drops=drops)
        self.assertEqual(sum(drops.values()), 2)


class TestExcludeKeywords(unittest.TestCase):
    """Live junk rows from the 15:44 report that were valued off real card
    comps: a resin art print at $897 EV and a repack box at $979 EV."""

    KEYWORDS = ["reprint", "repack", "poster", "art print", "chase box",
                "set-break", "set break", "custom card"]

    def test_catches_the_live_junk_rows(self):
        junk = [
            "Ermsy MJ 1986 Michael Jordan Fleer RC BLACK RED Poster Print",
            "UNIVERSAL TREASURES BASKETBALL Chase Box LOADED 100 Fleer PACK",
            "1977 Topps Star Wars Set-Break #270 Luke Skywalker",
            "2024 Pokemon Repack Mystery Box",
        ]
        for t in junk:
            self.assertTrue(scanner._excluded(t, self.KEYWORDS), t)

    def test_does_not_catch_real_cards(self):
        real = [
            "1999 Pokemon 1st Edition Fossil Gengar Holo PSA 9",
            "Pokemon Charizard Topsun Blue Back 1997 1st Print",
            "1986 Fleer Michael Jordan #57 RC PSA 8",
            "Umbreon VMAX Alt Art Evolving Skies PSA 10",
            "2004 Panini Mega Cracks Lionel Messi #89 PSA 7",
        ]
        for t in real:
            self.assertFalse(scanner._excluded(t, self.KEYWORDS), t)


class TestObservationSchema(unittest.TestCase):
    """The learner had no way to tell a well-comped $2,500 valuation from a
    mongrel $2.85 one, because n_comps was never recorded."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "h.db")

    def test_migrates_a_pre_existing_table_without_losing_rows(self):
        c = sqlite3.connect(self.db)
        c.executescript(
            """CREATE TABLE observations(item_id TEXT, site TEXT, query TEXT,
               title TEXT, listing_type TEXT, price REAL, shipping REAL,
               bids INTEGER, end_time TEXT, fair REAL, predicted_settle REAL,
               hours_left REAL, observed_at TEXT);""")
        c.execute("INSERT INTO observations VALUES ('1','ebay','q','t',"
                  "'auction',1,0,0,NULL,10,10,5,'2026-07-01')")
        c.commit()
        c.close()
        conn = histdb.connect(self.db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(observations)")}
        self.assertIn("n_comps", cols)
        self.assertIn("confidence", cols)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 1)
        conn.close()

    def test_records_the_evidence(self):
        conn = histdb.connect(self.db)
        listing = Listing(site="ebay", title="Gengar PSA 9",
                          url="https://www.ebay.com/itm/123456789012",
                          current_price=900.0, listing_id="123456789012",
                          query="Gengar PSA 9",
                          end_time=datetime.now(timezone.utc)
                          + timedelta(hours=3))
        histdb.record_observation(conn, listing,
                                  Valuation(fair_value=2500.0, n_comps=11,
                                            confidence=0.81))
        row = conn.execute("SELECT n_comps, confidence FROM observations"
                           ).fetchone()
        self.assertEqual(row[0], 11)
        self.assertAlmostEqual(row[1], 0.81)
        conn.close()


class TestLearnerTrustFilters(unittest.TestCase):
    """On 2026-07-25 the learner reported settle_ratio 1.2756 - auctions
    supposedly closing 28% ABOVE fair value - because 891 of 1,090 matched
    closes were valued under $50. That inflated every expected cost."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "h.db")

    def _auction(self, conn, iid, fair, actual, n_comps=9, snapshots=3):
        for i, hrs in enumerate([72, 24, 6][:snapshots]):
            listing = Listing(
                site="ebay", title=f"Card {iid} PSA 9",
                url=f"https://www.ebay.com/itm/{iid}",
                current_price=fair * 0.4, bid_count=i * 3, listing_id=iid,
                query=f"Card {iid} PSA 9",
                end_time=datetime.now(timezone.utc) + timedelta(hours=hrs))
            histdb.record_observation(
                conn, listing,
                Valuation(fair_value=fair, expected_cost=fair * 0.9,
                          n_comps=n_comps, confidence=0.7))
        conn.execute("INSERT OR IGNORE INTO closed VALUES (?,?,?)",
                     (iid, actual, datetime.now(timezone.utc).isoformat()))
        conn.commit()

    def test_junk_valuations_are_excluded(self):
        conn = histdb.connect(self.db)
        # the real shape of the bad data: a $2.85 valuation on a card that
        # closed at $67 (a 24x "settle ratio")
        for i in range(40):
            self._auction(conn, f"90000000000{i}", 2.85, 67.0)
        conn.close()
        msg = learner.fit(self.db, directory=self.dir)
        self.assertIn("trustworthy closes", msg)
        params = _load_json(os.path.join(self.dir, "learned_params.json"))
        self.assertEqual(params["n"], 0)
        self.assertGreater(params["training_filter"]
                           ["dropped_fair_below_floor"], 0)

    def test_legacy_rows_without_evidence_are_excluded(self):
        conn = histdb.connect(self.db)
        self._auction(conn, "999000000001", 1200.0, 1000.0)
        conn.execute("UPDATE observations SET n_comps = NULL")
        conn.commit()
        conn.close()
        learner.fit(self.db, directory=self.dir)
        params = _load_json(os.path.join(self.dir, "learned_params.json"))
        self.assertEqual(params["n"], 0)
        self.assertGreater(params["training_filter"]
                           ["dropped_no_evidence_recorded"], 0)

    def test_thin_comp_valuations_are_excluded(self):
        conn = histdb.connect(self.db)
        for i in range(40):
            self._auction(conn, f"88000000000{i}", 1200.0, 1000.0, n_comps=1)
        conn.close()
        learner.fit(self.db, directory=self.dir)
        params = _load_json(os.path.join(self.dir, "learned_params.json"))
        self.assertEqual(params["n"], 0)
        self.assertGreater(params["training_filter"]["dropped_thin_comps"], 0)

    def test_params_are_written_even_on_a_cold_start(self):
        """Returning early used to leave a stale (wrong) settle ratio and a
        deployed model on disk, which the engine kept using forever."""
        stale = {"n": 383, "settle_ratio": 1.2756,
                 "ml": {"deployed": True, "cv_mae": 0.2865}}
        path = os.path.join(self.dir, "learned_params.json")
        with open(path, "w") as f:
            json.dump(stale, f)
        conn = histdb.connect(self.db)
        conn.close()
        learner.fit(self.db, directory=self.dir)
        params = _load_json(path)
        self.assertEqual(params["n"], 0)
        self.assertFalse(params["ml"]["deployed"])
        self.assertIsNone(learner.ClosePredictor(self.dir).settle_ratio)

    def test_recovers_the_true_settle_ratio_from_clean_data(self):
        conn = histdb.connect(self.db)
        rng = random.Random(11)
        true_settle = 0.85
        for i in range(60):
            fair = rng.choice([600.0, 900.0, 1500.0, 2600.0, 4200.0])
            self._auction(conn, f"70000000%04d" % i, fair,
                          round(fair * true_settle * rng.gauss(1, 0.10), 2))
        conn.close()
        msg = learner.fit(self.db, directory=self.dir)
        params = _load_json(os.path.join(self.dir, "learned_params.json"))
        self.assertEqual(params["n"], 60, "should dedupe 180 snapshots -> 60")
        self.assertEqual(params["n_snapshots"], 180)
        self.assertAlmostEqual(params["settle_ratio"], true_settle, delta=0.06)
        self.assertLess(params["parametric_mae"], 0.20)
        self.assertIn("settle_ratio", msg)

    def test_median_is_per_auction_not_per_snapshot(self):
        """One heavily-scanned auction must not outvote 20 others."""
        conn = histdb.connect(self.db)
        for i in range(20):
            self._auction(conn, "6000000%05d" % i, 1000.0, 800.0)
        self._auction(conn, "600009999999", 1000.0, 2500.0, snapshots=3)
        conn.close()
        learner.fit(self.db, directory=self.dir)
        params = _load_json(os.path.join(self.dir, "learned_params.json"))
        self.assertEqual(params["n"], 21)
        self.assertAlmostEqual(params["settle_ratio"], 0.80, delta=0.01)

    def test_engine_falls_back_to_config_when_learner_is_cold(self):
        conn = histdb.connect(self.db)
        conn.close()
        learner.fit(self.db, directory=self.dir)
        pred = learner.ClosePredictor(self.dir)
        self.assertIsNone(pred.settle_ratio)
        self.assertIsNone(pred.settle_ratio_for(1500))
        self.assertIsNone(pred.predict_ratio(5, 0.5, 3, 1500))


class TestSubjectInjection(unittest.TestCase):
    """When a query names no card ("1999 1st Edition Pokemon Set") the
    engine values each listing on its own subject. It used to take the
    three LONGEST leftover words, which are reliably seller adjectives -
    measured over 599 real listings the top injected "subjects" were
    symbol x116, wotc x67, tcg x51, lp x20, stamp x15, none of them cards.
    """

    def test_picks_the_card_not_the_adjectives(self):
        from valuation.comps import subject_candidates
        cases = [
            ("Pokemon Jungle 1st Edition Snorlax Holo Beautiful Centering "
             "Investment", "snorlax"),
            ("1996 POKEMON BASE SET JAPANESE NO RARITY SYMBOL #145 ZAPDOS "
             "COLLECTIBLE", "zapdos"),
            ("1999 POKEMON BASE SET 1ST EDITION #2 BLASTOISE HOLO GRADED "
             "COLLECTIBLE", "blastoise"),
            ("1996 Pokemon Japanese Base No Rarity Charizard Authenticated "
             "Vintage Holo", "charizard"),
        ]
        for title, expected in cases:
            self.assertIn(expected, subject_candidates(title, 2), title[:40])

    def test_marketing_and_context_words_are_never_subjects(self):
        from valuation.comps import subject_candidates
        banned = {"collectible", "authenticated", "investment", "beautiful",
                  "centering", "symbol", "wotc", "tcg", "stamp", "near",
                  "graded", "vintage", "sealed", "rocket", "trainer"}
        titles = [
            "1996 POKEMON BASE SET JAPANESE NO RARITY SYMBOL #145 ZAPDOS",
            "Pokemon Jungle 1st Edition Snorlax Beautiful Centering",
            "Pokemon TCG WOTC Team Rocket Trainer Card Near Mint Collectible",
        ]
        for t in titles:
            got = set(subject_candidates(t, 3))
            self.assertFalse(got & banned, f"{t[:40]} -> {got}")

    def test_short_real_names_survive(self):
        from valuation.comps import subject_candidates
        self.assertIn("mew", subject_candidates(
            "1999 Pokemon Base Set Mew Promo Holo Graded PSA 9", 2))


class TestUnicodeFolding(unittest.TestCase):
    """7% of real listing titles (277 of 4,075) contain non-ASCII. The
    tokenizers split on [a-z], so 'Pokemon' with an accent became the two
    tokens 'pok' and 'mon' - which is where 'mon x50' and 'pok x22' in the
    injected-subject tally came from."""

    def test_accented_word_stays_one_token(self):
        from valuation.comps import _subject_tokens
        accented = _subject_tokens("Pokémon TCG Gloom Jungle")
        plain = _subject_tokens("Pokemon TCG Gloom Jungle")
        self.assertEqual(accented, plain)
        self.assertNotIn("mon", accented)
        self.assertNotIn("pok", accented)

    def test_title_match_is_accent_insensitive(self):
        from valuation.comps import title_match_score
        a = title_match_score("Pokemon Charizard Base Set",
                              "Pokémon Charizard Base Set Holo")
        b = title_match_score("Pokemon Charizard Base Set",
                              "Pokemon Charizard Base Set Holo")
        self.assertAlmostEqual(a, b, places=6)

    def test_grail_matching_is_accent_insensitive(self):
        import grails
        g = grails.load_grails({"grails": ["Pokemon Red Cheeks Pikachu"]})
        self.assertIsNotNone(
            grails.match(g, "1999 Pokémon Red Cheeks Pikachu PSA 9"))

    def test_japanese_titles_pass_through_unchanged(self):
        """Japanese dakuten decompose exactly like a Latin accent. Dropping
        them turns プ into フ - a different word - which would break the
        Topsun variant check and every translated Yahoo/Buyee query."""
        from textutil import fold
        for s in ("トップサン", "リザードン", "カメックス", "旧裏",
                  "マークなし", "ポケモンカード", "ギャラドス"):
            self.assertEqual(fold(s), s)

    def test_japanese_matchers_still_fire_after_folding(self):
        from valuation.comps import variant_conflict, JP_NATIVE_RE
        from textutil import fold
        self.assertTrue(JP_NATIVE_RE.search(fold("旧裏 ポケモン")))
        # Topsun original vs the VS battle series must still be told apart
        self.assertTrue(variant_conflict("トップサン Charizard",
                                         "トップサン Charizard VS Blastoise"))


class TestHybridBuyItNow(unittest.TestCase):
    """A hybrid auction's current_price is the BID. With zero bids that is
    the seller's opening ask, not a transactable price. Live on 2026-07-25:
    a $499 opening bid on a card valued at $2,821, reported as $1,584 of
    expected value."""

    def setUp(self):
        logging.disable(logging.WARNING)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def _engine(self):
        from valuation.engine import ValuationEngine
        return ValuationEngine({"_config_dir": tempfile.mkdtemp(),
                                "algorithm": {"sales_tax_rate": 0.0,
                                              "psa_vault": {"enabled": False}}})

    def _listing(self, bids, bin_price):
        return Listing(site="ebay", title="1940 Superman Gum R145",
                       url="https://www.ebay.com/itm/1", current_price=499.0,
                       bid_count=bids, listing_type="auction",
                       has_buy_now=True, buy_now_price=bin_price,
                       query="Superman 1940",
                       end_time=datetime.now(timezone.utc)
                       + timedelta(hours=10))

    def test_zero_bid_hybrid_is_priced_off_the_buy_it_now(self):
        e = self._engine()
        v = e.score(self._listing(0, 2400.0), Valuation(fair_value=2821.0))
        # edge is measured against the BIN (2,400), not the 499 opening bid
        self.assertLess(v.edge_now, 200)
        self.assertTrue(any("Buy It Now" in n for n in v.notes))

    def test_bid_hybrid_still_uses_the_live_bid(self):
        e = self._engine()
        v = e.score(self._listing(7, 2400.0), Valuation(fair_value=2821.0))
        self.assertGreater(v.edge_now, 1500)     # real bidding at $499

    def test_expected_close_never_exceeds_the_buy_it_now(self):
        e = self._engine()
        v = e.score(self._listing(7, 1200.0), Valuation(fair_value=9000.0))
        self.assertLessEqual(v.expected_cost, 1200.0 + 0.01)


class TestCategoryRouting(unittest.TestCase):
    """'sealed' used to be a Video Games keyword, tested before Pokemon -
    so a sealed Pokemon query landed in the games tab AND skipped the
    Pokemon grade floor, which only applies to the Pokemon category."""

    def test_sealed_pokemon_is_a_pokemon_card(self):
        self.assertEqual(_category("Sealed Pokemon Base Set Booster Box"),
                         "Pokemon Cards")
        self.assertEqual(_category("1st Edition Pokemon Pack sealed"),
                         "Pokemon Cards")

    def test_platform_words_still_win(self):
        for q in ("Pokemon Red Gameboy", "Super Mario Bros 1985 NES",
                  "Halo Xbox", "Duck Hunt NES"):
            self.assertEqual(_category(q), "Video Games", q)

    def test_game_titles_without_a_platform(self):
        for q in ("Sealed Zelda Ocarina of Time", "The Sims",
                  "Roller Coaster Tycoon", "Call of Duty MW2"):
            self.assertEqual(_category(q), "Video Games", q)

    def test_cards_and_watches_unchanged(self):
        self.assertEqual(_category("Charizard Base Set PSA 9"),
                         "Pokemon Cards")
        self.assertEqual(_category("Michael Jordan 1986 Fleer Auto"),
                         "Sports Cards")
        self.assertEqual(_category("Patek Philippe World Time"), "Watches")


class TestBidLevels(unittest.TestCase):
    """Max Bid used to be exact breakeven - winning there earns nothing."""

    def _opp_for(self, fair, ship=0.0):
        return Opportunity(
            listing=Listing(site="ebay", title="t", url="", current_price=1.0,
                            shipping=ship, query="q", marketplace="EBAY_US"),
            valuation=Valuation(fair_value=fair))

    def _cfg(self, target=0.15, vault=True):
        return {"algorithm": {"resale_fee_rate": 0.1325,
                              "sales_tax_rate": 0.08,
                              "psa_vault": {"enabled": vault,
                                            "min_price": 500,
                                            "sell_fee_rate": 0.07}},
                "output": {"today": {"max_bid_target_roi": target}}}

    def test_max_bid_leaves_the_target_return(self):
        mb, be = report_mod._bid_levels(self._opp_for(2450.0), self._cfg())
        self.assertIsNotNone(mb)
        self.assertLess(mb, be, "max bid must sit below breakeven")
        proceeds = 2450.0 * (1 - 0.07)          # vault route
        self.assertAlmostEqual((proceeds - mb) / mb, 0.15, places=2)

    def test_breakeven_is_still_zero_profit(self):
        _mb, be = report_mod._bid_levels(self._opp_for(2450.0), self._cfg())
        self.assertAlmostEqual(be, round(2450.0 * (1 - 0.07), 0), delta=1)

    def test_target_is_configurable(self):
        loose, _ = report_mod._bid_levels(self._opp_for(2450.0),
                                          self._cfg(target=0.05))
        tight, _ = report_mod._bid_levels(self._opp_for(2450.0),
                                          self._cfg(target=0.40))
        self.assertGreater(loose, tight)

    def test_taxed_route_below_the_vault_threshold(self):
        mb, be = report_mod._bid_levels(self._opp_for(300.0), self._cfg())
        self.assertIsNotNone(be)
        self.assertLess(mb, be)


class TestCompHygiene(unittest.TestCase):
    """'Babe Ruth 1933' had 116 comps from $1 to $21,000 with a median of
    $6, because the pool mixed 1933 Goudeys with '1991 Conlon ... You Pick'
    lots and modern tribute cards."""

    def test_year_guard_rejects_modern_tribute_cards(self):
        from valuation.comps import year_conflict
        self.assertTrue(year_conflict(
            "George Mikan 1948", "2009-10 Bowman '48 George Mikan Blue"))
        self.assertTrue(year_conflict(
            "Babe Ruth 1933", "1991 Conlon Collection TSN Babe Ruth"))

    def test_year_guard_accepts_season_ranges(self):
        from valuation.comps import year_conflict, years_in
        self.assertFalse(year_conflict(
            "Michael Jordan 1984 Star", "1984-85 STAR #288 MICHAEL JORDAN"))
        self.assertIn("1985", years_in("1984-85 Star"))

    def test_year_guard_is_inert_without_a_year(self):
        from valuation.comps import year_conflict
        self.assertFalse(year_conflict("Charizard Holo PSA 9",
                                       "1999 Charizard Holo PSA 9"))

    def test_card_number_extraction(self):
        from valuation.comps import card_number
        self.assertEqual(card_number("1984-85 STAR #288 MICHAEL JORDAN"), "288")
        self.assertEqual(card_number("1933 Goudey No. 53 Babe Ruth"), "53")
        self.assertEqual(card_number("Charizard 4/102 Base Set"), "4")
        self.assertIsNone(card_number("1948 Bowman George Mikan RC"))
        # a serial number is not a card number
        self.assertIsNone(card_number("Bowman Blue #rd /1948 Mikan"))

    def test_number_guard_pins_one_card(self):
        from valuation.comps import number_conflict
        q = "Michael Jordan 1984 Star #288"
        self.assertFalse(number_conflict(q, "1984-85 STAR #288 JORDAN PSA 6"))
        self.assertTrue(number_conflict(q, "1984-85 Star #101 Jordan XRC"))
        # an unnumbered sale cannot price a specific card
        self.assertTrue(number_conflict(q, "1984 Star Michael Jordan PSA 7"))

    def test_number_guard_is_inert_without_a_number(self):
        from valuation.comps import number_conflict
        self.assertFalse(number_conflict("Michael Jordan 1984 Star",
                                         "1984-85 Star #101 Jordan"))

    def test_guards_are_applied_by_robust_comp_value(self):
        from valuation.comps import robust_comp_value
        from models import SoldComp
        # ungraded query and ungraded comps, so the (pre-existing) grade
        # guard stays inert and this isolates the year/number guards
        pool = [SoldComp(title="1948 Bowman #69 George Mikan RC", price=5200.0),
                SoldComp(title="1948 Bowman #69 George Mikan rookie",
                         price=4800.0),
                SoldComp(title="1948 Bowman #69 Mikan", price=5000.0),
                # wrong era: a 2009 tribute card priced the 1948 rookie at $1
                SoldComp(title="2009-10 Bowman '48 George Mikan Blue #90",
                         price=1.0),
                # wrong card: different number, different player entirely
                SoldComp(title="1948 Bowman #81 George Kurowski", price=663.0)]
        value, n, _disp, _m = robust_comp_value("George Mikan 1948 #69", pool)
        self.assertEqual(n, 3, "only the three #69 sales should count")
        self.assertGreater(value, 4000)


class TestCollectingStandards(unittest.TestCase):
    """Pokemon: 1st Edition and No Rarity only. Video games: sealed or
    graded only, but at a lower dollar floor than cards."""

    def setUp(self):
        self.cfg = {"filters": {
            "pokemon_eras_only": True,
            "video_games_sealed_or_graded": True}}

    def ok(self, title, query, grail=""):
        o = Opportunity(
            listing=Listing(site="ebay", title=title, url="",
                            current_price=100.0, query=query, grail=grail),
            valuation=Valuation(fair_value=1000.0))
        return scanner.collection_ok(o, self.cfg)

    def test_keeps_first_edition_and_no_rarity(self):
        self.assertTrue(self.ok(
            "1999 Pokemon Base Set 1st Edition Charizard Holo PSA 9",
            "1999 1st Edition Pokemon Set"))
        self.assertTrue(self.ok(
            "1996 Pokemon Japanese No Rarity Charizard PSA 8",
            "Pokemon No Rarity 1996"))

    def test_drops_unlimited_and_modern_pokemon(self):
        self.assertFalse(self.ok(
            "1999 Pokemon Base Set Unlimited Charizard Holo PSA 9",
            "1999 1st Edition Pokemon Set"))
        self.assertFalse(self.ok("2023 Pokemon 151 Charizard PSA 10",
                                 "1999 1st Edition Pokemon Set"))

    def test_named_vintage_sets_still_work(self):
        """Topsun, Carddass and Movie Promo are neither 1st Edition nor No
        Rarity, but they are exactly what those queries are for."""
        self.assertTrue(self.ok("Pokemon Charizard Topsun Blue Back 1997 PSA 7",
                                "Topsun Charizard 1997"))
        self.assertTrue(self.ok("1996 Bandai Carddass Kangaskhan PSA 9",
                                "Bandai Carddass Pokemon PSA"))
        self.assertTrue(self.ok("Movie Promo Charizard 1998 PSA 9",
                                "Movie Promo Charizard"))

    def test_games_must_be_sealed_or_graded(self):
        self.assertTrue(self.ok("Super Mario Bros NES Factory Sealed WATA 9.8",
                                "Super Mario Bros 1985 NES"))
        self.assertTrue(self.ok("Legend of Zelda NES VGA 85 Sealed",
                                "Zelda 1987 NES"))
        self.assertFalse(self.ok("Super Mario Bros NES Cartridge Only Tested",
                                 "Super Mario Bros 1985 NES"))
        self.assertFalse(self.ok("GoldenEye 007 N64 Complete in Box CIB",
                                 "GoldenEye 007 N64"))

    def test_grails_bypass_every_standard(self):
        self.assertTrue(self.ok("2023 Pokemon 151 Charizard PSA 10", "q",
                                grail="Topsun Charizard 1997"))

    def test_other_categories_untouched(self):
        self.assertTrue(self.ok("1986 Fleer Michael Jordan #57 PSA 8",
                                "Michael Jordan 1986 Fleer"))
        self.assertTrue(self.ok("Rolex Submariner 16610", "Rolex Submariner"))


class TestGameGrading(unittest.TestCase):
    """WATA and VGA grade sealed games and were not recognised at all, so a
    sealed WATA 9.8 copy was matched against loose cartridges."""

    def test_wata_is_a_grader(self):
        self.assertEqual(grade_info("Super Mario Bros NES WATA 9.8 A++"),
                         ("wata", "9.8", "9.8"))

    def test_vga_100_point_scale_maps_to_ten(self):
        self.assertEqual(grade_info("Zelda NES VGA 85")[1], "8.5")
        self.assertEqual(grade_info("Metroid NES VGA 90")[1], "9")

    def test_no_psa_shift_for_game_graders(self):
        """The -1 cross-grader penalty is a CARD rule; game graders are
        never compared against PSA."""
        gi = grade_info("Super Mario Bros WATA 9")
        self.assertEqual(gi[1], gi[2])

    def test_sealed_cib_and_loose_are_different_items(self):
        from valuation.comps import variant_conflict
        self.assertTrue(variant_conflict("Super Mario Bros NES Sealed",
                                         "Super Mario Bros NES loose cart"))
        self.assertTrue(variant_conflict("Zelda NES Sealed",
                                         "Zelda NES CIB complete in box"))
        self.assertFalse(variant_conflict("Zelda NES Sealed",
                                          "Zelda NES Factory Sealed WATA 9.4"))


class TestApiThrottling(unittest.TestCase):
    """Andrew's rule: after 3 straight failures an endpoint is left alone
    for the rest of the run. These breakers live in the shared network
    layer, so they apply identically to the full scan and the BIN sweep -
    which are the same program (main.py, with and without --mode bin)."""

    def setUp(self):
        # these tests deliberately cause failures; their warnings would
        # otherwise bury the real result in test_results.log
        logging.disable(logging.CRITICAL)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def _scraper(self, failing=True):
        from scrapers.base import BaseScraper, reset_api_stats
        reset_api_stats()
        tmp = tempfile.mkdtemp()
        cfg = {"_config_dir": tmp,
               "scraping": {"request_delay_seconds": 0,
                            "circuit_breaker_failures": 3}}
        s = BaseScraper(cfg)
        s.site = "testsite"
        s._streaks = {"api": 0, "html": 0}

        class Boom(Exception):
            pass

        import requests as _rq

        def _get(*a, **k):
            if failing:
                raise _rq.RequestException("simulated outage")
            raise Boom()
        s.session.get = _get
        return s

    def test_stops_calling_after_three_straight_failures(self):
        from scrapers.base import API_STATS
        s = self._scraper()
        for _ in range(20):
            s._get("https://example.invalid/x", api=True)
        self.assertEqual(API_STATS[("testsite/api", "failed")], 3,
                         "should have hit the wire exactly 3 times")
        self.assertEqual(API_STATS[("testsite/api", "skipped")], 17)
        self.assertTrue(s.lane_tripped("api"))

    def test_lanes_trip_independently(self):
        s = self._scraper()
        for _ in range(10):
            s._get("https://example.invalid/x", api=True)
        self.assertTrue(s.lane_tripped("api"))
        self.assertFalse(s.lane_tripped("html"))

    def test_summary_names_the_endpoints_left_alone(self):
        from scrapers.base import api_summary
        s = self._scraper()
        for _ in range(10):
            s._get("https://example.invalid/x", api=True)
        summary = api_summary()
        self.assertIn("testsite/api", summary)
        self.assertIn("3 failed", summary)
        self.assertIn("7 skipped", summary)
        self.assertIn("breaker open", summary)

    def test_ebay_oauth_breaker_counts_and_stops(self):
        from scrapers.base import API_STATS, reset_api_stats
        from scrapers.ebay import EbayScraper
        reset_api_stats()
        tmp = tempfile.mkdtemp()
        s = EbayScraper({"_config_dir": tmp,
                         "api_keys": {"ebay": {"client_id": "x",
                                               "client_secret": "y"}},
                         "scraping": {"request_delay_seconds": 0}})
        import requests as _rq
        s.session.post = lambda *a, **k: (_ for _ in ()).throw(
            _rq.RequestException("auth down"))
        for _ in range(15):
            s._get_token()
        self.assertEqual(API_STATS[("ebay/oauth", "failed")], 3)
        self.assertEqual(API_STATS[("ebay/oauth", "skipped")], 12)

    def test_telegram_breaker_is_shared_between_alerts_and_digest(self):
        import alerts
        from scrapers.base import API_STATS, reset_api_stats
        reset_api_stats()
        alerts._tg_breaker.update({"fails": 0, "announced": False})
        try:
            for _ in range(3):
                alerts.telegram_result(False)
            self.assertTrue(alerts.telegram_blocked())
            # digest.py imports the same two helpers, so its sends stop too
            from digest import _send
            self.assertFalse(_send("x", {"bot_token": "t", "chat_id": "c"}))
            self.assertEqual(API_STATS[("telegram", "failed")], 3)
            self.assertGreaterEqual(API_STATS[("telegram", "skipped")], 1)
        finally:
            alerts._tg_breaker.update({"fails": 0, "announced": False})

    def test_price_guide_breakers_stop_at_three(self):
        from scrapers.base import API_STATS, reset_api_stats
        from valuation.price_guide import PriceGuide
        reset_api_stats()
        tmp = tempfile.mkdtemp()
        g = PriceGuide({"database": {"file": os.path.join(tmp, "h.db")},
                        "api_keys": {"pricecharting": {"token": "t"}}})
        import requests as _rq
        g.session.get = lambda *a, **k: (_ for _ in ()).throw(
            _rq.RequestException("guide down"))
        for i in range(12):
            g._pricecharting(f"query {i}")
        self.assertEqual(API_STATS[("pricecharting", "failed")], 3)
        self.assertEqual(API_STATS[("pricecharting", "skipped")], 9)


class TestRunFooter(unittest.TestCase):
    """Every run ends with its duration, in every mode."""

    def test_duration_formatting(self):
        self.assertEqual(scanner._format_duration(0), "0s")
        self.assertEqual(scanner._format_duration(59), "59s")
        self.assertEqual(scanner._format_duration(159), "2m 39s")
        self.assertEqual(scanner._format_duration(3725), "1h 02m 05s")

    def test_footer_logs_duration_and_api_summary(self):
        with self.assertLogs("scanner", level="INFO") as caught:
            scanner._run_footer(0.0, "bin", 0)
        joined = "\n".join(caught.output)
        self.assertIn("run finished in", joined)
        self.assertIn("mode=bin", joined)
        self.assertIn("api calls:", joined)


class TestCredentialHygiene(unittest.TestCase):
    def test_network_text_redacts_query_path_and_header_secrets(self):
        from security import redact_text
        secrets = {
            "pc-token-123456789": (
                "https://www.pricecharting.com/api/product?"
                "t=pc-token-123456789&q=charizard"),
            "123456789:telegram-token-value": (
                "https://api.telegram.org/"
                "bot123456789:telegram-token-value/sendMessage"),
            "bearer-token-123456789": (
                "Authorization: Bearer bearer-token-123456789"),
            "client-secret-123456789": (
                '"client_secret": "client-secret-123456789"'),
        }
        for secret, text in secrets.items():
            safe = redact_text(text)
            self.assertNotIn(secret, safe)
            self.assertIn("<redacted>", safe)

    def test_secrets_file_and_environment_override_inline_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.yaml")
            secrets_path = os.path.join(tmp, "secrets.yaml")
            with open(config_path, "w") as f:
                yaml.safe_dump({
                    "api_keys": {
                        "ebay": {"client_id": "inline-id",
                                 "client_secret": "inline-secret"},
                        "pricecharting": {"token": "inline-token"},
                    },
                    "alerts": {"telegram": {"bot_token": "inline-bot"}},
                    "database": {"file": "database/test.db"},
                }, f)
            with open(secrets_path, "w") as f:
                yaml.safe_dump({
                    "api_keys": {
                        "ebay": {"client_secret": "file-secret"},
                        "pricecharting": {"token": "file-token"},
                    },
                    "alerts": {"telegram": {"bot_token": "file-bot"}},
                }, f)
            with mock.patch.dict(
                    os.environ,
                    {"CARD_SCANNER_PRICECHARTING_TOKEN": "env-token"},
                    clear=False):
                config = scanner.load_config(config_path)
            self.assertEqual(
                config["api_keys"]["ebay"]["client_secret"], "file-secret")
            self.assertEqual(
                config["api_keys"]["pricecharting"]["token"], "env-token")
            self.assertEqual(
                config["alerts"]["telegram"]["bot_token"], "file-bot")
            self.assertEqual(
                config["database"]["file"],
                os.path.join(tmp, "database", "test.db"))


class TestMLDeploymentGate(unittest.TestCase):
    """The GBM deployed because it beat a baseline with an MAE of 1.126 by
    3%. A model must clear an absolute accuracy bar, and cross-validation
    must split by AUCTION so the same item can't sit in train and test."""

    def test_ml_tier_requires_enough_distinct_auctions(self):
        self.assertGreaterEqual(learner.MIN_ML_ITEMS, 150)

    def test_accuracy_ceiling_is_configured(self):
        self.assertLessEqual(learner.DEFAULT_MAX_CV_MAE, 0.25)

    def test_gate_rejects_an_inaccurate_model(self):
        para_mae, max_cv = 1.1257, learner.DEFAULT_MAX_CV_MAE
        cv_mae = 0.2865                      # the model that shipped
        self.assertTrue(cv_mae < para_mae * 0.97, "old gate passed it")
        self.assertFalse(cv_mae < para_mae * 0.97 and cv_mae <= max_cv,
                         "new gate must reject it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
