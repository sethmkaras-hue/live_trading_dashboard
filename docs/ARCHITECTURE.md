# Architecture and storage

A single FastAPI process owns the Alpaca streaming connection and REST gap repair. It serves the React bundle and pushes the account and market state to browsers over a same-origin WebSocket. Market ingestion continues while all browsers are closed. The backend uses exchange-calendars' XNYS schedule for regular sessions, including holidays, early closes, and Eastern Time changes. Application timestamps are persisted as UTC Unix timestamps. The application logs stream and repair errors without logging API headers.

## Data path

1. The Alpaca connection authenticates and subscribes to trades, BBO quotes and completed bars on the IEX feed. It reconnects with backoff. REST requests specify IEX and hydrate recent sessions at startup, then revisit the current session to repair gaps.
2. SQLite deduplicates one-minute bars per symbol and minute. The five-minute aggregator requires all five constituent bars with their expected timestamps. Charts expose missing minute slots as gaps.
3. Confirmed levels are written once per symbol and minute. Late bars can fill an absent level observation. They do not alter an observation that was previously written. The evidence records the pivot times, observation time, and configuration.
4. The rule engine evaluates and records a signal after both SPY and the instrument have the completed bar. Freshness, missing bars, configuration changes and current risk limits gate what the live dashboard labels actionable.
5. Executions are recorded in an immediate transaction. The ordered ledger is validated after every write or edit, so corrections cannot leave an oversold long position. Duplicate submit identifiers are idempotent. A matching fill fingerprint catches repeated form submissions with a new identifier.
6. A browser-independent SQLite WAL database is the durable source. Risk and performance use the ledger and available observed market prices.

## Schema version 2

The bootstrap migration creates these tables and stores the schema version in SQLite's `user_version`. Newer schema versions are refused.

| Table | Key | Data |
|---|---|---|
| bars | symbol, candle-open time | First completed IEX OHLCV observation |
| levels | symbol, candle-open time | Frozen support and resistance and evidence JSON |
| versions | id | Application time and complete YAML document |
| snapshots | symbol, candle-open time | Observation time and rule evidence JSON |
| executions | id | UTC fill time, side, ticker, price, quantity, fees, stop, target, notes, checklist, archived signal and rule version |
| quotes | symbol, source trade time | Observed trade price and UTC ingestion time |
| screenshots | execution id | Optional PNG, JPEG or WebP chart image |
| flows | id | UTC recorded time, signed cash amount and note |
| settings | key | JSON document for risk preferences and stage |

The UI exports each active YAML document as a versioned file. SQLite stores the immutable YAML versions in the same backup as journal data. Rollback inserts a new version with earlier settings. Live-rule evaluation and writes use the committed rule version.

## Accounting

Weighted-average cost basis is maintained per symbol while it is long. A sell allocates purchase cost and entry fees in proportion to shares sold and subtracts that sell's fee. A completed trade is the interval from flat to long to flat for one symbol. Net expectancy uses completed-trade net P&L. Period and account P&L count realized partial sells as they occur. Planned risk sums initial fill-by-fill risk. Position stop is the cost-weighted stop price in dollars per share of the remaining position. The most recent buy supplies the optional target.

Progression uses completed trades assigned to their entry stage. The thresholds use net profitability, positive expectancy, configured profit factor, drawdown and checklist compliance, 30 trades, and available recorded capital. Advancement takes a manual action. Independent risk settings still apply after advancement.

Recorded equity uses starting cash, realized net results and net cash flows. The cash-flow-adjusted return divides realized P&L by starting balance plus time-weighted signed cash flows over the recorded period (Modified Dietz denominator). An undefined ratio is shown as an em dash. Completed-trade drawdown uses realized net results in closing order. Unrealized P&L is a gross estimate based on the last observed IEX trade. Peak and trough use observed IEX trades during the position.

Execution context is limited to observations timestamped and received by the actual execution time. A late journal entry does not pull in the current live signal or a historical REST candle that arrived afterward.
