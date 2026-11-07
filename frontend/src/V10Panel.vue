<script setup lang="ts">
import V1Panel from './V1Panel.vue'
import type {V10Model} from './v8Types'
defineProps<{model?:V10Model}>()
const stamp=(value?:string)=>value?new Intl.DateTimeFormat('zh-TW',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)):'—'
</script>

<template>
 <section class="manual-record">
  <div><strong>V10 手動 V1</strong><p>與 V3、V6 一起依對話更新 · 收盤資料 {{model?.as_of||'—'}} · 保存 {{stamp(model?.recorded_at)}} · 第 {{model?.revision||0}} 版</p></div>
  <p>{{model?.manual_method}}</p>
  <details v-if="model?.review"><summary>本次更新說明</summary><p class="analysis">{{model.review.analysis}}</p><ul v-if="model.review.sources.length"><li v-for="source in model.review.sources" :key="source.url"><a :href="source.url" target="_blank" rel="noopener noreferrer">{{source.title}}</a></li></ul></details>
 </section>
 <V1Panel :model="model" manual/>
</template>

<style scoped>
.manual-record{padding:22px 24px;margin-bottom:16px;border:1px solid #dce5e1;border-radius:16px;background:#f6f9f7;color:#465b50}.manual-record strong{font-size:19px}.manual-record p{font-size:13px;line-height:1.8;margin:8px 0;color:#65796c}.manual-record details{margin-top:12px}.manual-record summary{cursor:pointer;font-weight:700;font-size:13px}.analysis{white-space:pre-line}a{color:#357b7a}li{margin:6px 0}
</style>
