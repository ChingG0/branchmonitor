"""Dated, representative USD bond G-spread estimates. No synthetic issuer quotes."""
from __future__ import annotations
import calendar
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from bs4 import BeautifulSoup
import requests

TZ = ZoneInfo('Asia/Taipei')
TREASURY = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value={year}'
TREASURY_PAGE = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve'
BROKER = 'https://www.comdirect.de/inf/anleihen/'
VERSION = 1
# Pin one security and venue per issuer so daily changes do not reflect a new bond.
BONDS = [
    dict(ticker='AVGO',name='博通',issuer='Broadcom',isin='US11135FCZ27',coupon=4.3,maturity='2031-01-15',first_coupon='2026-07-15'),
    dict(ticker='AAPL',name='蘋果',issuer='Apple',isin='US037833EJ59',coupon=1.7,maturity='2031-08-05',first_coupon='2022-02-05'),
    dict(ticker='TSLA',name='特斯拉',issuer='Tesla',isin=None,coupon=None,maturity=None,first_coupon=None),
    dict(ticker='MSFT',name='微軟',issuer='Microsoft',isin='US594918BC73',coupon=3.5,maturity='2035-02-12',first_coupon='2015-08-12'),
    dict(ticker='META',name='Meta',issuer='Meta Platforms',isin='US30303MAF95',coupon=4.55,maturity='2031-05-15',first_coupon='2026-11-15'),
    dict(ticker='AMZN',name='亞馬遜',issuer='Amazon.com',isin='US023135EC69',coupon=4.8,maturity='2031-07-09',first_coupon='2027-01-09'),
    dict(ticker='GOOGL',name='Alphabet',issuer='Alphabet',isin='US02079KAW71',coupon=4.1,maturity='2030-11-15',first_coupon='2026-05-15'),
    dict(ticker='NVDA',name='NVIDIA',issuer='NVIDIA',isin='US67066GAR56',coupon=4.5,maturity='2031-06-15',first_coupon='2026-12-15'),
    dict(ticker='MU',name='美光',issuer='Micron Technology',isin='US595112BS19',coupon=2.703,maturity='2032-04-15',first_coupon='2022-04-15'),
    dict(ticker='TSM',name='台積電',issuer='TSMC Global',isin='USG91139AH14',coupon=2.25,maturity='2031-04-23',first_coupon='2021-10-23',venue='Frankfurt',security_note='TSMC Global 發行、台積電保證的優先無擔保美元債。'),
    dict(ticker='ASML',name='ASML',issuer='ASML Holding',isin='XS2473687106',coupon=2.25,maturity='2032-05-17',first_coupon='2023-05-17',currency='EUR',frequency='Jahr',price_only=True,security_note='歐元綠色債券；先追蹤債券價格，不與美元美債混算利差。'),
    dict(ticker='JPM',name='JPMorgan',issuer='JPMorgan Chase',isin='US46625HNJ58',coupon=4.25,maturity='2027-10-01',first_coupon='2016-04-01',security_note='固定利率次順位債，剩餘期限較短；不能視為5年優先債或5年CDS，請觀察自身變化。'),
]
TENORS = {'BC_6MONTH':.5,'BC_1YEAR':1.,'BC_2YEAR':2.,'BC_3YEAR':3.,'BC_5YEAR':5.,'BC_7YEAR':7.,'BC_10YEAR':10.,'BC_20YEAR':20.,'BC_30YEAR':30.}
METHOD = '固定代表債券與交易場所；美元債以報價日為估值日（T+0）、30/360應計息與半年複利估算YTM。按剩餘年期線性內插同一日期的美國財政部公債曲線，再以（YTM%－公債%）×100換成bp。這是G利差估算，未調整提前贖回選擇權；報價非保證成交，兩地取價時刻不同。ASML歐元債僅顯示價格。'


def finite(value):
    value=float(value)
    if not math.isfinite(value):raise ValueError('債券數值無效')
    return value


def german(value):
    match=re.search(r'-?\d[\d.]*(?:,\d+)?',value)
    if not match:raise ValueError('債券報價數值缺漏')
    return finite(match[0].replace('.','').replace(',','.'))


def parse_treasury(raw,today=None):
    today=today or datetime.now(ZoneInfo('America/New_York')).date()
    root=ET.fromstring(raw);result={}
    ns={'m':'http://schemas.microsoft.com/ado/2007/08/dataservices/metadata'}
    for element in root.findall('.//m:properties',ns):
        fields={child.tag.split('}')[-1]:child.text for child in element}
        day=date.fromisoformat(fields.get('NEW_DATE','')[:10])
        if day>today:continue
        curve={years:finite(fields[key]) for key,years in TENORS.items() if fields.get(key)}
        if len(curve)<5 or any(not -5<=v<=30 for v in curve.values()):raise ValueError('美國公債曲線欄位不完整')
        if day.isoformat() in result and result[day.isoformat()]!=curve:raise ValueError('美國公債曲線重複日期不一致')
        result[day.isoformat()]=curve
    if not result:raise ValueError('無可核對日期的美國公債曲線')
    return result


def interpolate(curve,years):
    points=sorted((float(t),finite(y)) for t,y in curve.items())
    if not points or years<points[0][0] or years>points[-1][0]:raise ValueError('剩餘年期超出美債曲線範圍')
    for term,value in points:
        if abs(term-years)<1e-10:return value,[term,term]
    for (a,ya),(b,yb) in zip(points,points[1:]):
        if a<years<b:return ya+(yb-ya)*(years-a)/(b-a),[a,b]
    raise ValueError('無法內插美債曲線')


def shift_month(day,months):
    count=day.year*12+day.month-1+months;year,month=divmod(count,12);month+=1
    return date(year,month,min(day.day,calendar.monthrange(year,month)[1]))


def days360(start,end):
    # US 30/360, including February month-end rules.
    d1,d2=start.day,end.day
    feb1=start.month==2 and d1==calendar.monthrange(start.year,2)[1]
    feb2=end.month==2 and d2==calendar.monthrange(end.year,2)[1]
    if feb1:d1=30
    if feb1 and feb2:d2=30
    if d1==31:d1=30
    if d2==31 and d1==30:d2=30
    return (end.year-start.year)*360+(end.month-start.month)*30+d2-d1


def estimate_ytm(clean,coupon,maturity,issued,first_coupon,valuation):
    """Semiannual nominal yield on a clearly labelled T+0 valuation basis."""
    maturity,issued,first_coupon,valuation=map(date.fromisoformat,(maturity,issued,first_coupon,valuation))
    if not issued<=valuation<maturity or not issued<first_coupon<=maturity or not 0<clean<250 or not 0<coupon<25:
        raise ValueError('債券估值條件不適用')
    payments=[];pay=maturity
    while pay>=first_coupon:
        payments.append(pay);pay=shift_month(pay,-6)
        if len(payments)>100:raise ValueError('債券付息日期過長')
    payments.reverse()
    if not payments or payments[0]!=first_coupon:raise ValueError('付息日與到期日無法對齊')
    future=[p for p in payments if p>valuation]
    previous=max((p for p in payments if p<=valuation),default=issued)
    accrued=coupon*days360(previous,valuation)/360
    dirty=clean+accrued
    cashflows=[]
    for i,pay in enumerate(future):
        amount=coupon*days360(issued,first_coupon)/360 if pay==first_coupon else coupon/2
        if pay==maturity:amount+=100
        cashflows.append((days360(valuation,pay)/180,amount))
    def pv(y):return sum(amount/(1+y/200)**period for period,amount in cashflows)
    lo,hi=-10.,100.
    if not pv(hi)<dirty<pv(lo):raise ValueError('無法由報價求得合理YTM')
    for _ in range(100):
        mid=(lo+hi)/2
        if pv(mid)>dirty:lo=mid
        else:hi=mid
    return (lo+hi)/2,accrued


def parse_quote(raw,bond,now=None):
    now=now or datetime.now(TZ)
    page=BeautifulSoup(raw,'html.parser');details={}
    for tr in page.select('tr'):
        cells=tr.find_all(['th','td'],recursive=False)
        if len(cells)==2:details.setdefault(cells[0].get_text(' ',strip=True),cells[1].get_text(' ',strip=True))
    if details.get('ISIN')!=bond['isin'] or bond['issuer'].lower() not in details.get('Emittent','').lower():raise ValueError('債券ISIN或發行人不符')
    if details.get('Währung')!=bond.get('currency','USD') or details.get('Kupon-Art')!='Fest' or details.get('Zinszahlung')!=bond.get('frequency','6 Monate'):raise ValueError('債券幣別、固定票息或付息頻率不符')
    maturity=datetime.strptime(details['Fälligkeit'],'%d.%m.%Y').date().isoformat()
    issued=datetime.strptime(details['Ausgabedatum'],'%d.%m.%Y').date().isoformat()
    if maturity!=bond['maturity'] or abs(german(details['Nominalzinssatz'])-bond['coupon'])>1e-7:raise ValueError('債券付息條件變更，需重新核對')
    quotes=[];venue=bond.get('venue','gettex')
    notation=next((o.get('value') for o in page.select('#marketSelect option') if o.get_text(strip=True)==venue),None)
    for table in page.select('table'):
        headers=[x.get_text(' ',strip=True) for x in table.select('thead th')]
        if not all(label in headers for label in ['Börse','Aktuell','Datum','Zeit']):continue
        for tr in table.select('tbody tr'):
            # Responsive summary cells precede the actual direct td columns.
            cells=[c.get_text(' ',strip=True) for c in tr.find_all(['th','td'],recursive=False)]
            for i,cell in enumerate(cells):
                if cell!=venue or len(cells)<i+4:continue
                if 'für Kunden' in cells[i+1]:continue
                stamp=datetime.strptime(cells[i+2]+' '+cells[i+3],'%d.%m.%y %H:%M').replace(tzinfo=ZoneInfo('Europe/Berlin'))
                if stamp>now:raise ValueError('債券報價時間晚於目前時間')
                price=german(cells[i+1])
                if not 0<price<250:raise ValueError('債券價格無效')
                quotes.append(dict(as_of=stamp.date().isoformat(),quote_at=stamp.isoformat(),clean_price=price,venue=venue,issued=issued,notation=notation))
    if not quotes:raise ValueError(venue+'無可核對日期的公開報價')
    return max(quotes,key=lambda r:r['quote_at'])


def history_url(notation):
    if not str(notation).isdigit():raise ValueError('歷史報價識別碼不符')
    params=urlencode(dict(WIDTH=1200,HEIGHT=500,TYPE='MOUNTAIN',TIME_SPAN='3M',AXIS_SCALE='lin',PRICE_MAP=1,DATA_SCALE='abs',LNOTATIONS=notation,WITH_EARNINGS=0))
    return 'https://www.comdirect.de/inf/snippet$lsg.chart_map_middle.ajax?'+urlencode(dict(CHART_URL_PARAMS=params,REFERRER_PAGE_ID='lsg.bond.detail.chart'))


def parse_price_history(raw,quote):
    root=ET.fromstring(raw);element=root.find('.//replaceelement[@id="chartMapWrapper"]')
    node=BeautifulSoup(element.text if element is not None else '', 'html.parser').select_one('#chartData')
    if node is None:raise ValueError('歷史報價圖表格式不符')
    data=json.loads(node.get_text());points={}
    for item in data.get('chartQuoteInfo',[]):
        if not isinstance(item,dict):continue
        day=datetime.strptime(item['dateTime'],'%d.%m.%Y').date().isoformat()
        value=german(item['text'])
        if day>quote['as_of'] or day<quote['issued']:continue
        if not 0<value<250:raise ValueError('歷史債券價格無效')
        if day in points and abs(points[day]-value)>1e-8:raise ValueError('同日歷史報價不一致')
        points[day]=value
    # Require the chart and the chosen venue's dated quote to agree. Never splice venues.
    if data.get('datetimeLast')!=date.fromisoformat(quote['as_of']).strftime('%d.%m.%Y') or abs(german(data.get('textLast',''))-quote['clean_price'])>.0011:
        raise ValueError('歷史圖表與指定場所最新報價不一致')
    points[quote['as_of']]=quote['clean_price']
    return [dict(date=d,value=v) for d,v in sorted(points.items())][-60:]


def cds_probability(spread_bp,recovery=.4,years=5):
    """Illustrative flat-hazard risk-neutral approximation, for actual CDS inputs only."""
    spread_bp,recovery,years=map(finite,(spread_bp,recovery,years))
    if spread_bp<0 or not 0<=recovery<1 or years<=0:raise ValueError('CDS試算條件無效')
    return -math.expm1(-spread_bp/10000/(1-recovery)*years)*100


def merge_history(row,old):
    if not old or old.get('isin')!=row['isin'] or old.get('venue')!=row.get('venue') or old.get('method_version')!=VERSION:
        history=[]
    else:
        if old.get('as_of','')>row['as_of'] or (old.get('as_of')==row['as_of'] and old.get('quote_at','')>row['quote_at']):raise ValueError('債券來源日期或時間倒退')
        history=old.get('history',[])
    points={p['date']:p for p in row.get('history',[]) if p['date']<=row['as_of']}
    points.update({p['date']:p for p in history if p['date']<=row['as_of']})
    points[row['as_of']]=dict(date=row['as_of'],value=row['spread_bp'])
    row['history']=sorted(points.values(),key=lambda p:p['date'])[-60:]
    prior=[p for p in row['history'] if p['date']<row['as_of']]
    row['previous']=prior[-1] if prior else None
    row['change_bp']=round(row['spread_bp']-prior[-1]['value'],2) if prior else None
    return row


def load(fetch,prior=None,now=None):
    now=now or datetime.now(TZ);prior=prior or {};old={r['ticker']:r for r in prior.get('rows',[])}
    # One curve request serves all seven dated bonds, including prior year prices.
    curves=parse_treasury(fetch(TREASURY.format(year=now.year)),now.astimezone(ZoneInfo('America/New_York')).date())
    def one(bond):
        base={**bond,'source_url':BROKER+bond['isin'] if bond['isin'] else 'https://ir.tesla.com/sec-filings','method_version':VERSION}
        if not bond['isin']:
            return {**base,'status':'unavailable','as_of':None,'spread_bp':None,'history':[],
                    'note':'待確認可比較且有公開報價的一般美元公司債；資產證券化債務與銀行授信不混入本表。'}
        try:
            for attempt in range(2):
                try:
                    raw=fetch(base['source_url']);break
                except requests.RequestException:
                    if attempt:raise
            quote=parse_quote(raw,bond,now)
            day=quote['as_of']
            oldrow=old.get(bond['ticker'],{})
            price_history=[];history_error=None
            try:
                if quote.get('notation'):price_history=parse_price_history(fetch(history_url(quote['notation'])),quote)
            except Exception:
                history_error='歷史圖表暫不可用；保留已存來源日，不補假資料。'
            if oldrow.get('isin')==bond['isin'] and oldrow.get('venue')==quote['venue']:
                saved={p['date']:p for p in oldrow.get('price_history',[]) if p['date']<=day}
                saved.update({p['date']:p for p in price_history});price_history=sorted(saved.values(),key=lambda p:p['date'])
            price_history=[p for p in price_history if p['date']<day]+[dict(date=day,value=quote['clean_price'])]
            common=dict(base,**quote,price_history=price_history[-60:],history_error=history_error)
            if bond.get('price_only'):
                row=dict(common,status='price_only',spread_bp=None,history=[],note=bond['security_note'])
                if (now.date()-date.fromisoformat(day)).days>7:row.update(status='stale',error='報價超過7日，資料較舊')
                return row
            if day not in curves:
                matched=[p for p in price_history if p['date'] in curves]
                if not matched:raise ValueError('尚無與債券報價同日的美債曲線')
                latest_quote=dict(quote)
                previous=matched[-1];day=previous['date']
                quote=dict(quote,as_of=day,clean_price=previous['value'],quote_at='')
                price_history=[p for p in price_history if p['date']<=day]
                common.update(quote,price_history=price_history,latest_quote=latest_quote,quote_basis='歷史圖表日資料',
                              pairing_note=f"{latest_quote['as_of']}報價尚無同日美債曲線；使用{day}雙方都有資料的日期。")
            years=(date.fromisoformat(bond['maturity'])-date.fromisoformat(day)).days/365.25
            treasury,bracket=interpolate(curves[day],years)
            ytm,accrued=estimate_ytm(quote['clean_price'],bond['coupon'],bond['maturity'],quote['issued'],bond['first_coupon'],day)
            spread=(ytm-treasury)*100
            history=[]
            for point in price_history:
                if point['date'] not in curves:continue
                term=(date.fromisoformat(bond['maturity'])-date.fromisoformat(point['date'])).days/365.25
                benchmark,_=interpolate(curves[point['date']],term)
                y,_=estimate_ytm(point['value'],bond['coupon'],bond['maturity'],quote['issued'],bond['first_coupon'],point['date'])
                history.append(dict(date=point['date'],value=round((y-benchmark)*100,2)))
            row=dict(common,status='ready',yield_pct=round(ytm,6),treasury_pct=round(treasury,6),treasury_date=day,
                     treasury_bracket=bracket,remaining_years=round(years,3),spread_bp=round(spread,2),accrued=round(accrued,6),
                     history=history,note=bond.get('security_note') or ('代表債券年期較長；優先看自身變化，勿直接與其他公司排序。' if years>7 else '同日G利差估算；公開報價，未必有成交。'))
            row=merge_history(row,old.get(bond['ticker']))
            if (now.date()-date.fromisoformat(day)).days>7:row.update(status='stale',error='報價超過7日，資料較舊')
            return row
        except Exception as error:
            message=str(error) if isinstance(error,ValueError) else '報價來源暫不可用'
            if old.get(bond['ticker'],{}).get('spread_bp') is not None or old.get(bond['ticker'],{}).get('clean_price') is not None:
                return {**old[bond['ticker']],'status':'retained','error':message}
            return {**base,'status':'missing','as_of':None,'spread_bp':None,'history':[],'error':message}
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(one,BONDS))
    measured=[r for r in rows if r.get('spread_bp') is not None]
    if not measured:raise ValueError('債券利差來源尚未齊全，保留原紀錄')
    failed=[r['ticker'] for r in rows if r['status'] in {'missing','retained','stale'}]
    return dict(version=VERSION,as_of=max(r['as_of'] for r in measured),rows=rows,coverage=len(measured),quote_coverage=sum(r.get('clean_price') is not None for r in rows),total=len(rows),
                treasury_url=TREASURY_PAGE,method=METHOD,
                cds_reference=dict(years=5,recovery=.4,thresholds=[dict(bp=n,probability=round(cds_probability(n),2)) for n in [50,100,200,500]],source_url='https://www.chicagofed.org/publications/economic-perspectives/2023/4'),
                warning=('部分來源待補：'+ '、'.join(failed)) if failed else '',
                note='利差擴大表示該債券相對美債要求的收益增加，也受流動性與贖回條款影響；不直接代表股價將下跌。各列依自身報價日期比對，不使用聯邦基金利率。')
