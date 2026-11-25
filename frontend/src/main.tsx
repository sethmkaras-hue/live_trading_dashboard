import {useEffect,useMemo,useRef,useState,type FormEvent,type ReactNode} from 'react'
import {createRoot} from 'react-dom/client'
import {Chart,type Sync} from './Chart'
import {api,clock,date,duration,localDate,money,num,pct,title,type Execution,type Live,type Overlay,type Position,type Rule,type Signal} from './types'
import {Journal,Performance,Rules,Settings} from './pages'
import './style.css'

const pages=['Live Trading','Trading Journal','Performance','Trading Rules','Settings'] as const
type Page=typeof pages[number]
const overlayNames:Record<Overlay,string>={ema9:'9 EMA',ema20:'20 EMA',support:'Support',resistance:'Resistance',supportTrail:'Support trail',resistanceTrail:'Resistance trail',executions:'Executions',position:'Position lines'}
const defaultOverlays=new Set(Object.keys(overlayNames) as Overlay[])
const initialOverlays=()=>{
  try{
    const saved=localStorage.getItem('trading-overlays')
    return saved?new Set<Overlay>(JSON.parse(saved)):defaultOverlays
  }catch{return defaultOverlays}
}
const checks={
  upward:'SPY is demonstrating upward momentum',swings:'Recent swing highs and lows support the trade',
  resistance:'I evaluated nearby resistance',mandatory:'Mandatory entry conditions are satisfied',
  exit:'I defined my exit conditions',stop:'A stop-loss is established',size:'Position size and planned risk are within limits',
  fresh:'Required market data is fresh',fomo:'I am not entering solely because of fear of missing out'
}
const manual=new Set(['resistance','exit','fomo'])

export function Section({title:heading,sub,action,children,className=''}:{title:string,sub?:string,action?:ReactNode,children:ReactNode,className?:string}){
  return <section className={'panel '+className}><header className="section-head"><div><h2>{heading}</h2>{sub&&<p>{sub}</p>}</div>{action}</header>{children}</section>
}
export function Metric({label,value,detail,tone}:{label:string,value:ReactNode,detail?:ReactNode,tone?:string}){
  return <div className="metric"><span>{label}</span><strong className={tone}>{value}</strong>{detail&&<small>{detail}</small>}</div>
}
export function Dialog({title:heading,children,close}:{title:string,children:ReactNode,close:()=>void}){
  const ref=useRef<HTMLDialogElement>(null)
  useEffect(()=>{ref.current?.showModal()},[])
  return <dialog ref={ref} onCancel={e=>{e.preventDefault();close()}}>
    <header className="section-head"><h2>{heading}</h2><button className="icon-button" aria-label="Close" onClick={close}>×</button></header>{children}
  </dialog>
}
export function RuleResults({rules}:{rules:Rule[]}){
  if(!rules.length) return <div className="empty">Rule results appear after usable candles arrive.</div>
  return <div className="table-wrap"><table><thead><tr>{['Rule','Measured','Threshold','Result','Contribution','Last evaluation'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>
    {rules.map(r=><tr key={r.key}><td><strong>{title(r.key)}</strong><small>{r.description}</small></td><td>{num(r.value,5)}</td><td>{r.comparison} {r.threshold}</td>
    <td><span className={'tag '+(r.state==='pass'?'good':r.state==='fail'?'warn':'')}>{title(r.state)}</span></td><td>{r.contribution}</td><td>{date(r.t)}</td></tr>)}
  </tbody></table></div>
}
function inputTime(t:number){
  const d=new Date(t*1000)
  return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,19)
}
function checklist(live:Live|null,symbol:string,size:number,planned:number,stop:number,manualChecks:Record<string,boolean>){
  const signal=live?.signals[symbol]
  const ok=(key:string)=>signal?.rules.some(r=>r.key===key&&r.state==='pass')??false
  const limits=live?.risk
  const combined=size+(live?.positions[symbol]?.invested??0)
  return {...manualChecks,upward:ok('ema9')&&ok('ema20')&&ok('trend5'),swings:ok('higher_high')&&ok('higher_low'),
    mandatory:!!signal&&signal.rules.some(r=>r.mandatory)&&signal.failed.length===0,
    stop:stop>0&&stop<100,size:!!live&&!!limits&&size>0&&combined<=Math.min(live.stage_size,limits.max_size) && size<=live.available_capital&&planned<=limits.trade_risk,fresh:signal?.quality==='fresh'}
}
function Checklist({values,change}:{values:Record<string,boolean>,change:(key:string,value:boolean)=>void}){
  return <div className="checklist">{Object.entries(checks).map(([key,label])=><label key={key}>
    <input type="checkbox" checked={!!values[key]} disabled={!manual.has(key)} onChange={e=>change(key,e.target.checked)}/>
    <span>{label}</span><small>{manual.has(key)?'Manual':'Automatic'}</small>
  </label>)}</div>
}
export function TradeForm({side,symbol,live,initial,manualChecks,setManualChecks,close,done}:{
  side:string,symbol:string,live:Live|null,initial?:Execution,manualChecks:Record<string,boolean>,
  setManualChecks:(value:Record<string,boolean>)=>void,close:()=>void,done:()=>void
}){
  const [ticker,setTicker]=useState(initial?.symbol??symbol)
  const [price,setPrice]=useState(initial?.price.toString()??'')
  const [qty,setQty]=useState(initial?.qty.toString()??'')
  const [stop,setStop]=useState(initial?.stop.toString()??'5')
  const [busy,setBusy]=useState(false)
  const [error,setError]=useState('')
  const [stamp]=useState(inputTime(initial?.t??Math.floor(Date.now()/1000)))
  const [id]=useState(initial?.id??(globalThis.crypto?.randomUUID?.()??Array.from(crypto.getRandomValues(new Uint32Array(4)),n=>n.toString(16)).join('-')))
  const [checked,setChecked]=useState(initial?.checklist??manualChecks)
  const cost=Number(price)*Number(qty),planned=cost*Number(stop)/100
  const recordedChecks:Record<string,boolean>=initial?checked:checklist(live,ticker,cost,planned,Number(stop),checked)
  const update=(key:string,value:boolean)=>{
    setChecked({...checked,[key]:value})
    if(!initial) setManualChecks({...checked,[key]:value})
  }
  const handle=async(event:FormEvent<HTMLFormElement>)=>{
    event.preventDefault()
    if(busy) return
    const data=new FormData(event.currentTarget)
    const t=Math.floor(new Date(String(data.get('t'))).getTime()/1000)
    setBusy(true);setError('')
    try{
      await api('/api/executions'+(initial?'/'+id:''),initial?'PUT':'POST',{
        id,symbol:ticker,side,t,price:Number(price),qty:Number(qty),fee:Number(data.get('fee')),stop:Number(stop),
        target:Number(data.get('target'))||null,notes:String(data.get('notes')??''),emotion:String(data.get('emotion')??''),
        lessons:String(data.get('lessons')??''),checklist:side==='buy'?Object.fromEntries(Object.keys(checks).map(k=>[k,!!recordedChecks[k]])):{}
      })
      done()
    }catch(err){setError(String((err as Error).message));setBusy(false)}
  }
  return <Dialog title={initial?'Edit execution':side==='buy'?'Record Buy':'Record Sell'} close={close}>
    <p className="muted">Enter the actual fill from Webull. This creates a journal record.</p>
    <form onSubmit={handle} className="stack">
      <div className="fields">
        <label>Instrument<select value={ticker} onChange={e=>setTicker(e.target.value)}>
          {[...new Set([ticker,...Object.keys(live?.diagnostics.symbols??{SPYU:true}).filter(s=>s!=='SPY')])].map(s=><option key={s}>{s}</option>)}</select></label>
        <label>Execution time (device time)<input type="datetime-local" name="t" step="1" defaultValue={stamp} required/></label>
        <label>Actual price ($)<input type="number" min=".000001" step="any" value={price} onChange={e=>setPrice(e.target.value)} required autoFocus/></label>
        <label>Shares<input type="number" min=".000001" step="any" value={qty} onChange={e=>setQty(e.target.value)} required/></label>
        <label>Fees ($)<input name="fee" type="number" min="0" step="any" defaultValue={initial?.fee??0}/></label>
        {side==='buy'&&<><label>Stop-loss (%)<input type="number" min=".01" max="99.99" step="any" value={stop} onChange={e=>setStop(e.target.value)} required/></label>
        <label>Optional target ($)<input name="target" type="number" min=".01" step="any" defaultValue={initial?.target??''}/></label></>}
        <label>Emotional state<input name="emotion" maxLength={250} defaultValue={initial?.notes.emotion}/></label>
      </div>
      <label>{side==='buy'?'Entry justification':'Exit justification'}<textarea name="notes" rows={2} maxLength={4000} defaultValue={initial?.notes.notes}/></label>
      <label>Lessons learned<textarea name="lessons" rows={2} maxLength={4000} defaultValue={initial?.notes.lessons}/></label>
      {side==='buy'?<>
        <div className="inline-summary"><span>Position <b>{money(cost)}</b></span><span>Stop <b>{money(Number(price)*(1-Number(stop)/100))}</b></span><span>Planned risk <b>{money(planned)}</b></span></div>
        <details open={!initial}><summary>Pre-trade checklist</summary><Checklist values={recordedChecks} change={update}/></details>
      </>:<div className="inline-summary">Recorded long shares <b>{num(live?.positions[ticker]?.qty??0,6)}</b></div>}
      {error&&<p role="alert" className="error">{error}</p>}
      <div className="actions"><button type="button" onClick={close}>Cancel</button><button className={side==='buy'?'buy':'sell'} disabled={busy}>{busy?'Saving…':'Save execution'}</button></div>
    </form>
  </Dialog>
}
function SignalPanel({signal}:{signal?:Signal}){
  const evidence=(keys:string[])=>keys.map(k=>signal?.rules.find(x=>x.key===k)?.description??title(k))
  const groups=[['Bullish evidence',evidence(signal?.supporting??[]),'good'],['Warnings and conflicting evidence',evidence(signal?.conflicting??[]),'warn'],
    ['Mandatory conditions unmet',evidence(signal?.failed??[]),'warn'],['Risk limits',signal?.risk_flags??[],'bad']] as const
  return <Section title="Signal evidence" sub="SPY momentum · IEX data" className="signal-panel">
    <div className="signal-status"><strong>{signal?.label??'Insufficient data'}</strong><span className="tag">{signal?.quality??'Unavailable'}</span></div>
    <div className="score"><span>Momentum score</span><b>{signal?.rules.length?signal.score+' / 100':'—'}</b></div>
    <progress value={signal?.rules.length?signal.score:0} max="100" aria-label="Momentum score"/>
    <p className="muted">{signal?.explanation??'A signal requires completed candles and fresh prices for SPY and the trading instrument.'}</p>
    {groups.filter(([,items])=>items.length).map(([label,items,color])=><details key={label} open><summary className={color}>{label} ({items.length})</summary><ul>{items.slice(0,8).map(item=><li key={item}>{item}</li>)}</ul></details>)}
    <footer className="signal-footer"><span>Rules {signal?.version?'v'+signal.version:'—'}</span><span>{date(signal?.t)}</span></footer>
  </Section>
}
function PositionPanel({position}:{position?:Position}){
  return <Section title="Active position" sub="Estimated from IEX prices">
    {position?<><div className="metric-grid compact">{[
      ['Entry',money(position.entry)],['Current',money(position.price)],['Shares',num(position.qty,6)],['Value',money(position.value)],
      ['Unrealized P&L',money(position.unrealized)],['Return',pct(position.return_pct)],['Peak observed',money(position.peak)],
      ['Low observed',money(position.trough)],['From peak',money(position.drawdown)],['Holding time',duration(position.holding_seconds)],
      ['Stop price',money(position.stop_price)],['Distance to stop',money(position.stop_distance)]
    ].map(([label,value])=><Metric key={label} label={label} value={value}/>)}</div>
    {position.warnings?.map(w=><div key={w} className="alert-line">{w}</div>)}</>:<div className="empty">No recorded open position in this instrument.</div>}
  </Section>
}
function LevelPanel({levels}:{levels:Live['levels'][string]|undefined}){
  return <Section title="Support and resistance" sub="Confirmed swing levels">
    {!levels||!Object.keys(levels).length?<div className="empty">No confirmed levels yet.</div>:
    <div className="table-wrap"><table><thead><tr>{['Level','Price','From price','Trend','5m / 10m / 15m','At level','Tests','Crossings'].map(x=><th key={x}>{x}</th>)}</tr></thead>
      <tbody>{Object.entries(levels).map(([k,x])=><tr key={k}><td className={k==='support'?'good':'bad'}>{title(k)}</td><td>{money(x.price)}</td><td>{money(x.distance)} ({pct(x.percent)})</td><td>{x.trend??'—'}</td>
      <td>{[5,10,15].map(n=>pct(x.slopes[n])).join(' / ')}</td><td>{x.duration}m</td><td>{num(x.tests)}</td><td>{x.crossings}</td></tr>)}</tbody></table></div>}
  </Section>
}
function LivePage({live,connected,symbol,setSymbol,open,manualChecks,setManualChecks}:{
  live:Live|null,connected:boolean,symbol:string,setSymbol:(s:string)=>void,open:(side:string)=>void,
  manualChecks:Record<string,boolean>,setManualChecks:(value:Record<string,boolean>)=>void
}){
  const [frame,setFrame]=useState<1|5>(1)
  const [overlays,setOverlays]=useState(initialOverlays)
  const [checkOpen,setCheckOpen]=useState(false)
  const sync=useMemo<Sync>(()=>({peers:new Set(),locked:false}),[])
  const diagnostics=live?.diagnostics, signal=live?.signals[symbol], position=live?.positions[symbol]
  const session=diagnostics?.session
  const seconds=live&&session?.open?60-live.now%60:null
  const ready=connected&&diagnostics?.status==='Connected'
  return <>
    <div className="page-heading"><div><h1>Live Trading</h1><p>Monitor SPY and record your Webull executions.</p></div>
      <div className="heading-meta"><span className="tag"><i className={ready?'status-dot':'status-dot idle'}/>{ready?'IEX connected':diagnostics?.status??'Connecting'}</span><span className="tag">{session?.open?'Market open':'Market closed'}</span></div>
    </div>
    <div className="summary">
      {['SPY',symbol].map(s=>{const q=diagnostics?.symbols[s];return <Metric key={s} label={s} value={money(q?.price)} detail={<><span className={(q?.daily_change??0)>0?'good':(q?.daily_change??0)<0?'bad':''}>{pct(q?.daily_change)}</span> <span>· {q?.fresh?'Fresh':q?.price!=null?'Stale':'Unavailable'}</span></>}/>})}
      <Metric label="SPY trend" value={signal?.regime==='Unavailable'||!signal?.rules.length?'—':signal.regime} detail="Five-minute context"/>
      <Metric label="Trading signal" value={signal?.label??'Insufficient data'} detail={signal?.rules.length?'Momentum '+signal.score+' / 100':'Momentum —'}/>
      <Metric label="Open P&L" value={money(position?.unrealized)} detail={position?'Estimated · '+pct(position.return_pct):'Flat'}/>
      <Metric label="Next 1m close" value={seconds==null?'—':Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0')} detail={live?clock(live.now)+' ET':'Eastern Time'}/>
    </div>
    {!ready&&<div className="banner"><b>Live feed unavailable</b><span>{diagnostics?.status==='Alpaca credentials required'?'Set your Alpaca IEX credentials on the server. You can still record actual executions.':'Check connection diagnostics in Settings.'}</span></div>}
    <div className="tradebar panel">
      <div className="tradebar-left"><div><strong>{symbol}</strong><small>{position?num(position.qty,6)+' shares open':'No active position'}</small></div>
      <select aria-label="Trading instrument" value={symbol} onChange={e=>setSymbol(e.target.value)}>{Object.keys(diagnostics?.symbols??{SPYU:true}).filter(s=>s!=='SPY').map(s=><option key={s}>{s}</option>)}</select></div>
      <div className="actions"><button className="buy" onClick={()=>open('buy')}>+ Record Buy</button><button className="sell" onClick={()=>open('sell')}>− Record Sell</button><button onClick={()=>setCheckOpen(true)}>☑ Checklist</button></div>
    </div>
    <div className="workspace-grid">
      <div className="stack">
        <Section title="Market charts" sub="IEX · Regular session · Eastern Time" action={<div className="segments">{([1,5] as const).map(n=><button key={n} className={frame===n?'selected':''} onClick={()=>setFrame(n)}>{n}m</button>)}</div>}>
          <div className="overlay-control">{Object.entries(overlayNames).map(([key,label])=><label key={key}><input type="checkbox" checked={overlays.has(key as Overlay)} onChange={e=>{
            const next=new Set(overlays); if(e.target.checked)next.add(key as Overlay);else next.delete(key as Overlay)
            setOverlays(next);localStorage.setItem('trading-overlays',JSON.stringify([...next]))
          }}/>{label}</label>)}</div>
          {['SPY',symbol].map(s=><div key={s} className="chart-card"><div className="chart-title"><b>{s}</b><span>{s==='SPY'?'Market indicator':'Trading instrument'}</span><span className="chart-price">{money(diagnostics?.symbols[s]?.price)}</span></div>
            <Chart symbol={s} frame={frame} data={live?.charts[s]} overlays={overlays} executions={live?.executions} position={s===symbol?position:undefined} sync={sync}/>
          </div>)}
        </Section>
        <LevelPanel levels={live?.levels[symbol]}/>
      </div>
      <aside className="stack"><SignalPanel signal={signal}/><PositionPanel position={position}/></aside>
    </div>
    {checkOpen&&<Dialog title="Pre-trade checklist" close={()=>setCheckOpen(false)}><Checklist
      values={checklist(live,symbol,0,0,5,manualChecks)} change={(k,v)=>setManualChecks({...manualChecks,[k]:v})}/>
      <p className="muted">Position size and stop are assessed with your execution details.</p></Dialog>}
  </>
}
function App(){
  const [page,setPage]=useState<Page>('Live Trading')
  const [live,setLive]=useState<Live|null>(null)
  const [online,setOnline]=useState(false)
  const [symbol,setSymbol]=useState('SPYU')
  const [manualChecks,setManualChecks]=useState<Record<string,boolean>>({})
  const [order,setOrder]=useState<string|null>(null)
  const [revision,refresh]=useState(0)
  const [message,setMessage]=useState('')
  useEffect(()=>{
    let stopped=false
    let retry:number|undefined
    let socket:WebSocket|undefined
    const connect=()=>{
      socket=new WebSocket((location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+'/ws')
      socket.onopen=()=>setOnline(true)
      socket.onmessage=e=>{
        const next=JSON.parse(e.data) as Live
        setLive(previous=>({...next,charts:Object.keys(next.charts).length?next.charts:previous?.charts??{},
          executions:Object.keys(next.charts).length?next.executions:previous?.executions??[]}))
      }
      socket.onclose=()=>{
        setOnline(false)
        if(!stopped) retry=window.setTimeout(connect,2000)
      }
    }
    api<Live>('/api/live').then(x=>{if(!stopped)setLive(v=>v??x)}).catch(()=>{})
    connect()
    return ()=>{stopped=true;window.clearTimeout(retry);socket?.close()}
  },[])
  const saved=()=>{setOrder(null);setManualChecks({});refresh(r=>r+1);setMessage('Execution saved')}
  useEffect(()=>{if(message){const timer=window.setTimeout(()=>setMessage(''),3500);return ()=>window.clearTimeout(timer)}},[message])
  const icons=['⌁','▤','▥','☷','⚙']
  return <div className="app">
    <header className="topbar"><div className="brand"><span>⌁</span><div>Live Trading <small>Dashboard</small></div></div>
      <nav aria-label="Primary navigation">{pages.map((p,i)=><button key={p} className={p===page?'active':''} aria-current={p===page?'page':undefined} onClick={()=>{setPage(p);window.scrollTo(0,0)}}><span aria-hidden>{icons[i]}</span>{p}</button>)}</nav>
      <div className="header-status"><i className={online?'status-dot':'status-dot idle'}/>{online?'Dashboard online':'Reconnecting'}</div>
    </header>
    <main>
      {page==='Live Trading'&&<LivePage live={live} connected={online} symbol={symbol} setSymbol={setSymbol} open={setOrder} manualChecks={manualChecks} setManualChecks={setManualChecks}/>}
      {page==='Trading Journal'&&<Journal live={live} revision={revision}/>}
      {page==='Performance'&&<Performance live={live} revision={revision}/>}
      {page==='Trading Rules'&&<Rules live={live} symbol={symbol}/>}
      {page==='Settings'&&<Settings live={live}/>}
    </main>
    {order&&<TradeForm side={order} symbol={symbol} live={live} manualChecks={manualChecks} setManualChecks={setManualChecks} close={()=>setOrder(null)} done={saved}/>}
    {message&&<div className="toast" role="status">{message}</div>}
  </div>
}
createRoot(document.getElementById('root')!).render(<App/>)
