import {useEffect,useRef,useState} from 'react'
import {CandlestickSeries,LineSeries,LineStyle,createChart,createSeriesMarkers,CrosshairMode,
  type IChartApi,type ISeriesApi,type ISeriesMarkersPluginApi,type LogicalRange,type Time,type UTCTimestamp} from 'lightweight-charts'
import {clock,date,money,type ChartData,type Execution,type Level,type Overlay,type Position} from './types'

type Peer = {chart:IChartApi, series:ISeriesApi<'Candlestick'>, close:Map<number,number>}
export type Sync = {peers:Set<Peer>,locked:boolean}
const timestamp = (t:number) => t as UTCTimestamp
const empty:ChartData={minute:[],five:[],trail:[]}

export function Chart({symbol,data=empty,frame,overlays,executions=[],position,sync}:{
  symbol:string,data?:ChartData,frame:1|5,overlays:Set<Overlay>,executions?:Execution[],position?:Position,sync?:Sync
}){
  const host=useRef<HTMLDivElement>(null), canvas=useRef<HTMLCanvasElement>(null)
  const engine=useRef<{
    peer:Peer,fast:ISeriesApi<'Line'>,slow:ISeriesApi<'Line'>,markers:ISeriesMarkersPluginApi<Time>,
    prices:ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[],initialized:boolean,frame:number,
  }|null>(null)
  const current=useRef({data,overlays,frame})
  current.current={data,overlays,frame}
  const [hover,setHover]=useState<{level:Level,bar:number|null}|null>(null)
  const [cursor,setCursor]=useState<string|null>(null)
  const [chartReady,ready]=useState(0)
  const bars=frame===1?data.minute:data.five
  useEffect(()=>{
    if(!host.current) return
    const chart=createChart(host.current,{
      autoSize:true,layout:{background:{color:'#10171f'},textColor:'#a0adbc',fontFamily:'Inter, system-ui, sans-serif',attributionLogo:true},
      grid:{horzLines:{color:'#1f2a35'},vertLines:{color:'#1c2630'}},
      rightPriceScale:{borderColor:'#263440',minimumWidth:73},timeScale:{borderColor:'#263440',timeVisible:true,secondsVisible:false,rightOffset:3,
        tickMarkFormatter:(time:Time)=>typeof time==='number'?clock(time):String(time)},
      crosshair:{mode:CrosshairMode.Normal,vertLine:{color:'#60748c'},horzLine:{color:'#60748c'}},
      localization:{timeFormatter:(time:Time)=>typeof time==='number'?clock(time)+' ET':String(time)},
    })
    const series=chart.addSeries(CandlestickSeries,{upColor:'#47d7a6',downColor:'#f37c83',borderVisible:false,wickUpColor:'#47d7a6',wickDownColor:'#f37c83',lastValueVisible:false,priceLineVisible:false})
    const fast=chart.addSeries(LineSeries,{color:'#79aaff',lineWidth:1,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false})
    const slow=chart.addSeries(LineSeries,{color:'#ecb768',lineWidth:1,lastValueVisible:false,priceLineVisible:false,crosshairMarkerVisible:false})
    const peer={chart,series,close:new Map<number,number>()}
    engine.current={peer,fast,slow,markers:createSeriesMarkers(series,[]),prices:[],initialized:false,frame}
    sync?.peers.add(peer)
    let job=0
    const draw=()=>{
      const el=canvas.current
      const size=chart.paneSize()
      if(!el||!size.width||!size.height) return
      const dpr=window.devicePixelRatio||1
      el.width=Math.round(size.width*dpr)
      el.height=Math.round(size.height*dpr)
      el.style.width=size.width+'px'
      el.style.height=size.height+'px'
      const ctx=el.getContext('2d')
      if(!ctx) return
      ctx.scale(dpr,dpr)
      const {data,overlays,frame}=current.current
      const trail=data.trail, step=frame*60
      const spacing=chart.timeScale().options().barSpacing
      for(const [i,item] of trail.entries()){
        const aligned=Math.floor(item.t/step)*step
        const anchor=chart.timeScale().timeToCoordinate(timestamp(aligned))
        if(anchor==null) continue
        const x=anchor+((item.t-aligned)/step-.5)*spacing
        ctx.globalAlpha=.18+.82*(i+1)/trail.length
        for(const field of ['s','r'] as const){
          if(!overlays.has(field==='s'?'supportTrail':'resistanceTrail')||item[field]==null) continue
          const y=series.priceToCoordinate(item[field]!)
          if(y==null) continue
          ctx.strokeStyle=field==='s'?'#47d7a6':'#f37c83'
          ctx.lineWidth=2
          ctx.beginPath()
          ctx.moveTo(x,y)
          ctx.lineTo(x+Math.max(1,spacing/frame),y)
          ctx.stroke()
        }
      }
    }
    const redraw=()=>{cancelAnimationFrame(job);job=requestAnimationFrame(draw)}
    const range=(value:LogicalRange|null)=>{
      redraw()
      if(!value||!sync||sync.locked) return
      sync.locked=true
      const visible=chart.timeScale().getVisibleRange()
      if(visible) for(const other of sync.peers) if(other!==peer&&other.close.size) other.chart.timeScale().setVisibleRange(visible)
      sync.locked=false
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(range)
    chart.subscribeCrosshairMove(param=>{
      if(typeof param.time!=='number'||!param.point){
        setHover(null)
        setCursor(null)
        if(sync&&!sync.locked) for(const other of sync.peers) if(other!==peer) other.chart.clearCrosshairPosition()
        return
      }
      const t=param.time
      setCursor(date(t))
      const {frame,data,overlays}=current.current
      const x=chart.timeScale().timeToCoordinate(timestamp(t))
      const index=frame===5&&x!=null?Math.min(4,Math.max(0,Math.floor(((param.point.x-x)/chart.timeScale().options().barSpacing+.5)*5))):0
      const point=data.trail.find(level=>level.t===t+index*60)
      setHover(point&&((overlays.has('supportTrail')&&point.s!=null)||(overlays.has('resistanceTrail')&&point.r!=null))?{level:point,bar:peer.close.get(t)??null}:null)
      if(sync&&!sync.locked){
        sync.locked=true
        for(const other of sync.peers) if(other!==peer){
          const price=other.close.get(t)
          if(price!==undefined) other.chart.setCrosshairPosition(price,timestamp(t),other.series)
          else other.chart.clearCrosshairPosition()
        }
        sync.locked=false
      }
    })
    const observer=new ResizeObserver(redraw)
    observer.observe(host.current)
    host.current.addEventListener('pointermove',redraw)
    host.current.addEventListener('wheel',redraw)
    const interval=window.setInterval(redraw,350)
    ready(x=>x+1)
    return ()=>{
      window.clearInterval(interval)
      cancelAnimationFrame(job)
      observer.disconnect()
      sync?.peers.delete(peer)
      chart.remove()
      engine.current=null
    }
  },[symbol,sync])
  useEffect(()=>{
    const c=engine.current
    if(!c) return
    const {peer,fast,slow,markers}=c
    const previous=peer.chart.timeScale().getVisibleLogicalRange()
    const timeline=bars.length?Array.from({length:(bars.at(-1)!.t-bars[0].t)/(frame*60)+1},(_,i)=>bars[0].t+i*frame*60):[]
    const lookup=new Map(bars.map(b=>[b.t,b]))
    peer.close=new Map(bars.map(b=>[b.t,b.c]))
    peer.series.setData(timeline.map(t=>{
      const b=lookup.get(t)
      return b?{time:timestamp(t),open:b.o,high:b.h,low:b.l,close:b.c}:{time:timestamp(t)}
    }))
    for(const [period,series,key] of [[9,fast,'ema9'],[20,slow,'ema20']] as const){
      let value:number|undefined
      series.applyOptions({visible:overlays.has(key)})
      series.setData(timeline.map(t=>{
        const bar=lookup.get(t)
        if(!bar) return {time:timestamp(t)}
        value=value===undefined?bar.c:value+(bar.c-value)*2/(period+1)
        return {time:timestamp(t),value}
      }))
    }
    markers.setMarkers(overlays.has('executions')?executions.filter(x=>x.symbol===symbol).map(x=>({...x,slot:Math.floor(x.t/(frame*60))*frame*60})).filter(x=>lookup.has(x.slot)).sort((a,b)=>a.slot-b.slot).map(x=>({
      time:timestamp(x.slot),position:'atPriceMiddle' as const,price:x.price,shape:x.side==='buy'?'arrowUp' as const:'arrowDown' as const,color:x.side==='buy'?'#47d7a6':'#f37c83',text:clock(x.t)+' · '+money(x.price)
    })):[])
    for(const price of c.prices) peer.series.removePriceLine(price)
    c.prices=[]
    const last=data.trail.at(-1)
    const add=(value:number|null|undefined,color:string,label:string,dashed=false)=>{
      if(value!=null) c.prices.push(peer.series.createPriceLine({price:value,color,title:label,lineWidth:2,lineStyle:dashed?LineStyle.Dashed:LineStyle.Solid}))
    }
    if(overlays.has('support')) add(last?.s,'#47d7a6','Support')
    if(overlays.has('resistance')) add(last?.r,'#f37c83','Resistance')
    if(position&&overlays.has('position')){
      add(position.entry,'#79aaff','Entry')
      add(position.stop_price,'#f37c83','Stop',true)
      add(position.target,'#e8b36b','Target',true)
    }
    if(bars.length&&(!c.initialized||c.frame!==frame)){
      if(timeline.length>75) peer.chart.timeScale().setVisibleLogicalRange({from:timeline.length-75,to:timeline.length+2})
      else peer.chart.timeScale().fitContent()
      c.initialized=true
    }else if(previous) peer.chart.timeScale().setVisibleLogicalRange(previous)
    c.frame=frame
  },[bars,frame,overlays,executions,position,data.trail,chartReady,symbol])
  return <div className="chart-shell">
    <div className="chart-host" ref={host} aria-label={symbol+' candlestick chart'}/>
    <canvas className="trail-layer" ref={canvas}/>
    {!bars.length&&<div className="chart-empty"><span className="empty-icon">⌁</span><strong>No completed {frame}-minute candles</strong><span>Completed IEX candles will appear here when available.</span></div>}
    {cursor&&bars.length>0&&<div className="chart-tooltip">
      {cursor}{hover&&<> · Close {money(hover.bar)}<br/>{date(hover.level.t)} · S {money(hover.level.s)} / R {money(hover.level.r)}
      <br/>Confirmed pivots {hover.level.evidence.support_pivot?clock(hover.level.evidence.support_pivot):'—'} / {hover.level.evidence.resistance_pivot?clock(hover.level.evidence.resistance_pivot):'—'}</>}
    </div>}
  </div>
}
