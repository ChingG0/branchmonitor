<script setup lang="ts">
import {computed,ref} from 'vue'
const props=defineProps<{group?:string}>()
const version=ref(0)
const src=computed(()=>'/sector-flow/board?'+new URLSearchParams({group:props.group||'',v:String(version.value)}))
</script>
<template>
 <section class="sector-flow" aria-label="族群流向看板">
  <div class="sector-help"><span>沿用上下游與族群排行看板 · 更新只處理族群資料</span><button @click="version++">重新載入看板</button><a :href="src" target="_blank" rel="noopener">獨立視窗 ↗</a></div>
  <iframe :key="src" :src="src" title="族群流向：每日排行、上下游與個股明細" class="sector-frame"/>
 </section>
</template>
<style scoped>
.sector-help{display:flex;align-items:center;gap:16px;flex-wrap:wrap;font-size:.76rem;color:#819276;margin-bottom:12px}.sector-help span{margin-right:auto}.sector-help button,.sector-help a{color:#587345;text-decoration:underline}.sector-frame{display:block;width:100%;height:calc(100dvh - 170px);min-height:620px;border:0;border-radius:10px;background:#f4f6f4}
</style>
