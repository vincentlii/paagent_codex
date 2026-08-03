# ADR-0013: Multi-Venue Native Contract Units

## Status

Accepted.

## Context

PA Agent monitors XAU perpetual markets on Binance USD-M, OKX, and Bitget.
The exchanges expose compatible OHLCV concepts but different order quantity
units. Binance and Bitget express XAU quantity directly for these products.
OKX expresses quantity as contracts and declares the XAU value of one contract
through `ctVal * ctMult`.

Treating every quantity as base-asset quantity would corrupt structural-stop
sizing, leverage, fees, realized PnL, and portfolio-risk accounting. This is a
deterministic instrument fact, not a PA or Wyckoff interpretation.

## Decision

1. Native adapters own exchange-specific response envelopes, timeframe mapping,
   pagination, closed-bar facts, and instrument metadata parsing.
2. `InstrumentSpec.contract_multiplier` records base-asset exposure per venue
   quantity unit. Its backward-compatible default is `1.0`.
3. Paper sizing, active exposure, fees, and PnL use
   `quantity * contract_multiplier` as base exposure.
4. The multiplier is frozen into each paper order. Existing database rows are
   migrated with multiplier `1.0`; later metadata changes cannot rewrite old
   outcomes.
5. All three native adapters expose exchange volume as observed facts. Volume
   comparisons remain scoped to the same provider and instrument.
6. Instrument availability and current contract constraints are checked from
   public metadata at connection time. Product IDs in configuration are not
   accepted merely because they look valid.
7. These adapters never assign Brooks structures, Wyckoff events, direction,
   setup quality, or trade timing. Those remain LLM judgment over measured
   facts.

## Provider Contracts

- Binance USD-M: `/fapi/v1/exchangeInfo`, `/fapi/v1/klines`.
- OKX V5: `/api/v5/public/instruments`, `/api/v5/market/candles`; candle
  `confirm=1` is closed. Global REST domain is `https://openapi.okx.com`.
- Bitget V3: `/api/v3/market/instruments`, `/api/v3/market/candles`, and
  `/api/v3/market/history-candles`. Because candles do not carry a close flag,
  closure is computed from the exchange server clock and timeframe duration.

Primary references:

- https://www.okx.com/docs-v5/
- https://www.bitget.com/api-doc/uta/public/Instruments
- https://www.bitget.com/api-doc/uta/public/Get-Candle-Data
- https://developers.binance.com/docs/derivatives/usds-margined-futures/

## Consequences

- Cross-venue paper results use correct quantity units and remain reproducible.
- Public-data adapters stay independent and can evolve with venue APIs without
  creating exchange branches in the PA/VPA prompt or decision pipeline.
- A changed or unavailable exchange product fails the data-source probe instead
  of silently falling back to a different instrument.
- This decision enables paper research only. It does not authorize private API
  credentials or real order placement.

