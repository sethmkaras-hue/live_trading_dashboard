# Market data validation

Live SPY and SPYU availability, update frequency, completed candle coverage, latency and reliability are unverified. No Alpaca credentials were supplied for live verification. Synthetic fixtures only validate software behavior.

## Collect an open-session sample

1. Configure Alpaca credentials on the deployment and open Settings during regular market hours.
2. Confirm the IEX status stays connected. Watch both symbols over at least 30 minutes, including a volatile period. Record updates per minute, last trade age, average transport latency, completed bar counts, missing timestamps, reconnects and backfill results. Check whether SPYU frequently crosses the 30-second freshness threshold.
3. Observe rule evaluation timing and verify two-minute and longer data interruptions produce the expected stale state. Confirm missing candles remain gaps.
4. Compare the observed behavior with Webull before using the feed for decisions. For thin IEX activity, use Webull for consolidated information and execution confirmation. The dashboard does not switch to another data source.

IEX covers a subset of US equity trading. Some minutes may have no eligible trade, and IEX volume and spread differ from consolidated values. Volume-dependent rules start disabled. Investigate coverage before relying on SPYU or extra instruments. Free alternatives to evaluate separately include an exchange-only feed such as a Cboe One display through a vendor with a free tier, or a broker-provided live display to supplement manual decisions. Check symbol availability, timestamps, API entitlement, and intraday rate limits for any alternative. A delayed feed is unsuitable for this live workflow.

## Known limitations

- Webull CSV import is gated until an actual exported file is supplied and its columns, timezone, fees and unique fill identifiers are verified. Exports and manual fills work independently.
- The first completed IEX candle is preserved. Late updates to existing candles do not overwrite that observation.
- One-minute indicators use current-session candles. Five-minute rules can warm up from earlier regular sessions. Mandatory entry still depends on complete, current bars and can remain unavailable early in the session.
- The app shows the last price with freshness status. Estimated excursions reflect trades the app observed and do not include prices from a disconnected period.
- Execution markers use the relevant candle slot and label the recorded time and price. When a slot has no candle, the execution remains visible in the journal.
- Default policy assumes USD, regular sessions, long positions, and a single private user. No order placement or automated broker synchronization is provided.
- Mobile layout is responsive in source. Managed browser relay startup prevented an iPhone-size browser check in this environment.
