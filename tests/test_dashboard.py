import asyncio
import json
from datetime import datetime
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import db, ledger, market, restore, rules
from app import main

def time(value):
    return int(datetime.fromisoformat(value).timestamp())

START = time("2026-11-24T09:30:00-05:00")
def bars(closes,start=START):
    return [dict(t=start+60*i,o=c-.1,h=c+.4,l=c-.4,c=c,v=100) for i,c in enumerate(closes)]

@pytest.fixture(autouse=True)
def storage(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"ROOT",tmp_path)
    monkeypatch.setattr(db,"DB",tmp_path/"test.sqlite3")
    monkeypatch.setattr(restore,"ROOT",tmp_path)
    monkeypatch.setattr(restore,"DB",db.DB)
    db.initialize()

def test_aggregation_requires_contiguous_complete_minutes():
    rows=bars([10,12,11,13,14,15,16,17,18,19])
    five=market.aggregate(rows)
    assert len(five)==2 and five[0]==dict(t=START,o=9.9,h=14.4,l=9.6,c=14,v=500)
    assert market.aggregate(rows[:2]+rows[3:])==five[1:]

def test_exchange_sessions_include_holidays_early_close_and_dst():
    assert market.session(time("2026-11-26T12:00:00-05:00"))["start"] is None
    assert market.session(time("2026-11-27T12:00:00-05:00"))["end"]==time("2026-11-27T13:00:00-05:00")
    assert market.session(time("2026-07-01T14:00:00+00:00"))["start"]==time("2026-07-01T09:30:00-04:00")

def test_indicators_pivots_and_doji_wicks():
    assert rules.ema([10,11,12],3)==[10,10.5,11.25]
    sequence=bars([10,11,14,12,11,13,16,13,12])
    assert [r["c"] for r in rules.swings(sequence,"h")]==[14,16]
    assert [r["c"] for r in rules.swings(sequence[:4],"h")]==[]
    assert rules.levels(sequence,20)[:2]==(10.6,16.4)
    assert rules.wick({"o":100,"c":100,"h":100.1},2)
    assert not rules.wick({"o":100,"c":100,"h":100},2)

def test_feed_missing_duplicate_and_level_integrity(monkeypatch):
    feed=market.Feed()
    now=START+8*60+1
    monkeypatch.setattr(market.time,"time",lambda:now)
    messages=[dict(r,S="SPY",T="b",t=market.iso(r["t"])) for r in bars([10,11,14,12,11,13,16,13])]
    feed.write(messages[:5],rules.evaluate)
    with db.database() as conn:
        initial=[dict(r) for r in conn.execute("SELECT * FROM levels ORDER BY t")]
    feed.write(messages[:5]+messages[6:],rules.evaluate)
    with db.database() as conn:
        assert [dict(r) for r in conn.execute("SELECT * FROM levels ORDER BY t LIMIT 5")]==initial
        diagnostics=feed.diagnostics(conn,now)
        assert diagnostics["symbols"]["SPY"]["missing"]==[START+5*60]
        assert diagnostics["symbols"]["SPY"]["candles"]==7
    feed.write(messages[5:6],rules.evaluate)
    with db.database() as conn:
        assert conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]==8
        assert conn.execute("SELECT r FROM levels WHERE t=?",(START+4*60,)).fetchone()[0]==14.4

def test_stale_and_trade_rate(monkeypatch):
    feed=market.Feed()
    feed.status="Connected"
    monkeypatch.setattr(market.time,"time",lambda:START+5)
    feed.write([dict(S="SPYU",T="t",t=market.iso(START),p=55)],rules.evaluate)
    with db.database() as conn:
        assert feed.diagnostics(conn,START+5)["symbols"]["SPYU"]["fresh"]
        assert not feed.diagnostics(conn,START+31)["symbols"]["SPYU"]["fresh"]
        assert not feed.diagnostics(conn,START+65)["symbols"]["SPYU"]["updates_per_minute"]

def test_websocket_retries(monkeypatch):
    monkeypatch.setattr(market,"KEY","test-key")
    monkeypatch.setattr(market,"SECRET","test-secret")
    feed=market.Feed()
    class Broken:
        async def __aenter__(self):
            raise ConnectionError()
        async def __aexit__(self,*args):
            pass
    monkeypatch.setattr(market.websockets,"connect",lambda *args,**kwargs:Broken())
    pause=AsyncMock(side_effect=[None,asyncio.CancelledError()])
    monkeypatch.setattr(market.asyncio,"sleep",pause)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(feed.run(rules.evaluate))
    assert [x.args[0] for x in pause.await_args_list]==[1,2]
    assert feed.reconnects==2

def result(key,group,state="pass",mandatory=False):
    return dict(key=key,group=group,state=state,mandatory=mandatory,weight=1,
                contribution=(1 if group=="Entry" else -1) if state=="pass" else 0)

def test_rules_thresholds_scores_and_conflicts():
    config=rules.DEFAULT["rules"]["sustained"]
    assert rules.measure("sustained",bars([12,10,11,12,13]),[],config,.05,None)==3
    inputs=[result("ema9","Entry",mandatory=True)]
    assert rules.classify(inputs,"fresh")["label"]=="Potential entry"
    assert rules.classify(inputs,"stale")["label"]=="Insufficient data"
    assert rules.classify([result("ema9","Entry","insufficient",True)],"fresh")["label"]=="Insufficient data"
    inputs.append(result("break_support","Exit"))
    signal=rules.classify(inputs,"fresh",True)
    assert signal["label"]=="Potential exit" and signal["score"]==50
    assert signal["supporting"]==["ema9"] and signal["conflicting"]==["break_support"]

def test_config_versions_and_invalid_rollback():
    cfg=deepcopy(rules.DEFAULT)
    with db.database(True) as conn:
        assert rules.save(conn,cfg)==1
        cfg["trail_minutes"]=80
        assert rules.save(conn,cfg)==2
        assert rules.version(conn)[1]["trail_minutes"]==80
    with pytest.raises(ValueError),db.database(True) as conn:
        cfg["rules"]["ema9"]["symbol"]="BAD"
        rules.save(conn,cfg)
    with db.database() as conn:
        assert rules.version(conn)[0]==2

def trade(ident,side,offset,price,qty,fee=0):
    return ledger.Execution(id=ident,symbol="SPYU",side=side,t=START+offset,price=price,qty=qty,fee=fee,
                            checklist={"confirmed":True})

def test_partial_fills_exits_fees_and_database_persistence():
    purchases=[trade("purchase-1","buy",1,10,10,1),trade("purchase-2","buy",2,20,10,1)]
    with db.database(True) as conn:
        for fill in purchases:
            ledger.record(conn,fill)
        ledger.record(conn,purchases[0])
        ledger.record(conn,trade("partial-1","sell",3,18,5,1))
    with db.database() as conn:
        opened,completed,realized=ledger.summarize(conn)
        assert opened["SPYU"]["qty"]==15
        assert opened["SPYU"]["entry"]==15
        assert realized[0]["net"]==13.5 and not completed
    with db.database(True) as conn:
        ledger.record(conn,trade("finalsale","sell",4,18,15,1))
    db.initialize()
    with db.database() as conn:
        opened,completed,realized=ledger.summarize(conn)
        assert not opened
        assert completed[0]["net"]==56
        assert ledger.stats(completed)["expectancy"]==56
        assert ledger.stats(completed)["profit_factor"] is None

def test_duplicate_and_oversell_roll_back():
    with db.database(True) as conn:
        ledger.record(conn,trade("purchase-1","buy",1,10,10))
    with pytest.raises(ValueError),db.database(True) as conn:
        ledger.record(conn,trade("purchase-2","buy",1,10,10))
    with pytest.raises(ValueError),db.database(True) as conn:
        ledger.record(conn,trade("over-sale","sell",4,18,11))
    with db.database() as conn:
        assert len(ledger.execution_rows(conn))==1

def test_context_uses_only_observed_information():
    with db.database(True) as conn:
        conn.execute("INSERT INTO quotes VALUES(?,?,?,?)",("SPYU",START+1,START+3,10))
        conn.execute("INSERT INTO snapshots VALUES(?,?,?,?)",("SPYU",START,START+4,json.dumps({"t":START+4,"score":72})))
        assert not ledger.context_at(conn,"SPYU",START+2)["available"]
        assert ledger.context_at(conn,"SPYU",START+2)["trade_prices"]["SPYU"] is None
        assert ledger.context_at(conn,"SPYU",START+4)["signal"]["score"]==72

def test_empty_performance_and_progression():
    with db.database(True) as conn:
        perf=ledger.performance(conn,[],[],{},{})
        assert perf["balance"]==2000 and perf["expectancy"] is None
        assert not perf["criteria"]["sample"]
        trades=[dict(t=START,symbol="SPYU",stage=1,version=1,net=10,risk=25,holding_seconds=60,compliant=True)]*30
        perf=ledger.performance(conn,trades,[],{},{})
        assert all(perf["criteria"].values())
        assert perf["stage"]==1

def test_api_record_review_annotations_exports_and_import_gate(monkeypatch):
    monkeypatch.setattr(main,"dev",True)
    with TestClient(main.app) as client:
        assert client.get("/healthz").status_code==200
        assert client.post("/api/import/webull",json={}).status_code==409
        first=trade("purchase-1","buy",5,10,10)
        last=trade("finalsale","sell",7,11,10)
        assert client.post("/api/executions",json=first.model_dump()).status_code==200
        assert client.post("/api/executions",json=last.model_dump()).status_code==200
        review=client.get("/api/review/"+first.id).json()
        assert [(x["t"],x["price"]) for x in review["trade"]["executions"]]==[(first.t,10),(last.t,11)]
        assert client.get("/api/export/csv").text.count(first.id)==1
        assert client.get("/api/export/xlsx").content.startswith(b"PK")
        assert client.post("/api/stage",json={}).status_code==422
        assert client.put("/api/risk",json=ledger.DEFAULT_RISK).status_code==200
        with client.websocket_connect("/ws",headers={"origin":"http://testserver"}) as ws:
            assert ws.receive_json()["diagnostics"]["symbols"]["SPYU"]["fresh"] is False
        backup=db.ROOT/"download.sqlite3"
        backup.write_bytes(client.get("/api/backup").content)
    restore.restore(backup)
    with db.database() as conn:
        assert len(ledger.execution_rows(conn))==2

def test_open_session_signal_and_price_based_stop(monkeypatch):
    feed = market.Feed()
    feed.status = "Connected"
    monkeypatch.setattr(main,"feed",feed)
    now = START+23*60+2
    monkeypatch.setattr(market.time,"time",lambda:now)
    cfg = deepcopy(rules.DEFAULT)
    for key,rule in cfg["rules"].items():
        rule["enabled"] = key=="ema9"
    with db.database(True) as conn:
        rules.save(conn,cfg)
    for symbol in ("SPY","SPYU"):
        feed.write([dict(S=symbol,T="t",t=market.iso(now),p=100)],rules.evaluate)
    messages = [
        dict(row,T="b",S=symbol,t=market.iso(row["t"]))
        for symbol in ("SPY","SPYU") for row in bars(list(range(80,103)))
    ]
    feed.write(messages,rules.evaluate)
    with db.database(True) as conn:
        assert main.snapshot(conn,now)["signals"]["SPYU"]["label"]=="Potential entry"
        ledger.record(conn,trade("live-buy","buy",23*60+2,110,1))
    with db.database() as conn:
        current = main.snapshot(conn,now)
        assert current["positions"]["SPYU"]["warnings"][0]=="Stop-loss threshold crossed"
        assert current["signals"]["SPYU"]["label"]=="Potential exit"

def test_private_auth_and_screenshot(monkeypatch):
    monkeypatch.setattr(main,"dev",False)
    monkeypatch.setattr(main,"password","test-only-password")
    with TestClient(main.app) as client:
        assert client.get("/api/live").status_code==401
        assert client.get("/api/live",auth=(main.username,"test-only-password")).status_code==200
        trade_id="purchase-1"
        assert client.post("/api/executions",json=trade(trade_id,"buy",5,10,10).model_dump()).status_code==200
        assert client.put("/api/screenshots/"+trade_id,json={"image":"aW52YWxpZA=="}).status_code==422
        from base64 import b64encode
        image=b"\x89PNG\r\n\x1a\n"
        assert client.put("/api/screenshots/"+trade_id,json={"image":b64encode(image).decode()}).status_code==200
        assert client.get("/api/screenshots/"+trade_id).content==image
        with client.websocket_connect("/ws",headers={"origin":"http://testserver"}) as ws:
            assert "SPYU" in ws.receive_json()["diagnostics"]["symbols"]
