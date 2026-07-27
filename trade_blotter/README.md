# Trade Blotter

`trade_blotter.csv` is the persistent source of truth for opportunities you
are considering and trades you actually make. Every live scan automatically
adds its 50 strongest tradeable non-watch opportunities and refreshes their
market data without overwriting your workflow fields.

Open it with `Open Trade Blotter.command`. Edit these fields:

- `status`: Discovered, Verified, Watching, Bid/Offer Placed, Won, Lost,
  Received, Listed, Sold, or Passed
- `verified`: yes/no after you inspect the listing, seller, certificate, and
  comps
- `planned_bid_or_offer`
- actual purchase price, buyer fees, shipping, tax, and workflow dates
- `asking_price`
- `sale_proceeds`: net cash received after sell-side fees
- `notes`

`actual_landed_cost`, `realized_profit`, `realized_roi`, and `holding_days`
are recalculated on every scan. The Excel `Trade Blotter` tab is a snapshot;
editing the workbook does not update this CSV.

The CSV is ignored by Git because it contains private financial activity.
If the schema changes, the scanner makes a timestamped backup before
migrating it.
