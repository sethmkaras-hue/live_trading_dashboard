import {useEffect,useState,type FormEvent} from 'react'
import {Chart} from './Chart'
import {Dialog,Metric,RuleResults,Section,TradeForm} from './main'
import {api,date,duration,localDate,money,num,pct,title,type ChartData,type Execution,type Live,type Overlay,type Position,type Signal} from './types'

function useResource<T>(url:string,revision=0){
  const [data,setData]=useState<T|null>(null)
  const [error,setError]=useState('')
  const [local,update]=useState(0)
  useEffect(()=>{
    let active=true
    api<T>(url).then(value=>{if(active){setData(value);setError('')}}).catch(err=>{if(active)setError(err.message)})
    return ()=>{active=false}
  },[url,revision,local])
  return {data,error,reload:()=>update(x=>x+1)}
}
const reviewOverlays=new Set<Overlay>(['ema9','ema20','support','resistance','supportTrail','resistanceTrail','executions','position'])
function Review({trade,close,screenshots}:{trade:Position,close:()=>void,screenshots:string[]}){
  const {data,error}=useResource<{chart:ChartData,excursion:{peak:number|null,trough:number|null},signals:Signal[]}>('/api/review/'+trade.id)
  const buy=trade.executions.find(x=>x.side==='buy')
  const position={...trade,stop_price:buy?buy.price*(1-buy.stop/100):trade.entry,target:buy?.target??null}
  return <Dialog title={trade.symbol+' trade review'} close={close}>
    {error&&<p role="alert" className="error">{error}</p>}
    <div className="metric-grid compact"><Metric label="Net realized" value={money(trade.net)}/><Metric label="Peak observed" value={money(data?.excursion.peak)}/><Metric label="Low observed" value={money(data?.excursion.trough)}/></div>
    <Chart symbol={trade.symbol} data={data?.chart} frame={1} overlays={reviewOverlays} executions={trade.executions} position={position}/>
    <SeriesPlot label="Momentum during trade" data={data?.signals.map(s=>[s.t??0,s.score])??[]}/>
    {trade.executions.map(e=><details key={e.id}><summary>{title(e.side)} · {date(e.t)} · {money(e.price)}</summary>
      <p>{e.notes.notes||'No justification recorded.'} {e.notes.emotion&&'Emotion · '+e.notes.emotion} {e.notes.lessons&&'Lessons · '+e.notes.lessons}</p>
      <p>SPY {money(e.context.trade_prices.SPY)} · {e.symbol} {money(e.context.trade_prices[e.symbol])} · Rules {e.version?'v'+e.version:'—'}</p>
      {screenshots.includes(e.id)&&<img className="trade-screenshot" src={'/api/screenshots/'+e.id} alt={title(e.side)+' chart screenshot'}/>}
      {e.context.signal?<><p>{e.context.signal.explanation}</p><RuleResults rules={e.context.signal.rules}/></>:<p className="muted">Execution-time rule context unavailable.</p>}
      {Object.keys(e.checklist).length>0&&<p className="muted">Checklist concerns · {Object.entries(e.checklist).filter(([,v])=>!v).map(([k])=>title(k)).join(', ')||'None'}</p>}
    </details>)}
  </Dialog>
}
export function Journal({live,revision}:{live:Live|null,revision:number}){
  const {data,error,reload}=useResource<{open:Position[],closed:Position[],screenshots:string[]}>('/api/journal',revision)
  const [symbol,setSymbol]=useState('')
  const [outcome,setOutcome]=useState('')
  const [compliance,setCompliance]=useState('')
  const [start,setStart]=useState('')
  const [end,setEnd]=useState('')
  const [edit,setEdit]=useState<Execution|null>(null)
  const [side,setSide]=useState<string|null>(null)
  const [review,setReview]=useState<Position|null>(null)
  const [upload,setUpload]=useState<Execution|null>(null)
  const [uploadError,setUploadError]=useState('')
  const [uploadBusy,setUploadBusy]=useState(false)
  const uploadImage=async(event:FormEvent<HTMLFormElement>)=>{
    event.preventDefault()
    const file=new FormData(event.currentTarget).get('image')
    if(!(file instanceof File)||!file.size||file.size>2*1024*1024){setUploadError('Choose an image up to 2 MiB.');return}
    setUploadBusy(true);setUploadError('')
    try{
      const image=await new Promise<string>((resolve,reject)=>{
        const reader=new FileReader()
        reader.onload=()=>resolve(String(reader.result).split(',')[1])
        reader.onerror=reject
        reader.readAsDataURL(file)
      })
      await api('/api/screenshots/'+upload?.id,'PUT',{image})
      setUpload(null);reload()
    }catch(err){setUploadError((err as Error).message??'Screenshot could not be uploaded.')}
    finally{setUploadBusy(false)}
  }
  const trades=[...data?.open??[],...data?.closed??[]].filter(t=>(!symbol||symbol===t.symbol)&&
    (!outcome||t.exit_t&&(outcome==='profit'?t.net>0:outcome==='loss'?t.net<0:t.net===0))&&
    (!compliance||t.compliant===(compliance==='yes'))&&(!start||localDate(t.t)>=start)&&(!end||localDate(t.t)<=end))
  return <>
    <div className="page-heading"><div><h1>Trading Journal</h1><p>Recorded Webull executions, decisions, and trade reviews.</p></div>
      <div className="actions"><button className="buy" onClick={()=>setSide('buy')}>Record Buy</button><button className="sell" onClick={()=>setSide('sell')}>Record Sell</button></div></div>
    <Section title="Trade history" action={<div className="actions"><a className="button" href="/api/export/csv">CSV</a><a className="button" href="/api/export/xlsx">Excel</a></div>}>
      <div className="filters">
        <label>Instrument<select value={symbol} onChange={e=>setSymbol(e.target.value)}><option value="">All</option>{Object.keys(live?.diagnostics.symbols??{SPYU:true}).filter(s=>s!=='SPY').map(s=><option key={s}>{s}</option>)}</select></label>
        <label>From<input type="date" value={start} onChange={e=>setStart(e.target.value)}/></label>
        <label>Through<input type="date" value={end} onChange={e=>setEnd(e.target.value)}/></label>
        <label>Profitability<select value={outcome} onChange={e=>setOutcome(e.target.value)}><option value="">All</option><option value="profit">Profitable</option><option value="loss">Loss</option><option value="flat">Break-even</option></select></label>
        <label>Compliance<select value={compliance} onChange={e=>setCompliance(e.target.value)}><option value="">All</option><option value="yes">Compliant</option><option value="no">Issues recorded</option></select></label>
      </div>
      {error&&<p className="error">{error}</p>}
      {!trades.length?<div className="empty">{data?'No trades match these filters.':'Loading journal…'}</div>:trades.map(t=><details className="journal-row" key={t.id}>
        <summary><b>{t.symbol}</b> <span>{date(t.t)}</span> <span>{t.exit_t?money(t.net):'Open'}</span></summary>
        <div className="metric-grid compact"><Metric label="Entry / exit" value={money(t.entry)+' / '+money(t.exit)}/><Metric label="Position size" value={money(t.invested)}/><Metric label="Planned risk" value={money(t.risk)}/><Metric label="Gross and fees" value={money(t.gross)+' / '+money(t.fees)}/><Metric label="Net return" value={pct(t.return_pct)}/><Metric label="Holding time" value={t.exit_t?duration(t.holding_seconds):'Open'}/><Metric label="Rule compliance" value={t.compliant?'Compliant':'Issues recorded'}/><Metric label="Rules" value={t.version?'v'+t.version:'—'}/></div>
        {t.executions.map(e=><div className="execution-row" key={e.id}><span>{title(e.side)} {num(e.qty,6)} @ {money(e.price)} · {date(e.t)}<small>{e.notes.notes}{e.notes.lessons&&' · '+e.notes.lessons}</small></span><span className="actions"><button onClick={()=>setEdit(e)}>Edit</button><button onClick={()=>{setUpload(e);setUploadError('')}}>{data?.screenshots.includes(e.id)?'Replace screenshot':'Add screenshot'}</button>
          {data?.screenshots.includes(e.id)&&<a className="button" href={'/api/screenshots/'+e.id} target="_blank" rel="noreferrer">View</a>}</span></div>)}
        {t.exit_t&&<button onClick={()=>setReview(t)}>Review trade</button>}
      </details>)}
      <p className="muted">Webull CSV import requires verification against an actual Webull export.</p>
    </Section>
    {(edit||side)&&<TradeForm side={edit?.side??side!} symbol={edit?.symbol??'SPYU'} initial={edit??undefined} live={live} manualChecks={{}} setManualChecks={()=>{}} close={()=>{setEdit(null);setSide(null)}} done={()=>{setEdit(null);setSide(null);reload()}}/>}
    {review&&<Review trade={review} screenshots={data?.screenshots??[]} close={()=>setReview(null)}/>}
    {upload&&<Dialog title="Chart screenshot" close={()=>setUpload(null)}><form className="stack" onSubmit={uploadImage}>
      <label>PNG, JPEG or WebP, up to 2 MiB<input name="image" type="file" accept="image/png,image/jpeg,image/webp" required/></label>
      {uploadError&&<p role="alert" className="error">{uploadError}</p>}
      <button className="primary" disabled={uploadBusy}>{uploadBusy?'Uploading…':'Save screenshot'}</button>
    </form></Dialog>}
  </>
}

type Perf = Record<string,unknown> & {
  equity:number[][],cumulative:number[][],daily:Record<string,number>,monthly:Record<string,number>,
  groups:Record<string,Record<string,Record<string,number|null>>>,flows:{id:number,t:number,amount:number,note:string}[],
  trades:Position[],stage:number,stage_size:number,stage_sizes:number[],next_size:number|null,stage_count:number,criteria:Record<string,boolean>
}
function SeriesPlot({data,label,currency=false}:{data:number[][],label:string,currency?:boolean}){
  if(!data.length)return <div className="empty">No {label.toLowerCase()} recorded yet.</div>
  const values=data.map(x=>x[1]),min=Math.min(...values),max=Math.max(...values)
  const first=data[0][0],last=data.at(-1)![0]
  const coordinates=data.map(([t,v])=>[20+(t-first)/Math.max(1,last-first)*540,145-(v-min)/Math.max(.01,max-min)*125])
  const [x,y]=coordinates.at(-1)!
  return <figure className="plot"><svg viewBox="0 0 580 166" role="img" aria-label={label}>
    <path d="M20 20V145H560 M20 82H560" stroke="#293746" fill="none"/>
    <polyline points={coordinates.map(p=>p.join(',')).join(' ')} stroke="#65d9ad" fill="none" strokeWidth="2.5"/>
    <circle cx={x} cy={y} r="4" fill="#65d9ad"/>
  </svg><figcaption>{currency?money(min):num(min)} to {currency?money(max):num(max)} · {label}</figcaption></figure>
}
const perfMetrics=[
  ['starting_balance','Starting balance','$'],['balance','Recorded balance','$'],['deposits','Deposits','$'],['withdrawals','Withdrawals','$'],
  ['realized','Lifetime realized P&L','$'],['unrealized','Estimated unrealized P&L','$'],['daily_net','Daily net','$'],['weekly_net','Weekly net','$'],['monthly_net','Monthly net','$'],['return_pct','Cash-adjusted return','%'],
  ['count','Completed trades',''],['win_rate','Win rate','%'],['avg_win','Average win','$'],['avg_loss','Average loss','$'],
  ['best','Largest win','$'],['worst','Largest loss','$'],['profit_factor','Profit factor',''],['expectancy','Net expectancy','$'],
  ['drawdown','Maximum drawdown','$'],['consecutive_wins','Current winning streak',''],['consecutive_losses','Current losing streak',''],
  ['longest_win','Longest winning streak',''],['longest_loss','Longest losing streak',''],['compliance','Rule compliance','%'],
  ['avg_duration','Average hold time','t'],['avg_risk','Average planned risk','$'],['avg_r','Average net / planned risk',''],
]
function format(value:unknown,kind:string){
  const n=typeof value==='number'?value:null
  return kind==='$'?money(n):kind==='%'?pct(n):kind==='t'?duration(n):num(n)
}
function Calendar({daily,now}:{daily:Record<string,number>,now:number}){
  const [month,setMonth]=useState(localDate(now).slice(0,7))
  const [year,m]=month.split('-').map(Number)
  const pad=new Date(Date.UTC(year,m-1,1)).getUTCDay()
  const days=new Date(Date.UTC(year,m,0)).getUTCDate()
  return <><label className="inline-field">Month<input type="month" value={month} onChange={e=>e.target.value&&setMonth(e.target.value)}/></label>
    <div className="calendar">{['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].map(d=><b key={d}>{d}</b>)}{Array.from({length:pad},(_,i)=><span key={'pad'+i}/>)}
      {Array.from({length:days},(_,i)=>{const d=i+1,value=daily[month+'-'+String(d).padStart(2,'0')];return <div key={d}><span>{d}</span>{value!==undefined&&<small className={value>=0?'good':'bad'}>{money(value)}</small>}</div>})}</div></>
}
export function Performance({live,revision}:{live:Live|null,revision:number}){
  const {data,error,reload}=useResource<Perf>('/api/performance',revision)
  const [notice,setNotice]=useState('')
  const [failed,setFailed]=useState(false)
  const [busy,setBusy]=useState(false)
  const act=async(path:string,body:unknown)=>{
    setBusy(true);setNotice('')
    try{await api(path,'POST',body);reload();setFailed(false);setNotice(path.endsWith('stage')?'Stage advanced. Review your independent limits in Settings.':'Cash flow recorded.');return true}
    catch(e){setFailed(true);setNotice((e as Error).message);return false}
    finally{setBusy(false)}
  }
  return <>
    <div className="page-heading"><div><h1>Performance</h1><p>Manually recorded account activity and net trading results.</p></div></div>
    {error&&<p className="error">{error}</p>}
    <Section title="Account and trade metrics" sub="Balance is manually tracked. Realized results include recorded fees.">
      <div className="metric-grid">{perfMetrics.map(([k,label,kind])=><Metric key={k} label={label} value={format(k==='unrealized'?Object.keys(live?.positions??{}).length?Object.values(live?.positions??{}).every(x=>typeof x.unrealized==='number')?Object.values(live?.positions??{}).reduce((n,p)=>n+(p.unrealized??0),0):null:data?.unrealized:data?.[k],kind)}/>)}</div>
    </Section>
    {data&&<><div className="two-up"><Section title="Account equity"><SeriesPlot label="Recorded equity" data={data.equity} currency/></Section><Section title="Cumulative P&L"><SeriesPlot label="Realized P&L" data={data.cumulative} currency/></Section></div>
      <div className="two-up"><Section title="Daily trading calendar"><Calendar daily={data.daily} now={live?.now??Date.now()/1000}/></Section>
      <Section title="Monthly net P&L">{Object.keys(data.monthly).length?<div className="month-bars">{Object.entries(data.monthly).map(([k,v])=><div key={k}><span>{k}</span><progress value={Math.abs(v)} max={Math.max(1,...Object.values(data.monthly).map(Math.abs))}/><strong className={v>=0?'good':'bad'}>{money(v)}</strong></div>)}</div>:<div className="empty">No monthly results yet.</div>}</Section></div>
      <Section title="Position-size progression" sub="Thirty completed trades is a learning milestone. Advancement requires your approval.">
        <div className="stages">{data.stage_sizes.map((n,i)=><div className={data.stage===i+1?'current':''} key={n}><span>Level {i+1}</span><b>{money(n)}</b><small>{money(n*.05)} at a 5% stop</small></div>)}</div>
        <label>Completed trades at this level · {data.stage_count} / 30<progress value={data.stage_count} max="30"/></label>
        <div className="criteria">{Object.entries(data.criteria).map(([k,v])=><span key={k} className={'tag '+(v?'good':'')}>{v?'✓':'○'} {title(k)}</span>)}</div>
        <button disabled={busy||!data.next_size||!Object.values(data.criteria).every(Boolean)} onClick={()=>void act('/api/stage',{})}>Approve next level</button>
      </Section>
      <Section title="Deposits and withdrawals"><form className="filters" onSubmit={async e=>{e.preventDefault();const form=e.currentTarget,d=new FormData(form);if(await act('/api/flows',{amount:Number(d.get('amount')),note:d.get('note')}))form.reset()}}>
        <label>Amount ($)<input name="amount" type="number" step=".01" required placeholder="Negative for withdrawal"/></label><label>Note<input name="note" maxLength={1000}/></label><button className="primary" disabled={busy}>Record cash flow</button>
      </form>
      {notice&&<p role="status" className={failed?'error':'good'}>{notice}</p>}
      {data.flows.map(f=><div className="execution-row" key={f.id}><span>{date(f.t)} · {f.note||'Cash flow'}</span><b>{money(f.amount)}</b></div>)}</Section>
      <div className="two-up">{Object.entries(data.groups).map(([field,groups])=><Section key={field} title={'Performance by '+({symbol:'instrument',stage:'position-size level',version:'rule version',compliant:'rule compliance'}[field]??field)}>
        {Object.entries(groups).length?<div className="table-wrap"><table><thead><tr><th>Group</th><th>Trades</th><th>Net P&L</th><th>Expectancy</th></tr></thead><tbody>
          {Object.entries(groups).map(([name,g])=><tr key={name}><td>{field==='compliant'?name==='True'?'Compliant':'Issues recorded':name}</td><td>{num(g.count)}</td><td>{money(g.net)}</td><td>{money(g.expectancy)}</td></tr>)}</tbody></table></div>:<div className="empty">No completed trades.</div>}
      </Section>)}</div>
      <Section title="Completed trades">{data.trades.length?<div className="table-wrap"><table><thead><tr><th>Instrument</th><th>Exit</th><th>Net P&L</th><th>Hold</th></tr></thead><tbody>{[...data.trades].reverse().map(t=><tr key={t.id}><td>{t.symbol}</td><td>{date(t.exit_t)}</td><td>{money(t.net)}</td><td>{duration(t.holding_seconds)}</td></tr>)}</tbody></table></div>:<div className="empty">No completed trades.</div>}</Section>
    </>}
  </>
}

type RuleConfig={enabled:boolean,symbol:string,timeframe:number,mandatory:boolean,threshold:number,lookback:number,confirmations:number,weight:number,wick_ratio:number}
type Config={level_window:number,tolerance_pct:number,trail_minutes:number,rules:Record<string,RuleConfig>}
type ConfigResult={id:number,config:Config,metadata:Record<string,{group:string,description:string,comparison:string}>,yaml:string,versions:{id:number,t:number}[]}
export function Rules({live,symbol}:{live:Live|null,symbol:string}){
  const {data,error,reload}=useResource<ConfigResult>('/api/rules')
  const [config,setConfig]=useState<Config|null>(null)
  const [notice,setNotice]=useState('')
  const [busy,setBusy]=useState(false)
  const [failed,setFailed]=useState(false)
  const [yamlText,setYamlText]=useState('')
  useEffect(()=>{if(data)setConfig(data.config)},[data])
  const save=async(body:unknown)=>{
    setBusy(true);setNotice('')
    try{const saved=await api<{id:number}>('/api/rules','PUT',body);setFailed(false);setNotice('Version '+saved.id+' applied. New evaluations use this version.');reload()}
    catch(e){setFailed(true);setNotice((e as Error).message)}
    finally{setBusy(false)}
  }
  const update=(key:string,values:Partial<RuleConfig>)=>{
    if(config)setConfig({...config,rules:{...config.rules,[key]:{...config.rules[key],...values}}})
  }
  const download=()=>{
    if(!data)return
    const url=URL.createObjectURL(new Blob([data.yaml],{type:'text/yaml'}))
    const a=document.createElement('a');a.href=url;a.download='rules-v'+data.id+'.yaml';a.click()
    setTimeout(()=>URL.revokeObjectURL(url),1000)
  }
  return <><div className="page-heading"><div><h1>Trading Rules</h1><p>Deterministic entry conditions, momentum warnings, and exits.</p></div>
    <button className="primary" disabled={busy||!config} onClick={()=>void save({config})}>Save and apply</button></div>
    {error&&<p className="error">{error}</p>}{notice&&<p role="status" className={failed?'error':'good'}>{notice}</p>}
    {config&&data&&<>
      <Section title="Level settings" sub={'Active rule configuration v'+data.id}>
        <div className="fields">{([['level_window','Level lookback (minutes)'],['tolerance_pct','Level tolerance (%)'],['trail_minutes','Trail length (minutes)']] as const).map(([k,label])=><label key={k}>{label}<input type="number" step="any" value={config[k]} onChange={e=>setConfig({...config,[k]:Number(e.target.value)})}/></label>)}</div>
      </Section>
      {(['Entry','Warning','Exit','Risk'] as const).map(group=><Section key={group} title={{Entry:'Entry conditions',Warning:'Momentum warnings',Exit:'Exit conditions',Risk:'Risk management'}[group]}>
        {Object.entries(config.rules).filter(([k])=>data.metadata[k].group===group).map(([key,r])=><details className="rule-editor" key={key}>
          <summary><b>{title(key)}</b><span className={'tag '+(r.enabled?'good':'')}>{r.enabled?r.mandatory?'Mandatory':'Enabled':'Disabled'}</span></summary>
          <p className="muted">{data.metadata[key].description}. Compare {data.metadata[key].comparison} {r.threshold}. Require {r.confirmations} confirmation bar(s) on {r.symbol} {r.timeframe}m with a {r.lookback}-bar lookback.</p>
          <div className="fields">
            <label>Enabled<select value={String(r.enabled)} onChange={e=>update(key,{enabled:e.target.value==='true'})}><option value="true">Yes</option><option value="false">No</option></select></label>
            <label>Instrument<select value={r.symbol} onChange={e=>update(key,{symbol:e.target.value})}>{[...new Set([r.symbol,...Object.keys(live?.diagnostics.symbols??{SPY:true,SPYU:true})])].map(s=><option key={s}>{s}</option>)}</select></label>
            <label>Timeframe<select value={r.timeframe} onChange={e=>update(key,{timeframe:Number(e.target.value)})}><option value={1}>1 minute</option><option value={5}>5 minutes</option></select></label>
            {group==='Entry'&&<label>Classification<select value={String(r.mandatory)} onChange={e=>update(key,{mandatory:e.target.value==='true'})}><option value="false">Optional</option><option value="true">Mandatory</option></select></label>}
            {([['threshold','Threshold'],['lookback','Lookback bars'],['confirmations','Confirmations'],['weight','Score weight'],...(['upper_wicks','rejections'].includes(key)?[['wick_ratio','Wick/body ratio']]:[])] as [keyof Pick<RuleConfig,'threshold'|'lookback'|'confirmations'|'weight'|'wick_ratio'>,string][]).map(([k,label])=><label key={k}>{label}<input type="number" step="any" value={r[k]} onChange={e=>update(key,{[k]:Number(e.target.value)})}/></label>)}
          </div></details>)}
      </Section>)}
      <Section title="Live rule results" sub={symbol}><RuleResults rules={live?.signals[symbol]?.rules??[]}/></Section>
      <Section title="Configuration history" action={<div className="actions"><button onClick={download}>Export YAML</button><button disabled={busy} onClick={()=>void save({defaults:true})}>Restore defaults</button></div>}>
        <div className="stack"><label>Import YAML<input type="file" accept=".yaml,.yml,text/yaml" onChange={async e=>{const file=e.target.files?.[0];if(file&&file.size<=60000)setYamlText(await file.text());else if(file){setFailed(true);setNotice('Choose a YAML file under 60 KB.')}}}/></label>
          <textarea rows={5} value={yamlText} onChange={e=>setYamlText(e.target.value)} placeholder="Paste YAML or choose a file"/>
          <button disabled={busy||!yamlText.trim()} onClick={()=>void save({yaml_text:yamlText})}>Validate and apply YAML</button></div>
        {data.versions.map(v=><div className="execution-row" key={v.id}><span>v{v.id} · {date(v.t)}{v.id===data.id?' · Active':''}</span><button disabled={busy||v.id===data.id} onClick={()=>void save({rollback:v.id})}>Apply as new version</button></div>)}
      </Section>
    </>}
  </>
}
const riskLabels:Record<string,string>={starting_balance:'Starting balance ($)',trade_risk:'Maximum risk per trade ($)',daily_loss:'Daily loss ($)',weekly_loss:'Weekly loss ($)',
  drawdown:'Account drawdown ($)',consecutive_losses:'Consecutive losses',max_size:'Maximum position size ($)',close_buffer:'Minutes before close',
  slippage_pct:'Slippage allowance (%)',minimum_profit_factor:'Advancement profit factor',minimum_compliance:'Advancement compliance (%)',...Object.fromEntries([1,2,3,4,5].map(i=>['stage_'+i,'Level '+i+' position size ($)']))}
export function Settings({live}:{live:Live|null}){
  const [risk,setRisk]=useState<Record<string,number>|null>(null)
  const [price,setPrice]=useState(0)
  const [stop,setStop]=useState(5)
  const [notice,setNotice]=useState('')
  const [failed,setFailed]=useState(false)
  const [busy,setBusy]=useState(false)
  const diag=live?.diagnostics
  useEffect(()=>{if(live)setRisk(x=>x??live.risk)},[live])
  const save=async(e:FormEvent)=>{
    e.preventDefault();setBusy(true)
    try{await api('/api/risk','PUT',risk);setFailed(false);setNotice('Risk settings saved.')}
    catch(err){setFailed(true);setNotice((err as Error).message)}
    finally{setBusy(false)}
  }
  const budget=Math.min(risk?.max_size??0,live?.stage_size??0,live?.available_capital??0)
  const shares=price>0&&stop>0&&stop<100?Math.floor(Math.min(budget/price,(risk?.trade_risk??0)/(price*(stop+(risk?.slippage_pct??0))/100))):null
  return <><div className="page-heading"><div><h1>Settings</h1><p>Data diagnostics, risk limits, and application backups.</p></div></div>
    <Section title="Connection diagnostics" sub="Alpaca IEX · Regular US equity session">
      <div className="metric-grid compact"><Metric label="Feed status" value={diag?.status??'Connecting'}/><Metric label="Reconnect attempts" value={diag?.reconnects??'—'}/><Metric label="Session close" value={date(diag?.session.end)}/><Metric label="Last backfill" value={date(diag?.last_backfill)} detail={diag?.backfill_error}/><Metric label="Last rule evaluation" value={diag?.evaluation_ms==null?"—":num(diag.evaluation_ms)+" ms"}/></div>
      <div className="table-wrap"><table><thead><tr>{['Instrument','Data quality','Last price','Age','Updates / min','Mean latency','Completed bars','Missing bars','Bid / ask'].map(x=><th key={x}>{x}</th>)}</tr></thead>
      <tbody>{Object.entries(diag?.symbols??{}).map(([s,q])=><tr key={s}><td>{s}</td><td>{q.fresh?'Fresh':q.price!=null?'Stale':'Unavailable'}</td><td>{date(q.t)}</td>
      <td>{q.age==null?'—':q.age+'s'}</td><td>{q.updates_per_minute}</td><td>{q.mean_latency==null?'—':num(q.mean_latency)+'s'}</td><td>{q.candles}</td><td>{q.missing.length}</td><td>{money(q.quote?.bid)} / {money(q.quote?.ask)}</td></tr>)}</tbody></table></div>
      {Object.entries(diag?.symbols??{}).filter(([,q])=>q.missing.length).map(([s,q])=><details key={s}><summary>{s} missing candle timestamps</summary><p className="muted">{q.missing.map(t=>date(t)).join(' · ')}</p></details>)}
      <p className="muted">IEX reflects a subset of US equity activity. Configure Alpaca credentials and up to four extra symbols in the server environment, then restart the application.</p>
    </Section>
    <Section title="Risk limits" sub="Position size and dollar risk are independent."><form onSubmit={save} className="stack">
      <div className="fields">{Object.entries(risk??{}).map(([k,v])=><label key={k}>{riskLabels[k]??title(k)}<input type="number" step="any" min=".01" required value={v} onChange={e=>setRisk({...risk,[k]:Number(e.target.value)})}/></label>)}</div>
      {notice&&<p role="status" className={failed?'error':'good'}>{notice}</p>}<button className="primary" disabled={busy||!risk}>Save risk settings</button>
    </form></Section>
    <Section title="Position-size calculator"><div className="fields">
      <label>Planned entry ($)<input type="number" step="any" min="0" value={price||''} onChange={e=>setPrice(Number(e.target.value))}/></label>
      <label>Stop (%)<input type="number" step="any" min=".01" max="99.99" value={stop} onChange={e=>setStop(Number(e.target.value))}/></label>
      <Metric label="Maximum whole shares" value={num(shares)}/><Metric label="Risk with slippage allowance" value={shares==null?'—':money(shares*price*(stop+(risk?.slippage_pct??0))/100)}/>
    </div><p className="muted">A stop can fill below its trigger price. Slippage and price gaps can increase losses beyond planned risk.</p></Section>
    <Section title="Backup and restore" action={<a className="button" href="/api/backup">Download database</a>}><p className="muted">Restore a backup with the dashboard stopped using the documented Ubuntu restore command.</p></Section>
  </>
}
