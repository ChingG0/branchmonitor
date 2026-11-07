<script setup lang="ts">
defineProps<{name:string;size?:number}>()
const paths:Record<string,string>={
 document:'M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6',
 globe:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18',
 grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
 chart:'M4 3v17h17M8 14l4-5 4 3 5-7',
 settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4',
 refresh:'M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1',
 arrow:'M5 12h14m-6-6 6 6-6 6',
 external:'M14 3h7v7m0-7L10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5',
 download:'M12 3v12m-5-5 5 5 5-5M4 15v5h16v-5',
 clock:'M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
 search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
 plus:'M12 5v14M5 12h14',
 close:'M6 6l12 12M6 18 18 6',
 database:'M20 5c0 2-4 3-8 3S4 7 4 5s4-3 8-3 8 1 8 3ZM4 5v14c0 2 4 3 8 3s8-1 8-3V5M4 12c0 2 4 3 8 3s8-1 8-3',
 check:'m5 12 4 4L19 6',
 info:'M12 11v6m0-10v.1M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
 chevron:'m9 5 7 7-7 7',
 pulse:'M2 12h5l3-8 4 16 3-8h5',
 wallet:'M20 8H5a2 2 0 0 1 0-4h13v4M4 7v13h17V8M21 12h-6v4h6',
 star:'m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-2.9-5.6 2.9 1.1-6.2L3 9.6l6.2-.9Z',
}
</script>
<template><svg :width="size||20" :height="size||20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name]||paths.chart"/></svg></template>
