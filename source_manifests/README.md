# Source Manifests

A source manifest onboards an authorized marketplace JSON/CSV feed without
editing Python orchestration code. Start from `_example.yaml`, then validate
and install it with `Onboard Source.command`.

An enabled manifest is added to every scan automatically; it does not also
need to be added under `sites:`. Source Health reports it as a separate row.

Rules:

- IDs use lowercase letters, numbers, and underscores.
- Declare `auctions`, `fixed`, or both under `capabilities`.
- Map source columns/JSON paths to the normalized fields. `title`, `url`, and
  `current_price` are required.
- Local `.json` and `.csv` exports are supported.
- A remote endpoint is contacted only when `authorized: true`.
- Never store tokens, API keys, passwords, or secrets in a manifest. Put them
  in ignored `secrets.yaml` under `api_keys.<source_id>` or name environment
  variables with `access_token_env` / `api_key_env`.
- Economics are buy-side costs. Rates are decimals (`0.20` = 20%).

If a manifest is invalid, the scanner logs the exact error and ignores that
source without interrupting the rest of the run.
