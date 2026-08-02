# ADR-0011: Native market data and isolated paper execution

## Status

Accepted.

## Context

VPS research needs stable, exchange-native XAU contract data and persistent
execution outcomes. TradingView remains useful for charts, but a display symbol
does not provide contract filters, quantity steps, or minimum notional. Analysis
records and closed-OHLC counterfactuals also do not represent an account fill or
PnL ledger.

## Decision

1. `BinanceUsdmSource` reads public USD-M REST endpoints only. It resolves the
   requested contract from `exchangeInfo`, uses server time to distinguish the
   forming bar, and preserves base volume plus quote turnover. It has no private
   trading credentials or order endpoint.
2. Contract tick size, quantity step, quantity bounds, minimum notional, venue,
   and contract type are provider facts stored in `InstrumentSpec`; they are not
   inferred from price history or LLM text.
3. `PaperBroker` consumes an already validated trade plan. It owns sizing,
   persistent order state, fee/slippage assumptions, structural-stop risk,
   aggregate portfolio risk, and aggregate leverage. It does not decide market
   context, Setup, direction, or Wyckoff labels.
4. Paper fills advance only on later closed bars. When stop and target are both
   touched within one OHLC bar, stop-first is recorded explicitly as an
   ambiguous conservative assumption. TP1 closes the whole position until
   partial-exit semantics are separately specified and tested.
5. Paper events use a separate SQLite database. They are not written into
   `analysis_outcomes`, which remains reserved for reviewed real execution, or
   `counterfactual_outcomes`, which remains a stateless plan replay.
6. Paper execution is disabled by default. No live/private exchange execution is
   introduced by this decision.

## Consequences

- PA/VPA signal frequency is unchanged; portfolio constraints affect only what
  a simulated account can carry concurrently.
- Closed-bar execution cannot recover intrabar path ordering. Conservative
  ambiguity labels prevent those assumptions from becoming false ground truth.
- Native adapters for other venues can implement the same source and instrument
  contracts without changing analysis authority.

## Verification

- Public response parsing and contract identity tests.
- Forming/closed bar boundary tests using provider server time.
- Structural-stop, venue minimum, portfolio-risk, and leverage sizing tests.
- Persistent fill, restart, deduplication, TP, and ambiguous same-bar tests.
- Cross-contract history and counterfactual isolation tests.

## References

- Binance USD-M Futures REST market-data documentation:
  <https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data>
- Binance USD-M kline documentation:
  <https://developers.binance.com/en/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data>
