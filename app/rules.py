import json
import math
import logging
import os
import tempfile
from pathlib import Path
import time

import yaml

from . import db
from .market import SYMBOLS, aggregate, session

log = logging.getLogger(__name__)

# Key, group, comparison, threshold, lookback, description.
DEFINITIONS = [
    ("higher_high","Entry","gt",0,30,"Change between the last two confirmed swing highs"),
    ("higher_low","Entry","gt",0,30,"Change between the last two confirmed swing lows"),
    ("bull_bodies","Entry","gt",0,10,"Slope of the last three bullish bodies, requiring monotonic growth"),
    ("breakout","Entry","gt",0,15,"Close above prior resistance after closing at or below it"),
    ("retest","Entry","gt",0,15,"Touch and recover above prior support within tolerance percent"),
    ("ema9","Entry","gt",0,20,"Close minus the 9-period EMA"),
    ("ema20","Entry","gt",0,30,"Close minus the 20-period EMA"),
    ("ema_slope","Entry","gt",0,30,"Smaller of the latest changes in the 9 and 20 EMAs"),
    ("sustained","Entry","ge",3,5,"Number of consecutive higher closes"),
    ("rising_support","Entry","gt",.02,5,"Support change in percent"),
    ("rising_resistance","Entry","gt",.02,5,"Resistance change in percent"),
    ("shrinking_bulls","Warning","lt",0,10,"Slope of bullish bodies, requiring monotonic shrinkage"),
    ("upper_wicks","Warning","ge",3,10,"Count of upper wicks at least twice the candle body"),
    ("rejections","Warning","ge",3,10,"Count of long upper wicks near prior resistance"),
    ("no_new_high","Warning","ge",5,10,"Candles since the lookback high"),
    ("lower_high","Warning","lt",0,30,"Change between the last two confirmed swing highs"),
    ("lower_low","Warning","lt",0,30,"Change between the last two confirmed swing lows"),
    ("bear_bodies","Warning","gt",0,10,"Slope of bearish bodies, requiring monotonic growth"),
    ("below_ema9","Warning","lt",0,20,"Close minus the 9-period EMA"),
    ("below_ema20","Exit","lt",0,30,"Close minus the 20-period EMA"),
    ("break_support","Exit","gt",0,15,"Prior support minus close on a breakdown"),
    ("failed_recovery","Exit","gt",0,15,"Wick reaches broken support and closes below it"),
    ("flat_resistance","Warning","le",.02,5,"Absolute resistance change in percent"),
    ("fall_resistance","Warning","lt",-.02,5,"Resistance change in percent"),
    ("flat_support","Warning","le",.02,5,"Absolute support change in percent"),
    ("fall_support","Warning","lt",-.02,5,"Support change in percent"),
    ("relative_volume","Entry","ge",1.5,20,"Volume divided by mean of previous volumes"),
    ("vwap","Entry","gt",0,20,"Close minus rolling volume-weighted typical price"),
    ("rsi","Entry","ge",55,14,"Simple RSI over the configured bars"),
    ("atr","Warning","ge",1,14,"Mean true range as percent of last close"),
    ("spread","Risk","ge",.25,1,"Bid-ask spread as percent of midpoint"),
    ("trend5","Entry","gt",0,20,"Five-minute close minus 9-period EMA"),
]
META = {key: {"group": group, "comparison": comp, "description": desc}
        for key, group, comp, _, _, desc in DEFINITIONS}
DEFAULT = {"level_window": 30, "tolerance_pct": .05, "trail_minutes": 60,
           "rules": {k: dict(enabled=k not in {"relative_volume","vwap","rsi","atr","spread"},
                      symbol="SPY", timeframe=5 if k == "trend5" else 1,
                      mandatory=k in {"ema9","ema20","trend5"}, threshold=threshold,
                      lookback=lookback, confirmations=1, weight=1, wick_ratio=2)
                     for k, _, _, threshold, lookback, _ in DEFINITIONS}}
COMPARE = {"gt":lambda x,y:x>y, "lt":lambda x,y:x<y,
           "ge":lambda x,y:x>=y, "le":lambda x,y:x<=y}

def validate(config):
    def number(x, low, high):
        return type(x) in (int,float) and math.isfinite(x) and low <= x <= high
    def integer(x, low, high):
        return type(x) is int and low <= x <= high
    if not isinstance(config, dict) or set(config) != set(DEFAULT):
        raise ValueError("Unexpected configuration fields")
    if not integer(config["level_window"], 5, 120) or not integer(config["trail_minutes"], 5, 390) or not number(config["tolerance_pct"], 0, 5):
        raise ValueError("Invalid level settings")
    rules = config["rules"]
    if not isinstance(rules, dict) or set(rules) != set(META):
        raise ValueError("YAML must define all predefined rules")
    for key, rule in rules.items():
        if (not isinstance(rule,dict) or set(rule) != set(DEFAULT["rules"][key])
            or type(rule["enabled"]) is not bool or type(rule["mandatory"]) is not bool
            or rule["symbol"] not in SYMBOLS or type(rule["timeframe"]) is not int or rule["timeframe"] not in (1,5)
            or not integer(rule["lookback"],1,120) or not integer(rule["confirmations"],1,10)
            or not number(rule["weight"],0,20) or not number(rule["threshold"],-100000,100000)
            or not number(rule["wick_ratio"],.1,100)):
            raise ValueError(f"Invalid parameters for {key}")
        if rule["mandatory"] and META[key]["group"] != "Entry":
            raise ValueError(f"Only entry conditions can be mandatory: {key}")
    if not any(x["enabled"] and x["mandatory"] for x in rules.values()):
        raise ValueError("Enable at least one mandatory entry condition")
    return config

def version(conn):
    row = conn.execute("SELECT id,yaml FROM versions ORDER BY id DESC LIMIT 1").fetchone()
    return (row["id"], yaml.safe_load(row["yaml"])) if row else (None, DEFAULT)

def save(conn, config):
    validate(config)
    return conn.execute("INSERT INTO versions(t,yaml) VALUES(?,?)",
                        (int(time.time()), yaml.safe_dump(config))).lastrowid

def export_versions(conn, ident=None):
    """Mirror committed YAML documents in the data volume for inspection."""
    folder = db.ROOT / "rules"
    rows = (conn.execute("SELECT id,yaml FROM versions") if ident is None else
            conn.execute("SELECT id,yaml FROM versions WHERE id=?",(ident,)))
    try:
        folder.mkdir(exist_ok=True)
        for row in rows:
            target = folder / f"v{row['id']:06d}.yaml"
            if target.exists() and target.read_text()==row["yaml"]:
                continue
            with tempfile.NamedTemporaryFile(mode="w",dir=folder,delete=False) as stream:
                stream.write(row["yaml"])
            try:
                os.replace(stream.name,target)
            finally:
                Path(stream.name).unlink(missing_ok=True)
    except OSError:
        log.exception("version_file_export_failed")

def ema(values, period):
    if not values:
        return []
    result = [values[0]]
    factor = 2 / (period + 1)
    for value in values[1:]:
        result.append(result[-1] + factor * (value - result[-1]))
    return result

def swings(rows, field, radius=2, step=60):
    compare = max if field == "h" else min
    found = []
    for i in range(radius, len(rows)-radius):
        window = rows[i-radius:i+radius+1]
        if window[-1]["t"] - window[0]["t"] != radius * 2 * step:
            continue
        values = [x[field] for x in window]
        if values[radius] == compare(values) and values.count(values[radius]) == 1:
            found.append(rows[i])
    return found

def levels(rows, window):
    highs = swings(rows[-window:], "h")
    lows = swings(rows[-window:], "l")
    return (lows[-1]["l"] if lows else None, highs[-1]["h"] if highs else None,
            {"support_pivot":lows[-1]["t"] if lows else None,
             "resistance_pivot":highs[-1]["t"] if highs else None})

def pct(a,b):
    return 100 * (a/b-1) if a is not None and b else None

def level_change(trail, field, minutes):
    if not trail:
        return None
    last = trail[-1]
    old = next((r for r in reversed(trail) if r["t"] == last["t"]-60*minutes), None)
    return pct(last[field], old[field]) if old else None

def wick(row, ratio):
    body = max(abs(row["o"]-row["c"]), row["c"]*.00001, .000001)
    return row["h"]-max(row["o"],row["c"]) >= ratio*body

def measure(key, rows, trail, cfg, tolerance, quote):
    look = cfg["lookback"]
    data = rows[-look:]
    if not data:
        return None
    step = cfg["timeframe"] * 60
    if (len(data)>1 and data[-1]["t"]-data[-2]["t"]!=step
        and key not in {"spread","ema9","ema20","ema_slope","below_ema9","below_ema20","trend5"}):
        return None
    x = data[-1]
    c = x["c"]
    prev = rows[-2] if len(rows)>1 else None
    prior = next((p for p in reversed(trail) if p["t"] == x["t"]-60), {})
    support, resistance = prior.get("s"), prior.get("r")
    if key in ("higher_high","lower_high","higher_low","lower_low"):
        field = "h" if key.endswith("high") else "l"
        pivots = swings(data, field, step=cfg["timeframe"]*60)
        return pivots[-1][field]-pivots[-2][field] if len(pivots)>1 else None
    if key in ("bull_bodies","shrinking_bulls","bear_bodies"):
        direction = -1 if key=="bear_bodies" else 1
        bodies = [direction*(r["c"]-r["o"]) for r in data if direction*(r["c"]-r["o"])>0][-3:]
        if len(bodies)<3:
            return None
        ordered = sorted(bodies, reverse=key=="shrinking_bulls")
        return (bodies[2]-bodies[0])/2 if ordered==bodies else 0
    if key in ("ema9","ema20","below_ema9","below_ema20","ema_slope","trend5"):
        period = 20 if key.endswith("20") or key=="ema_slope" else 9
        if len(data)<period + (key=="ema_slope"):
            return None
        values = [r["c"] for r in data]
        short, long = ema(values,9), ema(values,20)
        if key=="ema_slope":
            return min(short[-1]-short[-2],long[-1]-long[-2])
        return c-(long if period==20 else short)[-1]
    if key in ("rising_support","rising_resistance","flat_support","flat_resistance","fall_support","fall_resistance"):
        delta = level_change(trail, "s" if key.endswith("support") else "r", look)
        return abs(delta) if delta is not None and key.startswith("flat") else delta
    if key == "breakout":
        return max(0,c-resistance) if prev and resistance and prev["c"]<=resistance else (0 if prev and resistance else None)
    if key == "break_support":
        return max(0,support-c) if prev and support and prev["c"]>=support else (0 if prev and support else None)
    if key == "retest":
        return int(abs(x["l"]/support-1)*100<=tolerance and c>support) if support else None
    if key == "failed_recovery":
        return int(prev["c"]<support and x["h"]>=support*(1-tolerance/100) and c<support) if support and prev else None
    if key == "sustained":
        count = 0
        for a,b in zip(reversed(data[:-1]),reversed(data[1:])):
            if b["c"] <= a["c"]:
                break
            count += 1
        return count
    if key in ("upper_wicks","rejections"):
        if len(data)<look or (key=="rejections" and resistance is None):
            return None
        return sum(wick(r,cfg["wick_ratio"]) and (key=="upper_wicks" or
                   (abs(r["h"]/resistance-1)*100<=tolerance and r["c"]<resistance)) for r in data)
    if key=="no_new_high":
        return len(data)-1-max(range(len(data)),key=lambda i:(data[i]["h"],i)) if len(data)>=look else None
    if key=="spread":
        if not quote or not 0<quote["bid"]<=quote["ask"]:
            return None
        return 200*(quote["ask"]-quote["bid"])/(quote["ask"]+quote["bid"])
    if key=="relative_volume":
        past = rows[-look-1:-1]
        total = sum(r["v"] for r in past)
        return x["v"]*look/total if len(past)==look and total else None
    if key=="vwap":
        total = sum(r["v"] for r in data)
        return c-sum((r["h"]+r["l"]+r["c"])*r["v"]/3 for r in data)/total if len(data)==look and total else None
    if len(rows)<look+1:
        return None
    pairs = list(zip(rows[-look-1:-1],data))
    if key=="atr":
        return sum(max(b["h"]-b["l"],abs(b["h"]-a["c"]),abs(b["l"]-a["c"])) for a,b in pairs)/look/c*100
    changes = [b["c"]-a["c"] for a,b in pairs]
    up, down = sum(max(0,d) for d in changes), sum(max(0,-d) for d in changes)
    return 100*up/(up+down) if up+down else 50

def classify(results, quality, in_position=False):
    enabled = [r for r in results if r["state"]!="insufficient"]
    weight = sum(r["weight"] for r in enabled)
    score = round(50 + 50*sum(r["contribution"] for r in enabled)/weight) if weight else 50
    positive = [r["key"] for r in results if r["state"]=="pass" and r["group"]=="Entry"]
    negative = [r["key"] for r in results if r["state"]=="pass" and r["group"]!="Entry"]
    failed = [r["key"] for r in results if r["mandatory"] and r["state"]!="pass"]
    unknown = any(r["mandatory"] and r["state"]=="insufficient" for r in results)
    if quality!="fresh" or unknown:
        label = "Insufficient data"
    elif any(r["group"]=="Risk" and r["state"]=="pass" for r in results):
        label = "Potential exit" if in_position else "No opportunity"
    elif in_position and any(r["group"]=="Exit" and r["state"]=="pass" for r in results):
        label = "Potential exit"
    elif negative and score<=50:
        label = "Momentum weakening" if in_position or positive else "No opportunity"
    elif in_position:
        label = "Hold"
    elif not failed and not negative and score>=60:
        label = "Potential entry"
    elif failed and positive and score>=65:
        label = "Momentum strengthening"
    else:
        label = "Wait for confirmation" if positive else "No opportunity"
    keys = set(positive+negative)
    regime = ("Established downward momentum" if {"lower_high","lower_low","below_ema20"} <= keys
              else "Potential trend reversal" if "break_support" in keys and negative
              else "Temporary pullback" if {"ema20","trend5","below_ema9"} <= keys
              else "Weakening momentum" if negative and score<=55
              else "Healthy upward momentum" if not failed and score>=60 and not negative
              else "Consolidation") if quality=="fresh" and not unknown else "Unavailable"
    return dict(label=label, score=score, regime=regime, supporting=positive, conflicting=negative,
                failed=failed, explanation=f"{label}. {len(positive)} bullish conditions and {len(negative)} warnings. "
                f"{len(failed)} mandatory conditions unmet. Data quality: {quality}.")

def evaluate(conn, t, now, feed):
    ident, config = version(conn)
    start = session(t)["start"]
    if start is None:
        return
    rows, trails = {}, {}
    for symbol in SYMBOLS:
        rows[symbol] = [dict(x) for x in conn.execute(
            "SELECT * FROM bars WHERE symbol=? AND t BETWEEN ? AND ? ORDER BY t",(symbol,start,t))]
        if rows[symbol] and rows[symbol][-1]["t"]==t:
            s,r,evidence = levels(rows[symbol],config["level_window"])
            conn.execute("INSERT OR IGNORE INTO levels VALUES(?,?,?,?,?)",(symbol,t,s,r,json.dumps(evidence | {"observed":now,"version":ident})))
        trails[symbol] = [dict(x) for x in conn.execute(
            "SELECT * FROM levels WHERE symbol=? AND t BETWEEN ? AND ? ORDER BY t",(symbol,start,t))]
    if now-t>120 or not rows["SPY"] or rows["SPY"][-1]["t"]!=t:
        return
    five = {}
    for s,x in rows.items():
        current = aggregate(x)
        if len(current)<130:
            prior = [dict(b) for b in conn.execute(
                """SELECT * FROM (SELECT * FROM bars WHERE symbol=? AND t<?
                   ORDER BY t DESC LIMIT 650) ORDER BY t""",(s,start))]
            current = aggregate(prior) + current
        five[s] = current
    frames = {(s,1): x for s,x in rows.items()} | {(s,5):x for s,x in five.items()}
    results = []
    for key,cfg in config["rules"].items():
        if not cfg["enabled"]:
            continue
        meta = META[key]
        samples = frames[cfg["symbol"],cfg["timeframe"]]
        values = []
        latest_expected = t if cfg["timeframe"]==1 else (t+60)//300*300-300
        is_current = samples and samples[-1]["t"]==latest_expected
        for lag in range(cfg["confirmations"]):
            subset = samples[:len(samples)-lag]
            end = subset[-1]["t"] if subset else 0
            quote = feed.quotes.get(cfg["symbol"])
            if quote and not end <= quote["t"] <= end+60*cfg["timeframe"]:
                quote = None
            values.append(measure(key,subset,[x for x in trails[cfg["symbol"]] if x["t"]<=end],
                                  cfg,config["tolerance_pct"],quote) if is_current else None)
        measured = values[0]
        passed = all(v is not None and COMPARE[meta["comparison"]](v,cfg["threshold"]) for v in values)
        state = "insufficient" if any(v is None for v in values) else "pass" if passed else "fail"
        results.append(dict(key=key,**meta,state=state,value=measured,threshold=cfg["threshold"],
                            mandatory=cfg["mandatory"],weight=cfg["weight"],t=now,
                            contribution=(cfg["weight"]*(1 if meta["group"]=="Entry" else -1)) if passed else 0))
    mandatory_symbols = {v["symbol"] for v in config["rules"].values() if v["enabled"] and v["mandatory"]}
    for symbol in SYMBOLS[1:]:
        if any(not rows[s] or rows[s][-1]["t"]!=t for s in mandatory_symbols):
            continue
        if not rows[symbol] or rows[symbol][-1]["t"]!=t:
            continue
        prices = {s:(rr[-1]["c"] if rr else None) for s,rr in rows.items()}
        required = {"SPY",symbol} | {v["symbol"] for v in config["rules"].values()
                                     if v["enabled"] and v["mandatory"]}
        quality = "fresh" if feed.status=="Connected" and session(now)["open"] and all(
            0<=now-int(feed.prices.get(s,{}).get("t",0))<=30 and
            {r["t"] for r in rows[s][-5:]} >= set(range(max(start,t-240),t+60,60))
            for s in required) else "stale or incomplete"
        payload = dict(t=now, candle=t, symbol=symbol, price=prices[symbol], spy=prices["SPY"],
                       quality=quality, version=ident, rules=results,
                       levels={s:(v[-1] if v else None) for s,v in trails.items()},
                       **classify(results,quality))
        conn.execute("INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)",(symbol,t,now,json.dumps(payload)))
