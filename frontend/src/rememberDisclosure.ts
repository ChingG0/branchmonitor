import type {ObjectDirective} from 'vue'
import './rememberDisclosure.css'

const storageKey='branch-monitor:v6:disclosures:v1'
const listeners=new WeakMap<HTMLDetailsElement,EventListener>()

function preferences():Record<string,boolean>{
 try{
  const saved:unknown=JSON.parse(localStorage.getItem(storageKey)||'{}')
  if(saved&&typeof saved==='object'&&!Array.isArray(saved)){
   return Object.fromEntries(Object.entries(saved).filter(([,value])=>typeof value==='boolean'))
  }
 }catch{/* Keep disclosures usable when browser storage is unavailable. */}
 return {}
}

export const vRememberDisclosure:ObjectDirective<HTMLDetailsElement,string>={
 beforeMount(el,{value:key}){
  el.dataset.v6Disclosure=key
  const saved=preferences()[key]
  if(typeof saved==='boolean')el.open=saved
  const onToggle:EventListener=(event)=>{
   if(event.target!==el||!el.isConnected)return
   try{localStorage.setItem(storageKey,JSON.stringify({...preferences(),[key]:el.open}))}catch{/* Storage may be disabled or full. */}
  }
  listeners.set(el,onToggle)
  el.addEventListener('toggle',onToggle)
 },
 beforeUnmount(el){
  const listener=listeners.get(el)
  if(listener)el.removeEventListener('toggle',listener)
  listeners.delete(el)
 }
}
