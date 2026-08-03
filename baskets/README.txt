CUSTOM BASKET PRICER - how to use it
====================================

Put a spreadsheet in THIS folder, then double-click
"Custom Basket Pricer.command" in the main folder.

Results land in  reports/basket pricer/


THE SPREADSHEET
---------------
.xlsx or .csv. Only "Card" is required. Header names are matched
loosely, so Name / Item / Product also work for the card column,
Condition for grade, and Paid / Basis for cost.

    Card                          Grade     Cost     Set
    1999 1st Edition Charizard    PSA 6.5   $2,034   Pokemon Base Set
    1999 1st Edition Alakazam     PSA 9     $5,200   Pokemon Base Set

  Grade  blank = ungraded. PSA 9, BGS 9.5, CGC 10, SGC 92 all work,
         and get the same cross-grader treatment as a real scan: a
         BGS 9.5 is priced as a PSA 8.5, a CGC 10 uses the published
         CGC-10 price, and no grade is ever rounded up.

  Cost   optional. Include it and you get Cost / Value / PnL columns
         and totals. Leave it out and you get a plain valuation.

  Set    optional but recommended. Without it, a card whose name
         exists in several sets is reported as ambiguous instead of
         being guessed at. "Charizard #4" is five products between
         $3,000 and $343,098, so guessing is not a favour.

Up to 500 rows. More than that and it prices the first 500 and says so.


NAMING
------
Write names however you keep them. Both of these find the same card:

    1999 1st Edition Charizard
    Charizard [1st Edition] #4

Variants must match exactly, both ways - "1st Edition" will not
resolve to the Shadowless or base copy, and a plain name will not
resolve to the 1st Edition.

Base Set Pikachu is a special case worth knowing: the guide has three
cards at #58 and only labels the unusual ones. Write "Pikachu Yellow"
for the ordinary card and "Pikachu Red" for the red-cheeks version.


STARTING FROM A WHOLE SET
-------------------------
Rather than typing 100+ names:

    .venv/bin/python code/basket_pricer.py \
        --seed-set "Pokemon Base Set" --variant "1st Edition"

writes a starter sheet here with every card in the set. Fill in the
Grade column and price it. Sealed product (booster boxes, packs) is
left out by default - priced as if it were a graded card it badly
inflates a set total. Add --include-sealed if you want it.

    --list-sets     the exact set names the local CSVs cover
                    (useful when --seed-set finds nothing)


COST
----
Anything the local price-guide CSVs cover is free and instant - no
API calls at all. Only cards the CSVs miss reach the paid API, and
never more than 50 of them per run (--api-cap N to change, or
--api-cap 0 to stay entirely local).

Run "Download Price Guides.command" if coverage looks thin.


WHAT THE NUMBER IS
------------------
Guide value. It ignores fees, shipping, and the fact that selling a
hundred cards at once moves the market. It is a valuation, not an
expected sale price.
