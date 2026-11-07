export type Threshold={threshold:number;eligibleN:number;signals:number;correct:number;coverage:number|null;accuracy:number|null}
export type Metric={n:number;quantileN:number;flatN:number;brier:number|null;logLoss:number|null;thresholds:Threshold[];coverage80:number|null;inside80:number;pinballPoints:Record<string,number|null>}
export type Study={horizon:number;first:string;last:string;M0:Metric;nonoverlap:Metric;regimes:Record<string,Metric>;highVolatility:Metric;candidate?:Metric;nonoverlapCandidate?:Metric}
export type Target={horizon:number;targetDate:string;up:number;down:number;direction:'up'|'down'|'none';anchorClose:number;medianClose:number;medianChangePoints:number;q10Close:number;q90Close:number;q10ChangePoints:number;q90ChangePoints:number;dailySigmaPct:number;sampleN:number;model:string;methodVersion?:string;M0Up:number;modelLabel:string;modelSelectionReason:string;historical:Study;featuresUsed:string[];featuresNotUsed:string[];candidateUp:number|null;outcome?:{actualClose:number;actualUp:boolean|null}|null}
export type SlotStudy={horizon:number;clock:string;modelReady:boolean;M0:Metric;candidate:Metric|null;improvement:number|null;features:string[];featureTests:{feature:string;relativeImprovement:number|null;passed:boolean}[]}
export type Forecast={id:string;version:string;anchorDate:string;anchorClose:number;informationCutoff:string;savedAt:string;slot:string|null;role:string;roleLabel:string;qualified:boolean;countsFormal:boolean;usTimeRegime:string;targets:Target[];dataNote?:string;backtestBySlot?:{slot:string;horizons:SlotStudy[]}[];sameClockNightTrainingAvailable?:boolean;liveInputsUsedByM0?:boolean;forecastMode?:string;observation?:Observation|null}
export type EveningReport={version:string;status:string;readOnly:boolean;latest:Forecast|null;progress:{role:string;label:string;saved:number;horizons:({horizon:number}&Metric)[]}[];history:Forecast[];scoreNote?:string;sourceRequirement?:string}
export function validateEvening(d:any):EveningReport{
 if(d?.version!=='v6-evening-forecast-1.0.0'||d.readOnly!==true||!Array.isArray(d.history)||!Array.isArray(d.progress))throw Error('18／22預報版本不符')
 for(const f of [d.latest,...d.history].filter(Boolean)){
  if(!Number.isFinite(f.anchorClose)||f.anchorClose<=0||!Number.isFinite(Date.parse(f.savedAt))||!Number.isFinite(Date.parse(f.informationCutoff))||Date.parse(f.informationCutoff)>Date.parse(f.savedAt)||f.targets?.length!==3)throw Error('預報錨點或保存時間無效')
  for(const [i,t] of f.targets.entries()){
   if(t.horizon!==i+1||t.targetDate<=f.anchorDate||!Number.isFinite(t.up)||t.up<0||t.up>1||Math.abs(t.up+t.down-1)>1e-10||![t.medianClose,t.q10Close,t.q90Close,t.medianChangePoints].every(Number.isFinite)||!(0<t.q10Close&&t.q10Close<=t.medianClose&&t.medianClose<=t.q90Close)||Math.abs(t.medianClose-f.anchorClose-t.medianChangePoints)>1e-5)throw Error('機率與點數不可缺項或交叉')
   const direction=t.up===.5?'none':t.up>=.65?'up':t.down>=.65?'down':'none'
   if(t.direction!==direction||(t.up>.5&&t.medianClose<f.anchorClose-1e-5)||(t.up<.5&&t.medianClose>f.anchorClose+1e-5)||(t.q10Close>f.anchorClose+1e-5&&t.up<.9)||(t.q90Close<f.anchorClose-1e-5&&t.up>.1))throw Error('方向與分布不一致')
  }
 }
 return d
}
export function signal(m:Metric,threshold=.65){return m.thresholds.find(r=>Math.abs(r.threshold-threshold)<1e-9)}

export type Observation={methodVersion:string;status:string;dataNote:string;inputs:{nightMissing:boolean;night:null|{contract:string;price:number;day_close:number;change_day_pct:number;quote_at:string};sigmaPct:number;overseasDisagreement:number;nightNote:string;overseasNote:string;overseas:{label:string;change_pct:number;quote_at:string}[]};seasonality:{month:number;asOf:string;currentMonthPct:number|null;currentYtdPct:number|null;monthDeviationPp:number|null;monthPercentile:number|null;monthlyHistoryFirst:string|null;lastCompletedMonth:string|null;comparisonNote:string;monthStats:SeasonStats;annualStats:SeasonStats};yearAnalogs:{cases:{year:number;rank:number;distance:number;matchedThrough:string;mtdPct:number;ytdPct:number;outcomes:{sessions:number;date:string;returnPct:number}[];path:{day:number;pct:number}[]}[];description?:string;outcomeNote?:string}}
export type SeasonStats={n:number;upRate:number|null;meanPct:number|null;minPct:number|null;maxPct:number|null;stdPct:number|null}
