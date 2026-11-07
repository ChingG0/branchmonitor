export type RollingRow={horizon:number;date:string;cutoff:string;latestTrainingOutcome:string;trainingN:number;fixed:number;rolling:number;up:boolean}
type Metric={n:number;mean_forecast:number;actual_up:number;bias_pp:number;brier:number;log_loss:number}
export type ForecastAudit={version:string;role:string;all:{n:number;mean_p:number;actual_up:number;accuracy:number;brier:number};last60:{brier:number};directions:Record<string,{n:number;matches?:number;fraction:number}>;calibration:{low:number;high:number;n:number;mean_p:number;actual_up:number}[];rollingComparison:{horizon:number;rolling_common_dates:{first:string;last:string;fixed:Metric;rolling:Metric;relative_brier_change:number;relative_log_loss_change:number}}[];rollingRows:RollingRow[];rounded50:{date:string;p:number;y:boolean}[];exact50N:number;calibrationAdopted:boolean}
export type NightConditions={version:string;role:string;isForecast:false;validatedForThisTime:false;computedAt:string;sourceSavedAt:string;quoteAt:string;anchorDate:string;anchorClose:number;targetDate:string;contract:string;dayClose:number;fixedHigh:number;fixedLow:number;dailySigma60Pct:number;volatility:Record<string,number|string>;rows:{nightCloseAssumed:number;label:string;up:number;down:number}[];driftCorrectionAdopted:boolean;intervalStatus:string;note:string}
export type Presentation={version:string;isForecast:boolean;readOnly:boolean;conditions?:NightConditions|null;conditionsError?:string;audit?:ForecastAudit|null;auditError?:string}
export function validatePresentation(value:any):Presentation{
 if(value?.version!=='v6-forecast-presentation-1.0.0'||value.isForecast!==false||value.readOnly!==true)throw Error('預報檢查資料版本不符')
 const c=value.conditions
 if(c&&(c.role!=='conditional_sensitivity_not_forecast'||c.isForecast!==false||c.validatedForThisTime!==false||c.driftCorrectionAdopted!==false||c.intervalStatus!=='withheld_unvalidated_intranight'||!Array.isArray(c.rows)||!c.rows.length||c.rows.some((r:any)=>!Number.isFinite(r.nightCloseAssumed)||r.nightCloseAssumed<c.fixedLow||r.nightCloseAssumed>c.fixedHigh||!Number.isFinite(r.up)||r.up<.01||r.up>.99||Math.abs(r.up+r.down-1)>1e-10)))throw Error('夜盤條件研究資料不符')
 const a=value.audit
 if(a&&(a.calibrationAdopted!==false||!Array.isArray(a.rollingRows)||a.rollingRows.some((r:any)=>!Number.isFinite(r.fixed)||!Number.isFinite(r.rolling)||Date.parse(r.latestTrainingOutcome)>=Date.parse(r.cutoff))))throw Error('校準研究含未成熟答案或資格不符')
 return value
}
