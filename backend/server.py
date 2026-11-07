from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import business_cycle, margin_observations, margin_dashboard, margin_events, margin_analytics, margin_checker, margin_statistics, monthly_statistics, v6_comparison, alpha_beta, pe_river, db, service, sources, major, turnover, rs, leaders, etf_rs, market_monitor, broker_focus, sector_flow, strategies, full_update, block_trades, sync_audit, history_repair, daily_follow, active_etf, insider_changes, dividend_yields, warrants, v9_tx, v6_tx, v6_weather, v2, v1, v3, v10, v11


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if len(args)<2 or str(args[1]) not in ['200','304']:
            super().log_message(fmt,*args)

    def send(self, body, status=200, content_type='application/json; charset=utf-8', csp=None):
        raw = json.dumps(body,ensure_ascii=False,allow_nan=False).encode() if not isinstance(body,bytes) else body
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store' if content_type.startswith('application/json') else 'no-cache')
        self.send_header('Content-Security-Policy',csp or "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def local_request(self):
        hostname = urlparse('//'+self.headers.get('Host','')).hostname
        return hostname in ['127.0.0.1','localhost','::1']

    def do_GET(self):
        if not self.local_request():
            return self.send({'error':'僅接受本機請求'},403)
        parsed = urlparse(self.path)
        if os.environ.get('BRANCH_MONITOR_CLOUD')=='1':
            try:
                from scripts.cloud_runtime import instance
                instance().route(parsed.path)
            except (OSError,ValueError) as error:
                return self.send({'error':'雲端資料讀取失敗：'+str(error)},503)
        query = {k:v[0] for k,v in parse_qs(parsed.query).items()}
        try:
            if parsed.path == '/api/margin-statistics/risk':
                return self.send(margin_observations.report(query.get('date'),query.get('category','listed_stock'),query.get('market','TWSE')))
            if parsed.path == '/api/margin-statistics/overview':
                return self.send(margin_dashboard.report(query.get('date')))
            if parsed.path == '/api/margin-statistics/events':
                return self.send(margin_events.report(query.get('date')))
            if parsed.path == '/api/margin-statistics/analytics':
                return self.send(margin_analytics.report(query.get('date')))
            if parsed.path == '/api/margin-statistics/checker':
                return self.send(margin_checker.report(query.get('symbol'),query.get('date')))
            if parsed.path == '/api/margin-statistics':
                return self.send(margin_statistics.report(query.get('market','TWSE'),query.get('date'),query.get('search',''),int(query.get('page','1'))))
            if parsed.path == '/api/business-cycle':
                return self.send(business_cycle.report())
            if parsed.path == '/api/monthly-statistics':
                return self.send(monthly_statistics.report())
            if parsed.path == '/api/alpha-beta/detail':
                return self.send(alpha_beta.detail(query.get('symbol',''),query.get('benchmark','TAIEX_TR')))
            if parsed.path == '/api/alpha-beta':
                return self.send(alpha_beta.report())
            if parsed.path == '/api/health':
                return self.send({'status':'ok','app':'branch-monitor','version':'1.0.0','pid':os.getpid(),'directory':str(db.ROOT),'data_directory':str(db.DATA.resolve()),'storage':__import__('scripts.cloud_runtime',fromlist=['instance']).instance().status() if os.environ.get('BRANCH_MONITOR_CLOUD')=='1' else {'mode':'local'}})
            if parsed.path == '/api/update-all':
                return self.send(full_update.status())
            if parsed.path == '/api/block-trades':
                return self.send(block_trades.report(query.get('date')))
            if parsed.path == '/api/active-etf':
                return self.send(active_etf.report(query.get('date')))
            if parsed.path == '/api/insider-changes':
                return self.send(insider_changes.report())
            if parsed.path == '/api/dividend-yields':
                return self.send(dividend_yields.report())
            if parsed.path == '/api/warrants':
                return self.send(warrants.report(query.get('query'), query.get('date')))
            if parsed.path == '/api/daily-follow':
                return self.send(daily_follow.report(query.get('date')))
            if parsed.path == '/api/v1':
                return self.send(v1.report())
            if parsed.path == '/api/v10':
                return self.send(v10.report())
            if parsed.path == '/api/v11':
                return self.send(v11.report())
            if parsed.path == '/api/v3':
                return self.send(v3.report())
            if parsed.path == '/api/v2':
                return self.send(v2.report())
            if parsed.path == '/api/v9':
                return self.send(v9_tx.report())
            if parsed.path == '/api/v6':
                return self.send(v6_weather.report().get('short_simulation') or {
                    'key':'v6','strategy_key':'v6-2','status':'missing','curve':[],
                    'warning':'尚未由對話保存V6-2主模擬。'})
            if parsed.path == '/api/v6-comparison':
                return self.send(v6_comparison.report(query.get('id')))
            if parsed.path == '/api/v6-weather':
                return self.send(v6_weather.report(query.get('id')))
            if parsed.path == '/api/v6-allocation-study':
                from . import v6_allocation
                return self.send(v6_allocation.read_study())
            if parsed.path == '/api/strategies':
                return self.send(strategies.report(query.get('id')))
            if parsed.path == '/api/status':
                return self.send(service.job_status())
            if parsed.path == '/api/sync-audit':
                limit=int(query.get('limit','100'))
                return self.send(sync_audit.report(limit,query.get('daily_tier'),query.get('repair_tier')))
            if parsed.path == '/api/history-repair':
                limit=int(query.get('limit','100'))
                return self.send(history_repair.report(limit,query.get('status'),query.get('repair_tier')))
            if parsed.path == '/sector-flow/board':
                page=sector_flow.board()
                return self.send(page,content_type='text/html; charset=utf-8',csp=sector_flow.board_policy(page))
            if parsed.path == '/api/sector-flow/status':
                return self.send(sector_flow.status())
            if parsed.path == '/api/broker-focus':
                return self.send(broker_focus.report(query.get('date'),query.get('turnover_date')))
            if parsed.path == '/api/market-pe':
                return self.send(pe_river.report())
            if parsed.path == '/api/market-monitor':
                return self.send(market_monitor.report())
            if parsed.path == '/api/main-watch':
                return self.send(major.report(query.get('date')))
            if parsed.path == '/api/rs':
                return self.send(rs.report(query.get('period','20')))
            if parsed.path == '/api/etf-rs':
                return self.send(etf_rs.report(query.get('period','20')))
            if parsed.path == '/api/leaders':
                return self.send(leaders.report(query.get('date')))
            if parsed.path == '/api/main-watch/brokers':
                return self.send(turnover.status(query.get('symbol',''),query.get('date','')))
            if parsed.path == '/api/catalog':
                return self.send(sources.branch_catalog())
            if parsed.path in ['/api/dashboard','/api/export']:
                result = service.dashboard(query.get('broker','9300'),query.get('branch','9339'),query.get('date'))
                if parsed.path == '/api/dashboard':
                    return self.send(result)
                table = query.get('table','daily')
                if table not in ['daily','positions','closed','signals','cycles','stock_stats','streak_buys','fresh_buys']:
                    raise ValueError('無效匯出項目')
                rows = result[table]
                buffer = io.StringIO()
                if rows:
                    writer = csv.DictWriter(buffer,fieldnames=list(rows[0]))
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({k:("'"+v if isinstance(v,str) and v[:1] in '=+-@' else v) for k,v in row.items()})
                return self.send(buffer.getvalue().encode('utf-8-sig'),content_type='text/csv; charset=utf-8')
            if parsed.path == '/api/stock':
                return self.send(service.stock_detail(query.get('broker','9300'),query.get('branch','9339'),query.get('symbol',''),query.get('date')))
            if parsed.path.startswith('/api/'):
                return self.send({'error':'找不到 API'},404)
            dist = (db.ROOT/'dist').resolve()
            path = (dist/parsed.path.lstrip('/')).resolve()
            if not path.is_relative_to(dist):
                return self.send({'error':'無效路徑'},400)
            if not path.is_file():
                path = dist/'index.html'
            if not path.exists():
                return self.send({'error':'請先執行 npm run build'},503)
            return self.send(path.read_bytes(),content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        except (ValueError,sources.SourceError) as error:
            self.send({'error':str(error)},400)
        except (BrokenPipeError,ConnectionResetError):
            pass
        except Exception as error:
            self.send({'error':f'本機處理失敗：{type(error).__name__}'},500)

    def do_POST(self):
        if not self.local_request():
            return self.send({'error':'僅接受本機請求'},403)
        origin = self.headers.get('Origin')
        if origin and origin not in [f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}','http://127.0.0.1:5178','http://localhost:5178']:
            return self.send({'error':'不接受外部網站寫入'},403)
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.send({'error':'需要 application/json'},415)
        try:
            length = int(self.headers.get('Content-Length',0))
            if not 0 < length <= 16384:
                raise ValueError('請求大小不符')
            values = json.loads(self.rfile.read(length))
            if not isinstance(values,dict):
                raise ValueError('請求需為 JSON 物件')
            if self.path=='/api/strategies/refresh':
                return self.send({'status':'retired','message':'ETF滾動存股規劃已停止更新；股票配置改隨V6對話更新。'},410)
            if self.path=='/api/daily-follow/generate':
                if values.get('all'):
                    daily_follow.generate_all()
                    return self.send(daily_follow.report())
                daily_follow.generate(values.get('date'),values.get('overwrite',False))
                return self.send(daily_follow.report(values.get('date')))
            if self.path=='/api/v9/refresh':
                return self.send(v9_tx.refresh())
            if self.path=='/api/update-all':
                return self.send(full_update.start(),202)
            if self.path=='/api/update-module':
                return self.send(full_update.retry(str(values.get('name',''))),202)
            if self.path=='/api/block-trades/refresh':
                return self.send(block_trades.refresh(values.get('date')),202)
            if self.path=='/api/active-etf/refresh':
                return self.send(active_etf.refresh(values.get('date')),202)
            if self.path=='/api/insider-changes/refresh':
                return self.send(insider_changes.refresh(values.get('force',False)),202)
            if self.path=='/api/alpha-beta/refresh':
                return self.send(alpha_beta.refresh(),202)
            if self.path=='/api/dividend-yields/refresh':
                return self.send(dividend_yields.refresh(),202)
            if self.path=='/api/warrants/refresh':
                return self.send(warrants.refresh(values.get('query')),202)
            if self.path=='/api/warrants/lookup':
                return self.send(warrants.lookup(values.get('query')),200)
            if self.path=='/api/warrants/enrich':
                return self.send(warrants.enrich_stock(values.get('query'), values.get('limit',20)),200)
            if self.path=='/api/strategies/settings':
                return self.send({'status':'retired','message':'ETF滾動存股規劃已停用。'},410)
            if self.path=='/api/main-watch/refresh':
                return self.send(major.start_refresh(),202)
            if self.path=='/api/broker-focus/refresh':
                return self.send(broker_focus.refresh(),202)
            if self.path=='/api/broker-focus/tag':
                raise ValueError('族群已統一使用族群流向字典，不再另存自訂標籤')
            if self.path=='/api/sector-flow/refresh':
                return self.send(sector_flow.refresh(values.get('force',False)),202)
            if self.path=='/api/business-cycle/refresh':
                return self.send(business_cycle.refresh(),202)
            if self.path=='/api/monthly-statistics/refresh':
                return self.send(monthly_statistics.refresh(),202)
            if self.path=='/api/market-monitor/refresh':
                return self.send(market_monitor.refresh(values.get('card')),202)
            if self.path=='/api/rs/refresh':
                return self.send(rs.refresh(),202)
            if self.path=='/api/etf-rs/refresh':
                return self.send(etf_rs.refresh(),202)
            if self.path=='/api/leaders/refresh':
                return self.send(leaders.report(values.get('date'),refresh=True))
            if self.path=='/api/main-watch/brokers':
                return self.send(turnover.start(values.get('symbol',''),values.get('date','')),202)
            if self.path=='/api/sync':
                return self.send(service.sync(values.get('days',45),targets=values.get('targets'),force=values.get('force',False),start_date=values.get('start_date')),202)
            if self.path=='/api/sync-audit/run':
                return self.send(sync_audit.run(values.get('recent_sessions',20)))
            if self.path=='/api/history-repair/plan':
                return self.send(history_repair.plan(values.get('through_date'),values.get('refresh_audit',True)))
            if self.path=='/api/history-repair/run':
                return self.send(history_repair.start(values.get('limit',50),values.get('resume',True)),202)
            if self.path=='/api/history-repair/retry':
                return self.send(history_repair.requeue(values.get('limit',500),values.get('force',False)))
            if self.path=='/api/branches':
                return self.send(service.add_branch(str(values.get('broker','')),str(values.get('branch',''))))
            if self.path=='/api/watchlist':
                return self.send({'branches':service.replace_watchlist(values.get('branches'))})
            if self.path=='/api/settings':
                allowed = {}
                if 'schedule_enabled' in values:
                    if not isinstance(values['schedule_enabled'],bool):
                        raise ValueError('自動更新需為布林值')
                    allowed['schedule_enabled']=values['schedule_enabled']
                if 'schedule_time' in values:
                    if not re.fullmatch(r'(?:1[4-9]|2[0-3]):[0-5]\d',str(values['schedule_time'])):
                        raise ValueError('更新時間限盤後 14:00～23:59')
                    allowed['schedule_time']=values['schedule_time']
                if 'lookback_days' in values:
                    n=values['lookback_days']
                    if not isinstance(n,int) or isinstance(n,bool) or not 7<=n<=180:
                        raise ValueError('回補範圍為 7～180 個日曆日')
                    allowed['lookback_days']=n
                if 'history_start_date' in values:
                    allowed['history_start_date']=service.validate_history_start(values['history_start_date'])
                db.update_settings(allowed)
                return self.send(db.settings())
            self.send({'error':'找不到 API'},404)
        except (ValueError,sources.SourceError) as error:
            self.send({'error':str(error)},400)
        except Exception as error:
            self.send({'error':f'本機處理失敗：{type(error).__name__}'},500)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--sync',action='store_true',help='開啟時立即同步')
    parser.add_argument('--no-scheduler',action='store_true',help='只讀啟動，不執行背景行情排程')
    args=parser.parse_args()
    if (db.ROOT/'cloud-runtime.local.json').is_file() and os.environ.get('BRANCH_MONITOR_CLOUD')!='1':
        raise SystemExit('請使用啟動雲端監控.command，先驗證Drive資料再啟動3001')
    db.initialize()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    if not args.no_scheduler:threading.Thread(target=service.scheduler,daemon=True).start()
    if args.sync:
        service.sync()
    print(f'分點觀測站 http://127.0.0.1:{args.port}  (Ctrl+C 停止)',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__=='__main__':
    main()
