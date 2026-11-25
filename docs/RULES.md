# Deterministic rules

Rules take completed candles in UTC time order. The configured comparison applies to the measurement below. A pass requires the requested number of consecutive evaluations to pass. An unavailable measurement makes that condition insufficient. Default thresholds are initial assumptions. `rules.example.yaml` contains the defaults.

Confirmed swings use two bars on each side. A high must be the unique maximum high in that five-bar window and a low the unique minimum low. The five timestamps must be contiguous at the selected interval. Current support and resistance are the last confirmed swing low and high in the configured window. The level trail stores each completed minute's observation. A level comparison uses the immediately prior one-minute level where noted. For 5m rules, candlestick history uses 5m bars and level slopes continue to use elapsed minutes.

| Rule | Measurement |
|---|---|
| Higher/lower swing high or low | Latest confirmed pivot price minus preceding pivot of the same kind |
| Growing or shrinking bullish bodies | Last three positive close-minus-open bodies must be monotone in the relevant direction. Measurement is (third minus first) / 2. Mixed ordering yields zero. |
| Growing bearish bodies | Same calculation on positive open-minus-close bodies in rising order |
| Breakout | Close minus prior resistance, floored at zero, when the prior close was at or below resistance |
| Support retest | 1 if the low is within the global percent tolerance of prior support and the close is above it, else 0 |
| EMA 9 / EMA 20 and below-EMA rules | Close minus its EMA. An EMA starts with the first included close and updates as previous EMA + 2/(period+1) × (close − previous EMA). Require one period of data. |
| EMA slope | Smaller of the last change in the 9 and 20 EMAs. Require at least 21 observations. |
| Sustained upward movement | Count of consecutive strictly rising closes, bounded by lookback |
| Rising/flat/falling support and resistance | 100 × (current level / level exactly lookback minutes ago − 1). Flat rules compare the absolute value. |
| Long upper wicks | Count bars where high minus max(open,close) is at least wick ratio × max(abs(close−open), close × 0.00001, 0.000001). The floor handles near-zero bodies. |
| Resistance rejections | Long upper wicks whose highs are within level tolerance of prior resistance and whose closes stay below it |
| Failure to establish new highs | Candles since the latest occurrence of the highest lookback high |
| Support breakdown | Prior support minus close, floored at zero, when the preceding close was at or above support. An intrabar wick alone does not trigger it. |
| Failed recovery | 1 when the prior close is below prior support, the latest high reaches it within tolerance, and the latest close stays below it |
| Relative volume | Last volume divided by mean volume in the preceding lookback bars |
| VWAP | Close minus rolling weighted mean of (high + low + close)/3, weighted by IEX volume |
| RSI | 100 × sum positive close changes / sum absolute close changes. A flat window scores 50. |
| ATR | Mean of max(high−low, abs(high−prior close), abs(low−prior close)) / last close × 100 |
| Spread | 200 × (ask − bid) / (ask + bid) with a valid BBO in the evaluated interval |
| Broader trend | Five-minute close minus 9-period EMA at the default 5m setting |

## Scoring and classification

Score is 50 + 50 × signed passing weight / evaluable enabled weight, rounded to an integer. Entry passes add weight. Warning, exit, and risk passes subtract weight. Failing conditions contribute zero. Disabled or insufficient rules are excluded from the denominator. This scale describes rule evidence and has no probability interpretation.

Missing or stale required data suppresses actionable signals. Enabled mandatory entry conditions must all pass. Risk warnings take priority, and an exit condition can independently flag an open position. A fresh instrument trade crossing the recorded stop can raise a risk alert between candle closes. An approaching session close or exhausted risk budget suppresses potential entry. The dashboard shows passing bullish and bearish conditions together when evidence conflicts.
