export type HistoryModel = {as_of?: string | null, trades?: unknown[]}
export type StockHistoryContext = {models: Record<string, HistoryModel>, calendar: string[]}
export type DurationGroup = {samples: number, avgDays: number | null}

const number = (value: unknown): number | null =>
 typeof value === 'number' && Number.isFinite(value) ? value : null
const validDay = (value: unknown): value is string => typeof value === 'string'
 && /^\d{4}-\d{2}-\d{2}$/.test(value)
 && Number.isFinite(Date.parse(value))
 && new Date(value).toISOString().slice(0, 10) === value
const average = (values: number[]): DurationGroup => ({samples: values.length,
 avgDays: values.length ? values.reduce((a, b) => a + b, 0) / values.length : null})

/** Same-account, completed, costed trades known at the selected day's close.
 * Entry day is excluded; use the actual trading calendar for saved V3 trades.
 * Missing holding times never become zero or disappear from the win-rate base.
 */
export function stockHistory(model: HistoryModel | undefined, symbol: string,
 asOf: string | null | undefined, calendar: string[], modelKey: string) {
 const cutoff = validDay(asOf) && validDay(model?.as_of)
  ? (asOf < model.as_of ? asOf : model.as_of) : null
 const index = new Map([...new Set(calendar)].sort().map((day, i) => [day, i]))
 const winDays: number[] = [], lossDays: number[] = [], allDays: number[] = []
 let completed = 0, wins = 0, losses = 0, flat = 0, missingResult = 0
 if (cutoff) for (const value of model?.trades || []) {
  if (!value || typeof value !== 'object') continue
  const trade = value as Record<string, unknown>
  const tradeSymbol = trade.symbol ?? (modelKey === 'v5' ? '00663L' : null)
  if (tradeSymbol !== symbol || !validDay(trade.entry_date)
   || !validDay(trade.exit_date) || trade.exit_date > cutoff
   || trade.exit_date < trade.entry_date) continue
  const result = ['pnl', 'net_return_pct', 'return_pct', 'pnl_points']
   .map(key => number(trade[key])).find(value => value !== null)
  if (result == null) {missingResult++; continue}
  completed++
  if (result > 0) wins++
  else if (result < 0) losses++
  else flat++
  let duration = number(trade.holding_sessions)
  if (duration === null || duration < 0) {
   const entry = index.get(trade.entry_date), exit = index.get(trade.exit_date)
   duration = entry === undefined || exit === undefined ? null : exit - entry
  }
  if (duration === null || duration < 0) continue
  allDays.push(duration)
  if (result > 0) winDays.push(duration)
  else if (result < 0) lossDays.push(duration)
 }
 return {asOf: cutoff, completed, wins, losses, flat, missingResult,
  winRate: completed ? wins / completed * 100 : null,
  all: average(allDays), win: average(winDays), loss: average(lossDays)}
}
