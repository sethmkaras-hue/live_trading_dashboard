import json
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, StrictBool

from .market import SYMBOLS, session
from .rules import classify

ET = ZoneInfo("America/New_York")
EPS = 1e-8
SIZES = [500,1000,2000,3000,5000]
DEFAULT_RISK = dict(starting_balance=2000, trade_risk=25, daily_loss=75, weekly_loss=200,
                    drawdown=200, consecutive_losses=3, max_size=500, close_buffer=15,
                    slippage_pct=.25, minimum_profit_factor=1.25, minimum_compliance=90,
                    **{f"stage_{i}":size for i,size in enumerate(SIZES,1)})

class Execution(BaseModel):
    id: str = Field(min_length=8,max_length=80)
    symbol: str = "SPYU"
    side: str
    t: int
    price: float = Field(gt=0,le=1e7)
    qty: float = Field(gt=0,le=1e7)
    fee: float = Field(0,ge=0,le=1e6)
    stop: float = Field(5,gt=0,lt=100)
    target: float | None = Field(None,gt=0,le=1e7)
    notes: str = Field("",max_length=4000)
    emotion: str = Field("",max_length=250)
    lessons: str = Field("",max_length=4000)
    checklist: dict[str,StrictBool] = Field(default_factory=dict)

def get_setting(conn,key,default):
    row = conn.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone()
    return json.loads(row[0]) if row else default

def risk_settings(conn):
    return DEFAULT_RISK | get_setting(conn,"risk",{})

def set_setting(conn,key,value):
    conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?)",(key,json.dumps(value)))

def context_at(conn,symbol,t):
    snapshot = conn.execute("""SELECT payload FROM snapshots WHERE symbol=? AND observed<=?
                               ORDER BY t DESC LIMIT 1""",(symbol,t)).fetchone()
    data = json.loads(snapshot[0]) if snapshot else {}
    if snapshot and t-data["t"] > 120:
        data = {}
    prices = {}
    for s in ("SPY",symbol):
        row = conn.execute("""SELECT price,t FROM quotes WHERE symbol=? AND t<=? AND observed<=?
            ORDER BY t DESC LIMIT 1""",(s,t,t)).fetchone()
        prices[s] = row["price"] if row and t-row["t"]<=30 else None
    if data.get("rules"):
        quantity = conn.execute("""SELECT COALESCE(SUM(CASE side WHEN 'buy' THEN qty ELSE -qty END),0)
                                  FROM executions WHERE symbol=? AND t<?""",(symbol,t)).fetchone()[0]
        data.update(classify(data["rules"],data["quality"],quantity>EPS))
    return dict(available=bool(data), signal=data or None, trade_prices=prices)

def record(conn,trade,edit=False):
    if (trade.symbol not in SYMBOLS[1:] or trade.side not in ("buy","sell") or
        not 946684800<=trade.t<=int(time.time())+5):
        raise ValueError("Invalid instrument, execution time or side")
    if trade.side=="buy" and trade.target is not None and trade.target<=trade.price:
        raise ValueError("Long profit target must exceed the entry price")
    existing = conn.execute("SELECT * FROM executions WHERE id=?",(trade.id,)).fetchone()
    if existing and not edit:
        return
    if edit and not existing:
        raise ValueError("Execution not found")
    duplicate = conn.execute("""SELECT id FROM executions WHERE symbol=? AND t=? AND side=? AND
        price=? AND qty=? AND fee=? AND id!=?""",
        (trade.symbol,trade.t,trade.side,trade.price,trade.qty,trade.fee,trade.id)).fetchone()
    if duplicate:
        raise ValueError("Matching execution already recorded")
    context = context_at(conn,trade.symbol,trade.t)
    archived = conn.execute("SELECT id FROM versions WHERE t<=? ORDER BY t DESC,id DESC LIMIT 1",
                            (trade.t,)).fetchone()
    ident = archived[0] if archived else None
    if existing and (existing["t"],existing["symbol"]) == (trade.t,trade.symbol):
        context,ident = json.loads(existing["context"]),existing["version"]
    notes = json.dumps(dict(notes=trade.notes,emotion=trade.emotion,lessons=trade.lessons))
    stage = existing["stage"] if existing else get_setting(conn,"stage",1)
    values = (trade.symbol,trade.t,trade.side,trade.price,trade.qty,trade.fee,trade.stop,trade.target,
              notes,json.dumps(trade.checklist),json.dumps(context),ident,stage)
    if edit:
        conn.execute("""UPDATE executions SET symbol=?,t=?,side=?,price=?,qty=?,fee=?,stop=?,target=?,
            notes=?,checklist=?,context=?,version=?,stage=? WHERE id=?""", (*values,trade.id))
    else:
        conn.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(trade.id,*values))
    summarize(conn)

def execution_rows(conn):
    return [dict(r) | {key:json.loads(r[key]) for key in ("context","notes","checklist")}
            for r in conn.execute("SELECT * FROM executions ORDER BY t,rowid")]

def summarize(conn):
    """Weighted-average cost basis. A trade spans flat → long → flat per symbol."""
    positions, complete, realized = {}, [], []
    for row in execution_rows(conn):
        s = row["symbol"]
        if row["side"]=="buy":
            if s not in positions:
                positions[s] = dict(id=row["id"], symbol=s, t=row["t"], qty=0., basis=0., fee_basis=0.,
                                    stop_basis=0., risk=0., gross=0., net=0., invested=0.,
                                    buy_qty=0., sell_qty=0., sell_notional=0., fees=0.,
                                    executions=[], stage=row["stage"], version=row["version"],
                                    compliant=bool(row["checklist"]) and all(row["checklist"].values()))
            p = positions[s]
            p["qty"] += row["qty"]
            cost = row["qty"]*row["price"]
            p["basis"] += cost
            p["fee_basis"] += row["fee"]
            p["stop_basis"] += cost*(1-row["stop"]/100)
            p["risk"] += cost*row["stop"]/100
            p["invested"] += cost
            p["buy_qty"] += row["qty"]
            p["target"] = row["target"]
            p["compliant"] &= bool(row["checklist"]) and all(row["checklist"].values())
        else:
            p = positions.get(s)
            if p is None or row["qty"] > p["qty"]+EPS:
                raise ValueError(f"Sale exceeds recorded long shares of {s} at execution time")
            fraction = min(1,row["qty"]/p["qty"])
            cost = p["basis"]*fraction
            entry_fee = p["fee_basis"]*fraction
            gross = row["qty"]*row["price"]-cost
            net = gross-entry_fee-row["fee"]
            p["gross"] += gross
            p["net"] += net
            p["sell_qty"] += row["qty"]
            p["sell_notional"] += row["qty"]*row["price"]
            p["qty"] -= row["qty"]
            p["basis"] -= cost
            p["fee_basis"] -= entry_fee
            p["stop_basis"] *= 1-fraction
            realized.append(dict(t=row["t"],net=net,symbol=s))
        p["executions"].append(row)
        p["fees"] += row["fee"]
        if p["qty"]<=EPS:
            p["exit_t"] = row["t"]
            p["holding_seconds"] = row["t"]-p["t"]
            p["return_pct"] = p["net"]/p["invested"]*100
            p["entry"] = p["invested"]/p["buy_qty"]
            p["exit"] = p["sell_notional"]/p["sell_qty"]
            complete.append(positions.pop(s))
    for p in positions.values():
        p["entry"] = p["basis"]/p["qty"]
        p["stop_price"] = p["stop_basis"]/p["qty"]
    return positions,complete,realized

def excursion(conn,p,end):
    events = [(r["t"],0,r) for r in p["executions"]]
    events.extend((r["t"],1,dict(r)) for r in conn.execute(
        "SELECT * FROM quotes WHERE symbol=? AND t BETWEEN ? AND ? ORDER BY t",
        (p["symbol"],p["t"],end)))
    qty,basis,high,low = 0,0,None,None
    last = None
    for _,kind,row in sorted(events,key=lambda e:e[:2]):
        if kind and qty>EPS:
            last = row["price"]*qty-basis
            high,low = (last if high is None else max(high,last),last if low is None else min(low,last))
        elif not kind and row["side"]=="buy":
            qty += row["qty"]
            basis += row["qty"]*row["price"]
        elif not kind and qty>EPS:
            basis *= max(0,1-row["qty"]/qty)
            qty -= row["qty"]
    return dict(peak=high,trough=low,drawdown=high-last if high is not None else None)

def stats(trades):
    pnl = [t["net"] for t in trades]
    gains = [v for v in pnl if v>0]
    losses = [v for v in pnl if v<0]
    n = len(trades)
    cumulative = peak = drawdown = 0
    wins = losing = longest_win = longest_loss = 0
    for value in pnl:
        cumulative += value
        peak = max(peak,cumulative)
        drawdown = max(drawdown,peak-cumulative)
        wins = wins+1 if value>0 else 0
        losing = losing+1 if value<0 else 0
        longest_win = max(longest_win,wins)
        longest_loss = max(longest_loss,losing)
    return dict(count=n,net=sum(pnl),win_rate=len(gains)*100/n if n else None,
                avg_win=sum(gains)/len(gains) if gains else None,
                avg_loss=sum(losses)/len(losses) if losses else None,
                best=max(gains,default=None),worst=min(losses,default=None),
                profit_factor=sum(gains)/-sum(losses) if losses else None,
                expectancy=sum(pnl)/n if n else None,drawdown=drawdown,consecutive_wins=wins,
                consecutive_losses=losing,longest_win=longest_win,longest_loss=longest_loss,
                compliance=sum(t["compliant"] for t in trades)*100/n if n else None,
                avg_duration=sum(t["holding_seconds"] for t in trades)/n if n else None,
                avg_risk=sum(t["risk"] for t in trades)/n if n else None,
                avg_r=sum(t["net"]/t["risk"] for t in trades)/n if n else None)

def performance(conn,complete,realized,positions,prices):
    now = datetime.now(ET)
    risk = risk_settings(conn)
    stage = get_setting(conn,"stage",1)
    flows = [dict(x) for x in conn.execute("SELECT * FROM flows ORDER BY t,id")]
    cumulative, equity, daily, monthly = [],[],defaultdict(float),defaultdict(float)
    running, balance = 0,risk["starting_balance"]
    deposits = sum(x["amount"] for x in flows if x["amount"]>0)
    withdrawals = -sum(x["amount"] for x in flows if x["amount"]<0)
    for t,net,flow in sorted([(x["t"],x["net"],0) for x in realized]+
                             [(x["t"],0,x["amount"]) for x in flows]):
        balance += net+flow
        running += net
        equity.append([t,balance])
        cumulative.append([t,running])
        day = datetime.fromtimestamp(t,ET).date().isoformat()
        if net:
            daily[day] += net
            monthly[day[:7]] += net
    groups = {}
    for field in ("symbol","stage","version","compliant"):
        keys = {str(t[field]) for t in complete}
        groups[field] = {k:stats([t for t in complete if str(t[field])==k]) for k in sorted(keys)}
    stage_stats = stats([p for p in complete if p["stage"]==stage])
    sizes = [risk[f"stage_{i}"] for i in range(1,len(SIZES)+1)]
    next_size = sizes[stage] if stage<len(sizes) else None
    criteria = dict(sample=stage_stats["count"]>=30, net=stage_stats["net"]>0,
                    expectancy=(stage_stats["expectancy"] or 0)>0,
                    profit_factor=(stage_stats["profit_factor"] or 0)>=risk["minimum_profit_factor"]
                    or (stage_stats["avg_win"] is not None and stage_stats["avg_loss"] is None),
                    drawdown=stage_stats["drawdown"]<=risk["drawdown"],
                    compliance=(stage_stats["compliance"] or 0)>=risk["minimum_compliance"],
                    capital=next_size is not None and balance-sum(p["basis"] for p in positions.values())>=next_size)
    open_values = [(prices.get(s) or {}).get("price") for s in positions]
    unrealized = sum(price*p["qty"]-p["basis"] for price,p in zip(open_values,positions.values())) if all(v is not None for v in open_values) else None
    today = now.date()
    monday = today-timedelta(days=today.weekday())
    first_t = min((p["t"] for p in complete+list(positions.values())),default=int(now.timestamp()))
    first_t = min(first_t,min((f["t"] for f in flows),default=first_t))
    end_t = int(now.timestamp())
    weighted_capital = risk["starting_balance"]+sum(
        f["amount"]*max(0,end_t-f["t"])/max(1,end_t-first_t) for f in flows)
    return dict(**stats(complete),starting_balance=risk["starting_balance"],balance=balance,flows=flows,
                deposits=deposits,withdrawals=withdrawals,realized=running,unrealized=unrealized,
                daily_net=daily[today.isoformat()],weekly_net=sum(v for k,v in daily.items() if k>=monday.isoformat()),
                monthly_net=monthly[today.isoformat()[:7]],daily=dict(daily),monthly=dict(monthly),
                return_pct=running/weighted_capital*100 if weighted_capital>0 else None,
                equity=equity,cumulative=cumulative,groups=groups,stage=stage,stage_size=sizes[stage-1],stage_sizes=sizes,
                next_size=next_size,stage_count=stage_stats["count"],criteria=criteria)
