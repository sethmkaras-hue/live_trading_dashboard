export type Bar = {t:number,o:number,h:number,l:number,c:number,v:number}
export type Level = {t:number,s:number|null,r:number|null,evidence:{support_pivot:number|null,resistance_pivot:number|null}}
export type ChartData = {minute:Bar[],five:Bar[],trail:Level[]}
export type Rule = {key:string,group:string,description:string,comparison:string,state:string,value:number|null,
  threshold:number,mandatory:boolean,weight:number,contribution:number,t:number}
export type Signal = {label:string,regime:string,score:number,supporting:string[],conflicting:string[],failed:string[],explanation:string,
  symbol:string,quality:string,version:number|null,t:number|null,rules:Rule[],risk_flags:string[],price?:number}
export type Execution = {id:string,symbol:string,side:string,t:number,price:number,qty:number,fee:number,stop:number,
  target:number|null,notes:{notes:string,emotion:string,lessons:string},checklist:Record<string,boolean>,
  context:{available:boolean,signal:Signal|null,trade_prices:Record<string,number|null>},version:number|null}
export type Position = {id:string,symbol:string,t:number,exit_t?:number,qty:number,entry:number,exit?:number,net:number,gross:number,
  invested:number,risk:number,fees:number,return_pct:number|null,holding_seconds:number,stage:number,version:number|null,
  compliant:boolean,executions:Execution[],stop_price:number,target:number|null,price?:number,value?:number,
  unrealized?:number,peak?:number,trough?:number,drawdown?:number,stop_distance?:number,warnings?:string[]}
export type Quote = {price?:number,t?:number,age:number|null,fresh:boolean,daily_change:number|null,
  updates_per_minute:number,mean_latency:number|null,candles:number,missing:number[],quote?:{bid:number,ask:number,t:number}}
export type Live = {now:number,diagnostics:{status:string,reconnects:number,last_backfill:number|null,backfill_error:string|null,evaluation_ms:number|null,session:{start:number|null,end:number|null,open:boolean},symbols:Record<string,Quote>},
  charts:Record<string,ChartData>,positions:Record<string,Position>,signals:Record<string,Signal>,executions:Execution[],
  risk:Record<string,number>,stage:number,stage_size:number,balance:number,available_capital:number,
  levels:Record<string,Record<string,{price:number|null,percent:number|null,distance:number|null,slopes:Record<string,number|null>,duration:number,tests:number,crossings:number,trend:string}>>}
export type Overlay = 'ema9'|'ema20'|'support'|'resistance'|'supportTrail'|'resistanceTrail'|'executions'|'position'
export const money = (n?:number|null) => n==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(n)
export const num = (n?:number|null,digits=2) => n==null?'—':n.toLocaleString('en-US',{maximumFractionDigits:digits})
export const pct = (n?:number|null) => n==null?'—':num(n)+'%'
export const date = (t?:number|null) => t==null?'—':new Intl.DateTimeFormat('en-US',{
  month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',timeZone:'America/New_York'}).format(t*1000)+' ET'
export const clock = (t:number) => new Intl.DateTimeFormat('en-US',{hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZone:'America/New_York'}).format(t*1000)
export const localDate = (t:number) => {
  const parts = new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(t*1000)
  const part=(kind:string)=>parts.find(x=>x.type===kind)?.value
  return part('year')+'-'+part('month')+'-'+part('day')
}
export const duration = (n?:number|null) => n==null?'—':Math.floor(n/3600)+'h '+Math.floor(n%3600/60)+'m'
export const title = (name:string) => name.replaceAll('_',' ').replace(/^./,x=>x.toUpperCase())
export async function api<T>(url:string,method='GET',body?:unknown):Promise<T>{
  const response=await fetch(url,{method,headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
  if(!response.ok){
    const result=await response.json().catch(()=>({}))
    throw new Error(typeof result.detail==='string'?result.detail:response.status===422?'Check the values in the form.':'Request failed ('+response.status+').')
  }
  return response.json()
}
