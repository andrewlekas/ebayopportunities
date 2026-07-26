# What to check on your next run — 2026-07-25

## Where everything lives now

The top level holds only what you touch. Everything else is in a named
folder:

| Folder | What's in it |
|---|---|
| `database/` | `history.db` — comps, observations, closes, alerts |
| `model/` | `learned_params.json`, `model.pkl` — the close model |
| `portfolio/` | `portfolio.csv` — your positions (edit this in Excel) |
| `reports/` | `Opp Runs/` and `BIN runs/` — the Excel output |
| `logs/` | `scan.log` and its rotations |
| `test results/` | `test_results.log`, `comps_test_result.txt` |
| `state/` | cookies, circuit-breaker state, run lock — machine-managed |
| `setup/` | `requirements.txt` |
| `docs/` | `README.md`, `FEATURES.md` |
| `code/` | the program |

Paths now resolve against `config.yaml`'s folder rather than wherever the
scan was launched from, so nothing breaks if you move or double-click from
somewhere else. `.venv` has to stay at the top level — every `.command`
calls it directly. There's a leftover `__pycache__` folder I couldn't
delete from here; drag it to the trash whenever, it just holds throwaway
compiled files.

---


Six things changed. Two you asked for, four were bugs. Here's how to
confirm each one actually works, in the order you'll see them.

---

## Step 1 — double-click `Run Tests.command` (about 5 seconds)

New file. It checks your API keys are intact, prints what the close model
is currently using, runs 36 regression tests, and runs the scanner
end-to-end on fake data. Nothing touches the network or your database.

You want the last line to say **`RESULT: everything passed.`**

It also writes `test_results.log`, so you can send me that file if
anything looks off.

## Step 2 — double-click `Run Scan.command`

Then open the report and `scan.log`.

---

# What should be different

### 1. The close model is back on your number, not a broken learned one

The scanner had taught itself that auctions close at **128% of fair
value**. That is what was killing your expected-value column — it was
pricing every auction as if you'd be outbid above fair, so profitable rows
were being dropped before they reached the report. On the 16:00 sweep it
threw out 201 rows for "negative EV" and sent zero alerts.

It learned that from 1,090 matched closes, 891 of which were valued at
under $50 — mongrel valuations like a graded Jungle card "worth" $2.85
that actually sold for $67. Your own calibration report said the median
auction closed at **25x** fair value, which is obviously not a market.

I've reset it. `learned_params.json` now says `n: 0`, and the engine is
using your hand-tuned `auction_settle_ratio: 0.92` from config.

**Check:** `Run Tests.command` prints
`cold start (0/20 trustworthy closes) - using the hand-tuned settle ratio`.

**This is the intended state, not a failure.** Every closed auction in the
database was recorded between 07-14 and 07-20, under valuation code that
has since been fixed, so none of it is safe to learn from. From now on the
scanner only learns from auctions where it recorded how much evidence the
valuation had. It rebuilds itself: the close-tracker settles up to 20
auctions per run, the learned ratio switches back on at 20 clean closes,
and the machine-learned model at 150. Expect a few days.

**In scan.log:**

```
grep "learner:" scan.log | tail -3
```

You should see the training filter breakdown and either "cold start" or,
once it has data, a settle ratio with the number of closed auctions behind
it.

### 2. Non-PSA slabs are no longer priced a full grade too high

You said it yourself: any other grading service takes a -1 grade penalty
against PSA. That was applied when matching sold comps but silently
skipped when looking up the price guide — so a CGC 10 was being priced off
the PSA 10 guide value instead of PSA 9.

Worst live example, from your 15:44 report: **four different grades of
Topsun Charizard were all cached at exactly $6,718** — PSA 9, CGC 9,
CGC 8.5, and a typo'd "CGC 85".

Half grades now interpolate between the grades either side of them instead
of rounding up, and if the guide can't price a grade honestly it returns
nothing and lets the sold comps carry the row.

**Check** the Crossover tab. The CGC 8.5 Topsun Charizard was claiming
$3,565 of regrade profit off a $6,382 fair value. It should now be priced
off roughly the Grade 7.5 level, and the row will either drop out or show
a much smaller number. Same for the SGC 9.5 Disney card.

**In scan.log:**

```
grep "grade routing" scan.log
```

Every distinct grade→price-field decision is logged, e.g.
`CGC 8.5 -> PSA 7.5 | cib-price<->new-price interpolated at grade 7.5`.

You'll also see a one-time line clearing 694 stale cached guide values.
Without that the fix would have been invisible for a week.

### 3. "CGC 85" is no longer read as grade 85

The grade reader accepted two-digit numbers, so a seller typing "CGC 85"
instead of "CGC 8.5" produced a card graded 85, which the report happily
printed as "counted as PSA 84". That row was in your Crossover tab with
$3,349 of claimed profit on a $1,886 listing.

Impossible grades now read as **ungraded**, which values the card down
rather than up. SGC's old 100-point labels (92, 96, 98) translate properly
to the modern scale.

**Check:** `grep "grade parser" scan.log` — it lists any grade tokens it
refused, so a new seller convention shows up as a log line instead of a
silent mispricing.

### 4. Pokemon grade floor is now PSA 5

Graded Pokemon at PSA 5 or below are dropped. Applies in PSA-equivalent
terms, so a CGC 6 also drops. Sports, games and watches are untouched,
grails are exempt, and raw cards are unaffected.

Nothing in your last report would have been removed by this — it's a
forward-looking filter.

### 5. Repacks, art prints and set-breaks are filtered

Added `repack` plus `poster`, `art print`, `chase box`, `set-break`,
`set break`. Two rows in your last report were being valued off real card
comps:

- "UNIVERSAL TREASURES BASKETBALL **Chase Box** LOADED…" — $979 EV
- "Ermsy MJ 1986 Michael Jordan Fleer RC BLACK RED **Poster Print**" — $897 EV

If any of these ever hide something real, delete the word from
`exclude_keywords` in config.yaml.

### 6. scan.log now tells you why rows were dropped

Instead of one lumped number, you get the breakdown:

```
output rules dropped 201 rows: expected value < $0 x184; negative ROI x9;
pure auction with zero bids x6; graded Pokemon at or below PSA 5 x2
```

Plus a new line counting listings whose valuation was too weak to be
written into history (`trust floor: N listing(s) valued below $50…`).

---

# Round 2 — five more fixes from the code review

### 7. Cards are now valued on the card, not the seller's adjectives

When a query names no specific card ("1999 1st Edition Pokemon Set"), the
scanner values each listing on its own subject. It was picking the three
longest leftover words in the title — which are almost always seller
adjectives. Across 599 of your real listings the most-injected "subjects"
were `symbol` (116x), `wotc` (67x), `tcg` (51x), `lp` (20x). A Jungle
Snorlax was being valued against *"investment beautiful centering"*.

Same 599 listings now give: `machamp, kabuto, charmander, zapdos, raichu,
aerodactyl, squirtle, bulbasaur, pikachu…` — actual cards.

This was the root cause of the junk valuations that poisoned the learner.
**Check:** the Notes column on set-wide rows should read *"valued on
'snorlax' (set-wide query)"*, not a string of adjectives.

### 8. "Pokémon" with the accent no longer splits into two words

7% of real titles contain accented characters. Nothing to check directly —
it just makes 7 above, the subject guard and the duplicate-collapse work
on those listings. Japanese titles are deliberately left alone.

### 9. Auction + Buy-It-Now listings are priced off the BIN

The Superman row from your last scan: $499 opening bid, 0 bids, fair
$2,821, reported **+$1,584 expected value**. That $499 was never
transactable. Priced against the real $2,400 Buy-It-Now it becomes
**−$1,286** and drops out of the report entirely.

**Check:** any auction row with 0 bids should either be gone, or say
*"no bids yet - priced at the Buy It Now ($X), not the opening bid"*.

### 10. The Today tab now has Max Bid **and** Breakeven

`Max Bid` is the highest price that still leaves you 15%. `Breakeven`
(grey, next to it) is where profit is exactly zero — the wall, not a
target. Your Dragonite row: Max Bid $1,981, Breakeven $2,278.

Change the target in config.yaml under `output → today →
max_bid_target_roi` if 15% isn't your number.

### 11. Sealed Pokemon no longer files as a video game

"sealed" was a Video Games keyword tested before Pokemon, so a sealed
Pokemon query landed in the wrong tab *and* skipped the Pokemon grade
floor. None of your current 90 queries were affected — this is
forward-looking, since you buy sealed.

---

# The honest caveats

- **Row counts will move and I can't predict the direction.** Removing the
  1.28 settle ratio should raise expected values and let more auctions
  through. The guide fix cuts fair values on non-PSA slabs, which removes
  some rows. Net effect is unknown until you run it.
- **Your last report only had 4 non-PSA graded rows.** The guide fix
  matters most for the Crossover tab, which is exactly the strategy it was
  silently overstating.
- **eBay's HTML lane was in cooldown when I finished** (until ~16:07), so
  the 16:00 sweep ran entirely on stale comp caches and the close-tracker
  settled nothing. If your run looks thin, check
  `cat .breaker_state.json` first — that's a blocking issue, not these
  changes.
- **Config knobs I added are all set to the values already in use**, except
  the Pokemon floor (3 → 5) and the new keywords. Nothing else should
  change behaviour on its own.

Anything that looks wrong: paste me the row or the log lines and I'll
reproduce it as a test before touching code.
