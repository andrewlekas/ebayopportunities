DROP PRICE-GUIDE CSV FILES IN THIS FOLDER
=========================================

Any .csv file you put here is read at the start of every scan and used
INSTEAD of calling the paid API. Filenames don't matter. You can add one
set or a hundred; every set you add is a set that stops costing API calls.

Why bother
----------
The paid API allows one call per second, and a scan can need hundreds of
lookups - that's the single biggest reason a full scan takes as long as it
does. A CSV gives us a whole set in one download, and PriceCharting
regenerates the files every 24 hours.

Where to get them
-----------------
You already have access - CSV download comes with the same Legendary
subscription that gives you API access.

  1. Go to a set page on pricecharting.com or sportscardspro.com
     e.g. sportscardspro.com/console/basketball-cards-1986-fleer
          pricecharting.com/console/pokemon-base-set
  2. Click "Download Price List"
  3. Save the file into this folder

Or grab everything at once from:
  pricecharting.com/subscriptions  ->  "API/Download" button

Which sets are worth downloading
--------------------------------
The ones your watchlist actually hits. After a scan, run

    Check Price CSVs.command

and it will tell you how many rows are loaded and which of your watchlist
queries are still falling through to the API.

Notes
-----
* Files are re-read at the start of every scan - no import step, no restart.
* Anything without "product-name" and "console-name" columns is ignored,
  so a stray spreadsheet in here does no harm.
* Cards not covered by your CSVs still work; they just use the API as before.
* Prices are only refreshed when you download a fresh file. If a set matters
  and moves fast, re-download it periodically.
