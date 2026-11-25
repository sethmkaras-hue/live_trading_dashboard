import asyncio
import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import exchange_calendars
import httpx
import pandas as pd
from functools import lru_cache
import websockets

from .db import database

log = logging.getLogger(__name__)
CALENDAR = exchange_calendars.get_calendar("XNYS", start="2000-01-01", end="2045-12-31")
SYMBOLS = tuple(dict.fromkeys(["SPY", "SPYU", *filter(None, (
    s.strip().upper() for s in os.getenv("EXTRA_SYMBOLS", "").split(",")
))]))
if len(SYMBOLS) > 6 or any(not s.isascii() or not s.replace(".", "").isalnum() for s in SYMBOLS):
    raise ValueError("Configure at most four valid additional symbols")

KEY = os.getenv("APCA_API_KEY_ID")
SECRET = os.getenv("APCA_API_SECRET_KEY")

def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()

def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()

@lru_cache(maxsize=30)
def session_bounds(day):
    label = pd.Timestamp(day)
    if label not in CALENDAR.schedule.index:
        return None, None
    hours = CALENDAR.schedule.loc[label]
    return int(hours["open"].timestamp()), int(hours["close"].timestamp())


def session(now):
    day = pd.Timestamp(now, unit="s", tz="UTC").tz_convert("America/New_York").date()
    start, end = session_bounds(day)
    return {"start": start, "end": end, "open": start is not None and start <= now < end}

def aggregate(rows):
    buckets = defaultdict(list)
    for row in rows:
        buckets[row["t"] // 300 * 300].append(row)
    return [
        dict(t=t, o=group[0]["o"], h=max(x["h"] for x in group),
             l=min(x["l"] for x in group), c=group[-1]["c"], v=sum(x["v"] for x in group))
        for t, group in sorted(buckets.items())
        if [x["t"] for x in group] == list(range(t, t + 300, 60))
    ]

class Feed:
    def __init__(self):
        self.status = "Waiting for connection" if KEY and SECRET else "Alpaca credentials required"
        self.quotes = {}
        self.prices = {}
        self.counts = defaultdict(deque)
        self.latencies = defaultdict(lambda: deque(maxlen=120))
        self.reconnects = 0
        self.daily = {}
        self.revision = 0
        self.warmed_up = False
        self.last_backfill = None
        self.last_backfill_error = None
        self.evaluation_ms = None

    def write(self, messages, evaluate):
        received = time.time()
        now = int(received)
        latest = set()
        with database(True) as conn:
            for msg in messages:
                symbol = msg.get("S")
                kind = msg.get("T")
                if symbol not in SYMBOLS:
                    continue
                t = timestamp(msg["t"])
                if kind in ("b", "u"):
                    t = int(t)
                    hours = session(t)
                    if not hours["open"] or t + 60 > now:
                        continue
                    cursor = conn.execute("""
                        INSERT OR IGNORE INTO bars VALUES(?,?,?,?,?,?,?)
                        """, (symbol, t, *(msg[k] for k in ("o","h","l","c","v"))))
                    if cursor.rowcount:
                        latest.add(t)
                elif kind == "q" and t >= self.quotes.get(symbol, {}).get("t", 0):
                    self.quotes[symbol] = dict(t=t, bid=msg["bp"], ask=msg["ap"])
                elif kind == "t" and t >= self.prices.get(symbol, {}).get("t", 0):
                    self.prices[symbol] = dict(t=t, price=msg["p"])
                    self.counts[symbol].append(received)
                    self.latencies[symbol].append(max(0, received - t))
                    conn.execute("INSERT OR IGNORE INTO quotes VALUES(?,?,?,?)",
                                 (symbol, t, received, msg["p"]))
            for t in sorted(latest):
                started = time.perf_counter()
                evaluate(conn, t, now, self)
                if now-t<=120:
                    self.evaluation_ms = round((time.perf_counter()-started)*1000,2)
        if latest:
            self.revision += 1

    async def backfill(self, evaluate):
        now = int(time.time())
        hours = session(now)
        start = hours["start"] if hours["start"] and hours["start"] <= now else now - 7 * 86400
        if not self.warmed_up:
            start = min(start,now-7*86400)
        end = now//60*60-1
        if end<=start:
            return
        if self.warmed_up and not hours["open"] and self.last_backfill and now-self.last_backfill<900:
            return
        headers = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET}
        async with httpx.AsyncClient(base_url="https://data.alpaca.markets", headers=headers,
                                    timeout=20) as client:
            params = dict(symbols=",".join(SYMBOLS), feed="iex", timeframe="1Min",
                          start=iso(start), end=iso(end), limit=10000)
            messages = []
            while True:
                response = await client.get("/v2/stocks/bars", params=params)
                response.raise_for_status()
                data = response.json()
                messages.extend(dict(bar, S=symbol, T="b") for symbol, bars in
                                (data.get("bars") or {}).items() for bar in bars)
                token = data.get("next_page_token")
                if not token:
                    break
                params["page_token"] = token
            # Bound transaction time during startup warm-up.
            for i in range(0,len(messages),180):
                self.write(messages[i:i+180],evaluate)
                await asyncio.sleep(0)
            self.warmed_up = True
            self.last_backfill = now
            self.last_backfill_error = None
            response = await client.get("/v2/stocks/snapshots",
                                        params={"symbols": ",".join(SYMBOLS), "feed": "iex"})
            response.raise_for_status()
            self.daily = {s: x["prevDailyBar"]["c"] for s, x in response.json().items()
                          if x.get("prevDailyBar")}

    async def repair(self, evaluate):
        while True:
            try:
                await self.backfill(evaluate)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_backfill_error = (f"HTTP {exc.response.status_code}"
                                            if isinstance(exc,httpx.HTTPStatusError) else type(exc).__name__)
                log.warning("iex_backfill_failed error=%s", self.last_backfill_error)
            await asyncio.sleep(60)

    async def run(self, evaluate):
        if not (KEY and SECRET):
            return
        delay = 1
        while True:
            try:
                async with websockets.connect("wss://stream.data.alpaca.markets/v2/iex",
                                              open_timeout=10, max_queue=256) as ws:
                    await asyncio.wait_for(ws.recv(), 10)
                    await ws.send(json.dumps(dict(action="auth", key=KEY, secret=SECRET)))
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if not any(x.get("msg") == "authenticated" for x in reply):
                        raise ValueError("Alpaca authentication rejected")
                    await ws.send(json.dumps(dict(action="subscribe", trades=SYMBOLS,
                                                  quotes=SYMBOLS, bars=SYMBOLS)))
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if not any(x.get("T") == "subscription" and set(x.get("bars", [])) >= set(SYMBOLS)
                               for x in reply):
                        raise ValueError("Alpaca subscription rejected")
                    self.status = "Connected"
                    delay = 1
                    async for frame in ws:
                        messages = json.loads(frame)
                        if any(x.get("T") == "error" for x in messages):
                            raise ValueError("Alpaca stream reported an error")
                        self.write(messages, evaluate)
                    raise ConnectionError("IEX connection closed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status = "Reconnecting"
                self.reconnects += 1
                log.warning("iex_stream_retry type=%s delay=%s", type(exc).__name__, delay)
                await asyncio.sleep(delay)
                delay = min(60, delay * 2)

    def diagnostics(self, conn, now):
        hours = session(now)
        start = hours["start"]
        stop = min(now // 60 * 60, hours["end"]) if start else 0
        result = {}
        for symbol in SYMBOLS:
            ticks = self.counts[symbol]
            while ticks and ticks[0] <= now - 60:
                ticks.popleft()
            present = {x[0] for x in conn.execute(
                "SELECT t FROM bars WHERE symbol=? AND t>=? AND t<?", (symbol, start or 0, stop))}
            missing = sorted(set(range(start, stop, 60)) - present) if start and stop > start else []
            tick = self.prices.get(symbol, {})
            age = now - int(tick["t"]) if tick else None
            fresh = self.status == "Connected" and hours["open"] and age is not None and 0 <= age <= 30
            result[symbol] = dict(tick, age=age, fresh=fresh, updates_per_minute=len(ticks),
                                  mean_latency=round(sum(self.latencies[symbol]) /
                                  len(self.latencies[symbol]), 2) if self.latencies[symbol] else None,
                                  candles=len(present), missing=missing, quote=self.quotes.get(symbol),
                                  daily_change=100 * (tick["price"] / self.daily[symbol] - 1)
                                  if tick and self.daily.get(symbol) else None)
        return dict(status=self.status, reconnects=self.reconnects, session=hours, symbols=result,
                    last_backfill=self.last_backfill, backfill_error=self.last_backfill_error,
                    evaluation_ms=self.evaluation_ms)
