"""Blended valuation + expected-value scoring.

Fair value
    Blend of sold-comps value and price-guide value. The comps weight scales
    with sample size and shrinks when comps are widely dispersed, so a thin
    or noisy comp set leans harder on the guide (and vice versa).

Expected acquisition cost (auctions)
    Auctions rarely close at the current bid. We model the expected final
    price as fair_value * settle_ratio, pulled toward the current bid when
    the auction is nearly over (late underpricing is real opportunity;
    early underpricing usually evaporates).

Expected value (EV)
    EV = net_proceeds_if_resold - expected_all_in_cost, where proceeds are
    discounted by marketplace selling fees.

Confidence (0-1)
    Rises with comp count and title-match quality; falls with comp
    dispersion; small bonus when comps and guide agree.

Opportunity score = EV * confidence  ->  final sort key.
"""
from __future__ import annotations

import math

from models import Listing, Valuation, Opportunity, SoldComp
from . import comps as comps_mod
from .comps import (GRADE_RE, GRADER_PREMIUM, _subject_tokens, card_number,
                    comp_velocity, grade_info, grader_of, robust_comp_value,
                    subject_candidates, title_match_score)
from .price_guide import PriceGuide


class ValuationEngine:
    def __init__(self, config: dict):
        algo = config.get("algorithm", {})
        self.settle_ratio = algo.get("auction_settle_ratio", 0.92)
        self.sell_fee = algo.get("resale_fee_rate", 0.1325)   # eBay ~13.25%
        self.min_match = algo.get("min_title_match", 0.55)
        self.mad_k = algo.get("outlier_mad_k", 3.0)
        self.half_life = algo.get("comp_half_life_days", 30.0)
        self.late_hours = algo.get("late_auction_hours", 6.0)
        self.tau_hours = algo.get("cost_model_tau_hours", 24.0)
        # bid-aware close model knobs (calibrated by learner over time):
        # - proxy premium: the displayed bid understates the leader's hidden
        #   max (proxy bidding); each bid adds a little, capped
        # - sniper floor: near close, a real card never settles at a token
        #   bid - last-minute bidders exist, so expected close is floored
        #   at a fraction of settle even when the current bid is tiny
        self.proxy_per_bid = algo.get("proxy_bid_per_bid", 0.02)
        self.proxy_cap = algo.get("proxy_bid_cap", 0.15)
        self.sniper_floor = algo.get("sniper_floor", 0.75)
        self.capture_half_life = algo.get("capture_half_life_hours", 12.0)
        self.premiums = {**GRADER_PREMIUM, **(algo.get("grader_premiums") or {})}
        comps_mod.GRADE_SHIFT.update(algo.get("grader_grade_shift") or {})
        comps_mod.UNGRADED_GRADE = float(algo.get("ungraded_grade", 5.0))
        # buy-side sales tax: eBay collects it at checkout (~8% swing that
        # was silently missing from EV). JP purchases are exports - exempt.
        # Vault routing (Andrew's rule): eBay items >= psa_vault.min_price
        # go to the PSA vault - no sales tax - but exiting means consigning
        # the card, losing ~consign_fee_rate of the sale. Below the
        # threshold: normal checkout, sales tax applies, no consign fee.
        self.sales_tax = algo.get("sales_tax_rate", 0.0)
        self.tax_free_mp = {str(m).upper() for m in
                            (algo.get("tax_free_marketplaces")
                             or ["YAHOO_JP", "PAYPAY_JP"])}
        vault = algo.get("psa_vault") or {}
        self.vault_enabled = bool(vault.get("enabled", False))
        self.vault_min = vault.get("min_price", 500.0)
        # vault exit: consignment REPLACES the marketplace fee - one all-in
        # sell-side rate (consignment + shipping + etc). Net = 1 - this.
        self.vault_sell_fee = vault.get("sell_fee_rate", 0.07)
        bin_cfg = config.get("bin", {})
        self.bin_half_life = bin_cfg.get("freshness_half_life_hours", 24.0)
        self.offer_ratio = bin_cfg.get("offer_target_ratio", 0.80)
        flt = config.get("filters", {})
        self.too_good = flt.get("too_good_ratio", 0.35)
        self.min_feedback = flt.get("flag_seller_feedback_below", 10)
        # Speed: with this many matched comps, the comp value dominates the
        # blend anyway (weight saturates ~8) - skip the price-guide HTTP
        # lookup entirely. Subject-injected valuation queries are unique
        # per listing, so they always miss the guide cache; this was
        # hundreds of PriceCharting calls (and 429 storms) per run.
        self.guide_skip_n = algo.get("guide_skip_min_comps", 8)
        # how many title words to inject as the subject on a set-wide query
        self.subject_tokens = algo.get("subject_tokens", 2)
        # comps needed before a card-specific valuation is trusted; below
        # this we fall back to the query's mixed median and flag the row
        self.min_specific_comps = algo.get("min_specific_comps", 3)
        self.mixed_pool_conf = algo.get("mixed_pool_confidence_cap", 0.25)

        # Self-improving layer: prefer learned parameters / model when the
        # accumulated close data supports them (see learner.py).
        try:
            import paths
            from learner import ClosePredictor
            self.predictor = ClosePredictor(
                paths.folder(paths.base_dir(config), paths.MODEL))
            learned = self.predictor.settle_ratio
            if learned:
                self.settle_ratio = learned
        except Exception:
            self.predictor = None
        self.guide = PriceGuide(config)

    # ---------------- fair value ----------------
    @staticmethod
    def _ask_based(asks: list[float]) -> float | None:
        """Value floor from live fixed-price asks: MAD-trim the outliers,
        take the 25th percentile, discount 10% (asks sit above clears)."""
        if len(asks) < 3:
            return None
        med = sorted(asks)[len(asks) // 2]
        mad = sorted(abs(a - med) for a in asks)[len(asks) // 2] or med * 0.05
        kept = sorted(a for a in asks if abs(a - med) <= 3 * mad)
        if len(kept) < 3:
            kept = sorted(asks)
        return kept[max(0, int(len(kept) * 0.25) - 0)] * 0.90

    def fair_value(self, query: str, comps: list[SoldComp],
                   asks: list[float] | None = None) -> Valuation:
        v = Valuation()
        cv, n, disp, match_q = robust_comp_value(
            query, comps, min_match=self.min_match,
            mad_k=self.mad_k, half_life_days=self.half_life,
            premiums=self.premiums)
        if cv is not None and n >= self.guide_skip_n:
            # enough matched comps that the guide barely moves the blend -
            # not worth an HTTP call (see guide_skip_min_comps note above).
            # Tradeoff: no comps-vs-guide dispute check on these rows, but
            # 8+ subject-guarded comps is the stronger signal anyway.
            gv = None
            v.audit_notes.append(f"guide skipped ({n} comps)")
        else:
            gv = self.guide.guide_value(query)
        v.comps_value, v.guide_value, v.n_comps, v.dispersion = cv, gv, n, disp
        v.sales_per_month = comp_velocity(query, comps,
                                          min_match=self.min_match)

        if cv is None and gv is None and asks:
            est = self._ask_based(asks)
            if est:
                v.fair_value = est
                v.confidence = round(min(0.35, 0.15 + 0.02 * len(asks)), 3)
                v.notes.append(f"ASK-BASED estimate from {len(asks)} live "
                               "asks (no sold comps) - verify before bidding")
                return v

        if cv is not None and gv is not None:
            # comps weight: grows with n (saturates ~8), shrinks with dispersion
            w = min(1.0, n / 8.0) * math.exp(-2.0 * disp)
            w = max(0.35, w)  # comps are the truer market signal; floor them
            v.fair_value = w * cv + (1 - w) * gv
            agreement = 1 - min(1.0, abs(cv - gv) / max(cv, gv))
            v.audit_notes.append(
                f"blend w_comps={w:.2f}, agreement={agreement:.0%}")
            if agreement < 0.25:
                # comps and guide are telling different stories (>4x apart)
                # - one of them priced the wrong thing (mongrel comp pool,
                # wrong guide product). Do NOT trust this fair value.
                v.disputed = True
                v.notes.append("VALUE DISPUTED: comps and guide disagree "
                               ">4x - verify before acting")
        elif cv is not None:
            v.fair_value = cv
            v.audit_notes.append("comps only")
        elif gv is not None:
            v.fair_value = gv * 0.95  # guide alone: haircut for staleness
            v.audit_notes.append("guide only")
        else:
            v.notes.append("no valuation source")
            return v

        # confidence
        sample = min(1.0, n / 8.0)
        tightness = math.exp(-3.0 * disp)
        match_f = match_q if n else 0.5
        conf = 0.45 * sample + 0.35 * tightness + 0.20 * match_f
        if cv is not None and gv is not None:
            conf = min(1.0, conf + 0.1 * (1 - min(1.0, abs(cv - gv) / max(cv, gv))))
        if v.disputed:
            conf = min(conf, 0.30)  # disputed value can't be high-confidence
        v.confidence = round(conf, 3)
        return v

    def _vault_route(self, listing: Listing, price: float) -> bool:
        """True when a purchase AT THIS PRICE would go through the PSA
        vault: eBay item at/above the vault threshold. No sales tax on
        the way in; consignment fee on the way out. (Auctions are judged
        on the expected close, not the current bid.)"""
        return (self.vault_enabled and listing.site == "ebay"
                and price >= self.vault_min)

    def _buy_tax(self, listing: Listing, price: float) -> float:
        """Effective buy-side sales-tax rate at this purchase price."""
        if self.sales_tax <= 0:
            return 0.0
        if (listing.marketplace or "").upper() in self.tax_free_mp:
            return 0.0
        if self._vault_route(listing, price):
            return 0.0
        return self.sales_tax

    # ---------------- EV per listing ----------------
    def score(self, listing: Listing, v: Valuation) -> Valuation:
        if v.fair_value <= 0:
            return v
        cost_now = listing.total_cost_now
        # Hybrid auction with NO bids: the displayed "current price" is the
        # seller's opening ask, which nobody can transact at. The only real
        # price on the page is the Buy It Now, so that is what "take it
        # now" has to mean - otherwise the row shows edge against a number
        # that does not exist (seen live 2026-07-25: a $499 opening bid on
        # a card valued at $2,821, reported as $1,584 of expected value).
        bin_all_in = 0.0
        if listing.has_buy_now and listing.buy_now_price > 0:
            bin_all_in = listing.buy_now_price + listing.shipping
            if listing.bid_count < 1:
                cost_now = bin_all_in
                v.notes.append(f"no bids yet - priced at the Buy It Now "
                               f"(${bin_all_in:,.0f}), not the opening bid")

        # PSA normalization: fair_value is in PSA-equivalent terms. Grade
        # shift already maps CGC/BGS one point down; residual premium
        # multipliers apply for SGC/BVG.
        g = grader_of(listing.title)
        factor = self.premiums.get(g, 1.0) if g else 1.0
        resale = v.fair_value * factor
        if factor != 1.0:
            v.notes.append(f"{g.upper()} valued at {factor:.0%} of PSA")
        gi = grade_info(listing.title)
        if gi and gi[1] != gi[2]:
            v.notes.append(f"{gi[0].upper()} {gi[1]} counted as PSA {gi[2]}")

        # edge_now = "take it at the current price": route (vault vs taxed
        # checkout) is decided at today's price. Vault route: no tax in,
        # and consignment replaces the marketplace fee on the way out
        # (one all-in rate - nets 93% by default).
        route_now = self._vault_route(listing, cost_now)
        proceeds_now = resale * (1 - (self.vault_sell_fee if route_now
                                      else self.sell_fee))
        tax_now = self._buy_tax(listing, cost_now)
        v.edge_now = round(proceeds_now - cost_now * (1 + tax_now), 2)
        proceeds = proceeds_now      # refined for EV once expected is known

        if listing.listing_type == "fixed":
            # Buy It Now: the edge is takeable immediately at asking price.
            # Capture decays with listing AGE instead of time-to-close - a
            # fresh underpriced BIN is the prize; one that has sat unsold
            # for a week at that price is probably mispriced for a reason.
            expected = cost_now
            age = listing.age_hours
            if age is None:
                capture = 0.5
            else:
                capture = max(0.10,
                              math.exp(-math.log(2) * age / self.bin_half_life))
                if age <= 2:
                    v.notes.append(f"FRESH: listed {age:.1f}h ago")
            if listing.best_offer and resale > 0:
                target = min(cost_now, self.offer_ratio * resale)
                v.notes.append(f"best offer - target ~${target:,.0f}")
        else:
            # Auction: expected final price interpolates between the bid
            # trajectory (market information) and the settle anchor (model
            # information, fair-value-derived - used most when the market
            # has said nothing). Bid-aware in two ways:
            #  1. proxy premium - with bids, the displayed price understates
            #     the leader's hidden max, so the interpolation base is
            #     lifted above the shown bid
            #  2. sniper floor - near close, expected never collapses to a
            #     token bid; hard-close markets price in the last minutes
            # settle ratio: price-band calibrated once closes accumulate
            sr = self.settle_ratio
            if self.predictor:
                band = self.predictor.settle_ratio_for(resale)
                if band:
                    sr = band
            settle = resale * sr
            hrs = listing.hours_remaining
            bids = listing.bid_count or 0
            ml_pred = None
            if self.predictor and resale > 0:
                ml_pred = self.predictor.predict_ratio(
                    hrs, cost_now / resale, bids, resale)
            if ml_pred is not None:
                expected = max(cost_now, resale * ml_pred)
                v.audit_notes.append("ML close model")
            else:
                adj_bid = cost_now
                if bids > 0:
                    adj_bid = cost_now * (1 + min(self.proxy_cap,
                                                  self.proxy_per_bid * bids))
                if hrs is None:
                    expected = max(cost_now, settle)
                else:
                    w = 1 - math.exp(-hrs / self.tau_hours)
                    expected = adj_bid + (settle - adj_bid) * w
                    if hrs <= self.late_hours:
                        floor = settle * self.sniper_floor
                        if expected < floor:
                            expected = floor
                            v.audit_notes.append(
                                f"sniper floor: close modeled >= "
                                f"{self.sniper_floor:.0%} of settle")
                    expected = max(cost_now, expected)
            # nobody bids an auction above a Buy It Now they could just take
            if bin_all_in > 0 and expected > bin_all_in:
                expected = bin_all_in
                v.audit_notes.append(
                    "expected close capped at the Buy It Now price")
            if hrs is None:
                capture = 0.25
            else:
                capture = max(0.05,
                              math.exp(-math.log(2) * hrs / self.capture_half_life))
                # ("ends in Xh" note removed - the Timing column already
                # shows the absolute end time; no need to say it twice)

        # EV route: judged at the pre-tax EXPECTED price (auctions get bid
        # up - a $100 bid closing near $2k is a vault purchase, not a taxed
        # one). For BINs expected == asking, so this matches edge_now.
        route_ev = self._vault_route(listing, expected)
        proceeds = resale * (1 - (self.vault_sell_fee if route_ev
                                  else self.sell_fee))
        tax_ev = self._buy_tax(listing, expected)
        if tax_ev > 0:
            expected *= (1 + tax_ev)
            v.notes.append(f"+{tax_ev:.0%} tax in")
        elif route_ev:
            v.notes.append("vault route (0% tax in / "
                           f"{self.vault_sell_fee:.0%} out)")
        v.expected_cost = round(expected, 2)
        v.expected_value = round(proceeds - expected, 2)
        v.roi = round(v.expected_value / expected, 4) if expected > 0 else 0.0
        # capital velocity: annualize the ROI by how fast this card actually
        # trades. A 15% flip that clears in 2 weeks beats a 40% flip that
        # takes a year - the ETF view of the same numbers.
        if v.sales_per_month and v.roi > 0:
            cycle_days = min(max(30.0 / v.sales_per_month, 7.0), 365.0)
            v.annualized_roi = round(v.roi * 365.0 / cycle_days, 3)

        # Defense: deals that look impossibly good usually are (scam, wrong
        # item, reprint the keyword filters missed). Slash capture and flag.
        if resale > 0 and cost_now < self.too_good * resale:
            capture *= 0.2
            v.notes.append("SUSPICIOUS: price far below market - verify item")
        if (listing.seller_feedback is not None
                and listing.seller_feedback < self.min_feedback):
            capture *= 0.7
            v.notes.append(f"low seller feedback ({listing.seller_feedback})")
        v.capture = round(capture, 3)

        lm = title_match_score(listing.query, listing.title)
        if listing.site == "yahoo_jp" and lm < 0.7:
            # Japanese titles can't fuzzy-match an English query; don't let
            # that alone kill confidence, but say so.
            lm = 0.7
            v.notes.append("JP listing - verify the exact card")
        if listing.misspell_from and lm < 0.7:
            # the low title match IS the opportunity here
            lm = 0.7
            v.notes.append(f"MISSPELLED listing (found via "
                           f"'{listing.misspell_from}') - verify card")
        v.confidence = round(v.confidence * (0.5 + 0.5 * lm), 3)
        # Final rank: relative upside x confidence x capturability.
        # ROI is capped at 100% here so a too-good-to-be-true price can't
        # out-rank legitimate deals through sheer implausibility.
        v.opportunity_score = round(min(v.roi, 1.0) * v.confidence * capture, 4)
        return v

    def _valuation_query(self, listing: Listing) -> tuple[str, str] | None:
        """(valuation_query, note) when the listing needs its OWN fair
        value - else None (query fair value applies as-is). Two triggers:

        1. GRADE: ungraded titles count as PSA UNGRADED_GRADE (5, Andrew's
           rule). A raw listing under a "PSA 9" query must NOT inherit the
           PSA 9 fair value; a PSA 10 under a broad raw query gets valued
           from PSA-10-equivalent comps instead of the raw median.
        2. SUBJECT: under a set-wide query ("Pokemon No Rarity Set") every
           card shared one mongrel comp median - a PSA 1 Mewtwo, a
           Gyarados and a Nidoking all "worth" the same number (seen live
           2026-07-25, 17 of the top 25 rows). When the query names no
           subject, inject the LISTING's subject so each card is valued
           against ITS OWN sales.
        """
        q_gi = grade_info(listing.query)
        l_gi = grade_info(listing.title)
        ungraded_eff = f"{comps_mod.UNGRADED_GRADE:g}"
        q_eff = q_gi[2] if q_gi else ungraded_eff
        l_eff = l_gi[2] if l_gi else ungraded_eff
        grade_differs = q_eff != l_eff

        # CARD NUMBER: "Michael Jordan 1984 Star" matches #101, #288, #195,
        # #7 and #26 - five different cards, $550 to $16,800, all sharing
        # one $29 fair value. When the listing names its number and the
        # query doesn't, value it against ITS OWN card.
        # Discovery queries are for BROWSING ("T206 PSA", "1933 Goudey
        # PSA") and their tab already says the values are mixed medians,
        # not bid targets. Pinning them to a card number would starve the
        # pool and empty the tab, so they keep the broad median.
        num_add = ""
        if not listing.discovery and card_number(listing.query) is None:
            listing_num = card_number(listing.title)
            if listing_num:
                num_add = f"#{listing_num}"

        subj_add, notes = "", []
        if not _subject_tokens(listing.query):
            # See comps.subject_candidates: real words only, context and
            # seller adjectives stripped, earliest-in-title first.
            subj = subject_candidates(listing.title,
                                      limit=self.subject_tokens)
            if subj:
                subj_add = " ".join(subj)
                notes.append(f"valued on '{subj_add}' (set-wide query)")

        if not grade_differs and not subj_add and not num_add:
            return None

        base = GRADE_RE.sub(" ", listing.query)
        parts = [" ".join(base.split())]       # collapse whitespace
        if subj_add:
            parts.append(subj_add)
        if num_add:
            parts.append(num_add)
            notes.append(f"valued as card {num_add}")
        if l_gi:
            # listing is graded: valuation query carries the LISTING's
            # grade token (also when equal to query's - base was stripped)
            parts.append(f"{l_gi[0].upper()} {l_gi[1]}")
            if grade_differs:
                notes.append(f"valued at listing grade "
                             f"{l_gi[0].upper()} {l_gi[1]}"
                             + (f" (query {q_gi[0].upper()} {q_gi[1]})"
                                if q_gi else " (ungraded query)"))
        elif grade_differs:
            notes.append(f"UNGRADED listing - valued as PSA {ungraded_eff} "
                         f"equivalent, not query grade "
                         f"{q_gi[0].upper()} {q_gi[1]}")
        return " ".join(parts), "; ".join(notes)

    def evaluate(self, listing: Listing, comps: list[SoldComp],
                 asks: list[float] | None = None) -> Opportunity:
        regrade = self._valuation_query(listing)
        if regrade is None:
            v = self.fair_value(listing.query, comps, asks)
            return Opportunity(listing=listing,
                               valuation=self.score(listing, v))

        vquery, note = regrade
        # no ask-based fallback here: the ask pool belongs to the QUERY's
        # grade population, not this listing's
        v = self.fair_value(vquery, comps, None)
        v.regraded = True
        v.notes.append(note)

        if v.n_comps >= self.min_specific_comps and v.fair_value > 0:
            return Opportunity(listing=listing,
                               valuation=self.score(listing, v))

        # Nothing in the pool sold as THIS card at THIS grade. A watchlist
        # query returns one page of sold results covering every card and
        # grade in the set, so slicing it down to one card usually leaves
        # nothing - the durable fix is a query per card. Until then, fall
        # back to the query's own (mixed) median rather than showing no
        # value at all, but say so loudly and cap confidence so it cannot
        # drive an alert. Kept out of fair_history either way (regraded).
        fallback = self.fair_value(listing.query, comps, asks)
        if fallback.fair_value > 0:
            fallback.regraded = True
            fallback.notes.append(
                f"MIXED POOL: no sales of {vquery!r} in the comps - this is "
                "a set-wide median across different cards, NOT a bid target")
            fallback.confidence = round(
                min(fallback.confidence, self.mixed_pool_conf), 3)
            fallback.audit_notes.append(
                f"specific query {vquery!r} had {v.n_comps} comps")
            v = fallback
        return Opportunity(listing=listing, valuation=self.score(listing, v))
