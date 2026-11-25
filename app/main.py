import asyncio
import base64
import binascii
import csv
import hashlib
import hmac
import io
import json
import logging
import math
import os
import secrets
import sqlite3
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from pydantic import BaseModel, Field

from . import db, ledger, rules
from .market import Feed, KEY, SECRET, SYMBOLS, aggregate, session

log = logging.getLogger(__name__)
feed = Feed()
password = os.getenv("DASHBOARD_PASSWORD", "")
username = os.getenv("DASHBOARD_USER", "trader")
cookie_name = "dashboard_session"
token = hmac.new(password.encode(), secrets.token_bytes(32), hashlib.sha256).hexdigest()
dev = os.getenv("DASHBOARD_DEV")=="1"

@asynccontextmanager
async def lifespan(_):
    if not dev and len(password)<12:
        raise RuntimeError("Set DASHBOARD_PASSWORD to at least 12 characters")
    db.initialize()
    with db.database(True) as conn:
        if not rules.version(conn)[0]:
            rules.save(conn,rules.DEFAULT)
    tasks = [asyncio.create_task(feed.run(rules.evaluate)),asyncio.create_task(feed.repair(rules.evaluate))] if KEY and SECRET else []
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

app = FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

@app.middleware("http")
async def access(request: Request, call_next):
    if request.url.path=="/healthz":
        return await call_next(request)
    valid_cookie = secrets.compare_digest(request.cookies.get(cookie_name,"").encode(),token.encode())
    if not (dev or valid_cookie):
        expected = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        if not secrets.compare_digest(request.headers.get("authorization","").encode(),expected.encode()):
            return Response("Authentication required",401,{"WWW-Authenticate":'Basic realm="Trading dashboard"'})
    if request.method not in ("GET","HEAD","OPTIONS"):
        if not request.headers.get("content-type","").startswith("application/json"):
            return Response("JSON required",415)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if not (valid_cookie or dev):
        response.set_cookie(cookie_name,token,httponly=True,samesite="strict",
                            secure=request.url.scheme=="https")
    return response

@app.get("/healthz")
def health():
    with db.database() as conn:
        conn.execute("SELECT 1").fetchone()
    return {"status":"ok"}

def unpack_levels(rows):
    return [dict(x) | {"evidence":json.loads(x["evidence"])} for x in rows]

def level_summary(bars, trail, price, cfg):
    if not trail:
        return {}
    current = trail[-1]
    tol = cfg["tolerance_pct"]
    result = {}
    for field,name in (("s","support"),("r","resistance")):
        level = current[field]
        slopes = {n:rules.level_change(trail,field,n) for n in (5,10,15)}
        slope = slopes[5]
        duration = 0
        for item in reversed(trail):
            if not level or abs((item[field] or 0)/level-1)*100>tol:
                break
            duration = (current["t"]-item["t"])//60
        samples = bars[-15:]
        tests = sum(abs(x["l" if field=="s" else "h"]/level-1)*100<=tol
                    and (x["c"]>=level if field=="s" else x["c"]<=level)
                    and (field=="s" or rules.wick(x,2)) for x in samples) if level else None
        previous = {x["t"]:x[field] for x in trail}
        crossings = 0
        for a,b in zip(samples,samples[1:]):
            prior = previous.get(a["t"])
            if prior and b["t"]-a["t"]==60:
                crossings += (a["c"]>=prior>b["c"]) if field=="s" else (a["c"]<=prior<b["c"])
        result[name] = dict(price=level,distance=price-level if price and level else None,
                            percent=rules.pct(price,level),slopes=slopes,duration=duration,tests=tests,
                            crossings=crossings,trend=None if slope is None else
                            "Flat" if abs(slope)<=tol else "Rising" if slope>0 else "Falling")
    return result

def snapshot(conn,now,with_charts=True):
    diagnostics = feed.diagnostics(conn,now)
    positions, complete, realized = ledger.summarize(conn)
    perf = ledger.performance(conn,complete,realized,positions,feed.prices)
    ident,cfg = rules.version(conn)
    risk = ledger.risk_settings(conn)
    risk_flags = [label for flag,label in (
        (perf["daily_net"]<=-risk["daily_loss"],"Daily loss limit"),
        (perf["weekly_net"]<=-risk["weekly_loss"],"Weekly loss limit"),
        (perf["drawdown"]>=risk["drawdown"],"Account drawdown limit"),
        (perf["consecutive_losses"]>=risk["consecutive_losses"],"Consecutive loss limit"),
    ) if flag]
    hours = diagnostics["session"]
    close = hours["end"]
    near_close = close and 0<=close-now<=risk["close_buffer"]*60
    available = perf["balance"] - sum(p["basis"]+p["fee_basis"] for p in positions.values())
    if available <= 0:
        risk_flags.append("No available recorded capital")
    signals, charts, levels = {}, {}, {}
    for s in SYMBOLS:
        last_t = conn.execute("SELECT MAX(t) FROM bars WHERE symbol=?",(s,)).fetchone()[0]
        start = session(last_t)["start"] if last_t is not None else now
        bars = [dict(x) for x in conn.execute("SELECT * FROM bars WHERE symbol=? AND t>=? ORDER BY t",(s,start))]
        trail = unpack_levels(conn.execute("SELECT * FROM levels WHERE symbol=? AND t>=? ORDER BY t",(s,start)))
        diag = diagnostics["symbols"][s]
        levels[s] = level_summary(bars,trail,diag.get("price"),cfg)
        if with_charts:
            charts[s] = dict(minute=bars,five=aggregate(bars),trail=trail[-cfg["trail_minutes"]:])
        if s=="SPY":
            continue
        latest = conn.execute("SELECT payload FROM snapshots WHERE symbol=? ORDER BY t DESC LIMIT 1",(s,)).fetchone()
        signal = json.loads(latest[0]) if latest else dict(rules=[],t=None,candle=None,version=ident,symbol=s)
        expected = now//60*60-60
        required = {"SPY",s} | {v["symbol"] for v in cfg["rules"].values() if v["enabled"] and v["mandatory"]}
        fresh = (all(diagnostics["symbols"][symbol]["fresh"] for symbol in required) and signal.get("candle") is not None
                 and expected-60<=signal["candle"]<=expected and signal["version"]==ident
                 and all(not any(t>=expected-240 for t in diagnostics["symbols"][symbol]["missing"])
                         for symbol in required))
        quality = "fresh" if fresh else "stale or incomplete"
        if not signal["rules"]:
            quality = "unavailable"
        signal.update(rules.classify(signal["rules"],quality,s in positions),
                      quality=quality,live_price=diag.get("price"),risk_flags=risk_flags)
        if s in positions:
            p = positions[s]
            price = diag.get("price")
            p.update(ledger.excursion(conn,p,now),price=price,
                     value=price*p["qty"] if price is not None else None,
                     unrealized=price*p["qty"]-p["basis"] if price is not None else None,
                     return_pct=rules.pct(price,p["entry"]),holding_seconds=max(0,now-p["t"]),
                     stop_distance=price-p["stop_price"] if price is not None else None)
            warnings = [rules.META[k]["description"] for k in signal["conflicting"]]
            if price is not None and diag["fresh"] and price<=p["stop_price"]:
                warnings.insert(0,"Stop-loss threshold crossed")
                signal.update(label="Potential exit",explanation="Fresh IEX price crossed the recorded stop.")
            elif price is not None and diag["fresh"] and price<=p["stop_price"]*1.01:
                warnings.insert(0,"Within 1% of recorded stop")
            if not diag["fresh"]:
                warnings.append("Stale or unavailable instrument price")
            if p["basis"] > min(risk["max_size"],perf["stage_size"]):
                warnings.append("Recorded position exceeds configured size")
            if p["basis"]-p["stop_basis"] > risk["trade_risk"]:
                warnings.append("Recorded planned risk exceeds configured limit")
            if hours["start"] and p["t"]<hours["start"] and now>=hours["start"]:
                warnings.append("Recorded position carried overnight")
            if hours["end"] and now>=hours["end"]:
                warnings.append("Regular session ended with this position recorded open")
            p["warnings"] = warnings + (["Approaching market close"] if near_close else [])
        if signal["label"]=="Potential entry" and (risk_flags or near_close):
            signal.update(label="No opportunity",explanation="Entry suppressed by risk limits or approaching close.")
        signals[s] = signal
    return dict(now=now,diagnostics=diagnostics,signals=signals,charts=charts,levels=levels,
                positions=positions,risk=risk,stage=perf["stage"],stage_size=perf["stage_size"],available_capital=max(0,available),
                balance=perf["balance"],executions=ledger.execution_rows(conn) if with_charts else [])

@app.get("/api/live")
def live():
    with db.database() as conn:
        return snapshot(conn,int(time.time()))

@app.websocket("/ws")
async def websocket(ws: WebSocket):
    same_origin = ws.headers.get("origin","").split("://")[-1] == ws.headers.get("host")
    if not same_origin or not (dev or secrets.compare_digest(
            ws.cookies.get(cookie_name,"").encode(),token.encode())):
        await ws.close(code=1008)
        return
    await ws.accept()
    previous = None
    try:
        while True:
            with db.database() as conn:
                revision = (feed.revision,conn.execute("SELECT COUNT(*), MAX(rowid) FROM executions").fetchone()[:])
                data = snapshot(conn,int(time.time()),revision!=previous)
            await ws.send_json(data)
            previous = revision
            try:
                if (await asyncio.wait_for(ws.receive(),timeout=1))["type"]=="websocket.disconnect":
                    return
            except TimeoutError:
                pass
    except (WebSocketDisconnect, RuntimeError, OSError):
        return

def invalid(exc):
    return HTTPException(422,str(exc))

@app.post("/api/executions")
def execute(trade: ledger.Execution):
    with db.database(True) as conn:
        try:
            ledger.record(conn,trade)
        except ValueError as exc:
            raise invalid(exc) from exc
    feed.revision += 1
    return {"id":trade.id}

@app.put("/api/executions/{ident}")
def edit(ident: str, trade: ledger.Execution):
    if ident!=trade.id:
        raise HTTPException(422,"Execution ID mismatch")
    with db.database(True) as conn:
        try:
            ledger.record(conn,trade,True)
        except ValueError as exc:
            raise invalid(exc) from exc
    feed.revision += 1
    return {"id":ident}

class Screenshot(BaseModel):
    image: str = Field(max_length=2_800_000)

@app.put("/api/screenshots/{ident}")
def save_screenshot(ident: str, body: Screenshot):
    try:
        data = base64.b64decode(body.image,validate=True)
    except (ValueError,binascii.Error) as exc:
        raise HTTPException(422,"Invalid image encoding") from exc
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith(b"RIFF") and data[8:12]==b"WEBP":
        mime = "image/webp"
    else:
        raise HTTPException(422,"Choose a PNG, JPEG or WebP screenshot")
    if len(data)>2*1024*1024:
        raise HTTPException(422,"Maximum screenshot size is 2 MiB")
    with db.database(True) as conn:
        if not conn.execute("SELECT 1 FROM executions WHERE id=?",(ident,)).fetchone():
            raise HTTPException(404,"Execution not found")
        conn.execute("INSERT OR REPLACE INTO screenshots VALUES(?,?,?)",(ident,mime,data))
    return {"ok":True}

@app.get("/api/screenshots/{ident}")
def screenshot(ident: str):
    with db.database() as conn:
        row = conn.execute("SELECT mime,image FROM screenshots WHERE execution_id=?",(ident,)).fetchone()
    if not row:
        raise HTTPException(404,"Screenshot not recorded")
    return Response(row["image"],media_type=row["mime"])

@app.get("/api/journal")
def journal():
    with db.database() as conn:
        opened,closed,_ = ledger.summarize(conn)
        return {"open":list(opened.values()),"closed":list(reversed(closed)),
                "screenshots":[r[0] for r in conn.execute("SELECT execution_id FROM screenshots")]}

@app.get("/api/review/{ident}")
def review(ident: str):
    with db.database() as conn:
        _,closed,_ = ledger.summarize(conn)
        trade = next((x for x in closed if x["id"]==ident),None)
        if not trade:
            raise HTTPException(404,"Completed trade not found")
        s,t,end = trade["symbol"],trade["t"],trade["exit_t"]
        bounds = (s,t-3600,end+60)
        bars = [dict(x) for x in conn.execute("SELECT * FROM bars WHERE symbol=? AND t BETWEEN ? AND ? ORDER BY t",bounds)]
        trail = unpack_levels(conn.execute("SELECT * FROM levels WHERE symbol=? AND t BETWEEN ? AND ? ORDER BY t",bounds))
        signals = [json.loads(x[0]) for x in conn.execute(
            "SELECT payload FROM snapshots WHERE symbol=? AND observed BETWEEN ? AND ? ORDER BY t",(s,t,end))]
        return {"trade":trade,"excursion":ledger.excursion(conn,trade,end),"signals":signals,
                "chart":{"minute":bars,"five":aggregate(bars),"trail":trail}}

@app.get("/api/performance")
def statistics():
    with db.database() as conn:
        positions,complete,realized = ledger.summarize(conn)
        return ledger.performance(conn,complete,realized,positions,feed.prices) | {"trades":complete}

class Flow(BaseModel):
    amount: float = Field(ge=-1e7,le=1e7)
    note: str = Field("",max_length=1000)

@app.post("/api/flows")
def flow(body: Flow):
    if not body.amount:
        raise HTTPException(422,"Enter a nonzero cash flow")
    with db.database(True) as conn:
        conn.execute("INSERT INTO flows(t,amount,note) VALUES(?,?,?)",(int(time.time()),body.amount,body.note))
    return {"ok":True}

@app.post("/api/stage")
def advance():
    with db.database(True) as conn:
        positions,complete,realized = ledger.summarize(conn)
        current = ledger.performance(conn,complete,realized,positions,feed.prices)
        if not current["next_size"] or not all(current["criteria"].values()):
            raise HTTPException(422,"Advancement criteria not satisfied")
        ledger.set_setting(conn,"stage",current["stage"]+1)
    return {"ok":True}

@app.get("/api/rules")
def configuration():
    with db.database() as conn:
        ident,config = rules.version(conn)
        return dict(id=ident,config=config,metadata=rules.META,yaml=yaml.safe_dump(config),
                    versions=[dict(x) for x in conn.execute("SELECT id,t FROM versions ORDER BY id DESC")])

class RuleChange(BaseModel):
    yaml_text: str | None = Field(None,max_length=60000)
    config: dict | None = None
    rollback: int | None = None
    defaults: bool = False

@app.put("/api/rules")
def change_rules(body: RuleChange):
    with db.database(True) as conn:
        try:
            if body.defaults:
                cfg = rules.DEFAULT
            elif body.rollback is not None:
                row = conn.execute("SELECT yaml FROM versions WHERE id=?",(body.rollback,)).fetchone()
                if not row:
                    raise ValueError("Configuration version not found")
                cfg = yaml.safe_load(row[0])
            elif body.yaml_text is not None:
                cfg = yaml.safe_load(body.yaml_text)
            else:
                cfg = body.config
            return {"id":rules.save(conn,cfg)}
        except (ValueError,yaml.YAMLError,RecursionError) as exc:
            raise invalid(exc) from exc

@app.put("/api/risk")
def risk_configuration(body: dict[str,float]):
    if set(body)!=set(ledger.DEFAULT_RISK) or any(not math.isfinite(x) or x<=0 or x>1e8 for x in body.values()):
        raise HTTPException(422,"All risk settings must be positive finite values")
    if (body["minimum_compliance"]>100 or body["close_buffer"]>120 or body["slippage_pct"]>=100
        or not body["consecutive_losses"].is_integer()
        or any(body[f"stage_{i}"]>=body[f"stage_{i+1}"] for i in range(1,len(ledger.SIZES)))):
        raise HTTPException(422,"Invalid compliance, close buffer, slippage, streak limit or stage sizes")
    with db.database(True) as conn:
        ledger.set_setting(conn,"risk",body)
    return {"ok":True}

@app.get("/api/export/{kind}")
def export(kind: str):
    fields = ("id","symbol","t","side","price","qty","fee","stop","target","stage","version","notes","checklist","context")
    with db.database() as conn:
        values = [[json.dumps(x[k]) if isinstance(x[k],dict) else x[k] for k in fields]
                  for x in ledger.execution_rows(conn)]
    if kind=="csv":
        buffer = io.StringIO()
        csv.writer(buffer).writerows([fields,*values])
        return Response(buffer.getvalue(),media_type="text/csv",
                        headers={"Content-Disposition":'attachment; filename="executions.csv"'})
    if kind=="xlsx":
        book = Workbook()
        sheet = book.active
        sheet.title = "Executions"
        sheet.append(fields)
        for row in values:
            sheet.append(row)
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value,str):
                    cell.data_type = "s"
        binary = io.BytesIO()
        book.save(binary)
        return Response(binary.getvalue(),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition":'attachment; filename="executions.xlsx"'})
    raise HTTPException(404,"Export format unavailable")

@app.post("/api/import/webull")
def webull_import():
    raise HTTPException(409,"Webull import is disabled pending validation against an actual Webull CSV export.")

@app.get("/api/backup")
def backup():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/"backup.sqlite3"
        with db.database() as source:
            target = sqlite3.connect(path)
            try:
                source.backup(target)
            finally:
                target.close()
        return Response(path.read_bytes(),media_type="application/octet-stream",
                        headers={"Content-Disposition":'attachment; filename="trading-backup.sqlite3"'})

dist = Path(os.getenv("FRONTEND_DIST","frontend/dist"))
if dist.is_dir():
    app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")

@app.get("/")
def home():
    if not dist.is_dir():
        return Response("Build the frontend first",503)
    return FileResponse(dist/"index.html")
