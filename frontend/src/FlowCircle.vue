<script setup lang="ts">
import {computed} from 'vue'
const props=defineProps<{buy:number|null;sell:number|null;period:string;scope:string}>()
const valid=computed(()=>props.buy!=null&&props.sell!=null&&Number.isFinite(props.buy)&&Number.isFinite(props.sell)&&props.buy>=0&&props.sell>=0)
const net=computed(()=>valid.value ? props.buy! - props.sell! : null)
const state=computed(()=>net.value==null?'pending':net.value>0?'buy':net.value<0?'sell':'flat')
const label=computed(()=>({pending:'待',buy:'買',sell:'賣',flat:'平'}[state.value]))
const ring=computed(()=>{
 if(!valid.value || (props.buy! + props.sell!) === 0)return undefined
 const share=100 * props.buy! / (props.buy! + props.sell!)
 return {background:`conic-gradient(#cc6056 0% ${share}%, #438c70 ${share}% 100%)`}
})
const fmt=(n:number)=>n.toLocaleString('zh-TW',{maximumFractionDigits:2})
const description=computed(()=>{
 const prefix=`${props.period} · ${props.scope}`
 if(!valid.value)return prefix+'：尚未載入，點個股後查詢。'
 const result=net.value!>0?'買方較多':net.value!<0?'賣方較多':'買賣相抵'
 return `${prefix}：買進 ${fmt(props.buy!)} 張、賣出 ${fmt(props.sell!)} 張；互抵後 ${net.value!>0?'+':''}${fmt(net.value!)} 張，${result}。圓環紅色為買進占比、綠色為賣出占比。`
})
</script>
<template>
 <span class="flow-circle" :class="'flow-circle-'+state" :style="ring" role="img" :aria-label="description" :title="description"><span aria-hidden="true">{{label}}</span></span>
</template>
<style scoped>
.flow-circle{display:inline-flex;align-items:center;justify-content:center;flex-shrink:0;width:27px;height:27px;border-radius:50%;background:#dce2d7;vertical-align:middle;font-variant-numeric:tabular-nums}
.flow-circle>span{display:flex;align-items:center;justify-content:center;width:19px;height:19px;border-radius:50%;background:#fff;font-size:10px;font-weight:650;line-height:1;color:#7c8972}
.flow-circle-buy>span{color:#b94a42}.flow-circle-sell>span{color:#307457}.flow-circle-pending{background:transparent;border:1.5px dashed #a6b39b}.flow-circle-pending>span{background:transparent;font-size:9px}
</style>
