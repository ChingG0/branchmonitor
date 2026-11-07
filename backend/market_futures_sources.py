"""Validated, explicitly dated TAIFEX CSV fallbacks for market observations."""
import csv,io,re
import requests
from . import db
BASE='https://www.taifex.com.tw/cht/3/'

def cash_day():
    with db.connect() as c:r=c.execute("SELECT MAX(date) FROM prices WHERE symbol='TAIEX'").fetchone()
    if not r or not r[0]:raise ValueError('缺少可核對的台股收盤日')
    return r[0]

def download(path,target,code):
    data={'queryStartDate':target.replace('-','/'),'queryEndDate':target.replace('-','/')}
    if path=='futContractsDateDown':data['commodityId']=code
    else:data.update(down_type='1',commodity_id=code,commodity_id2='')
    r=requests.post(BASE+path,data=data,headers={'User-Agent':'Mozilla/5.0'},timeout=(10,35));r.raise_for_status()
    return r.content

def rows(raw,required):
    try:reader=csv.DictReader(io.StringIO(raw.decode('cp950')))
    except UnicodeError:raise ValueError('期交所CSV編碼無法驗證') from None
    if not set(required)<=set(reader.fieldnames or []):raise ValueError('期交所CSV欄位改變')
    return list(reader)

def integer(x):
    s=str(x).replace(',','').strip()
    if not re.fullmatch(r'-?\d+',s):raise ValueError('期交所口數缺漏')
    return int(s)

def parse_positions(raw,target,commodity):
    a=rows(raw,['日期','商品名稱','身份別','多方未平倉口數','空方未平倉口數','多空未平倉口數淨額']);found={}
    for r in a:
        if str(r['日期']).replace('/','-')!=target or r['商品名稱'].strip()!=commodity:raise ValueError('期交所日期或商品不符')
        name=r['身份別'].strip()
        if name not in ['外資及陸資','投信','自營商'] or name in found:raise ValueError('期交所法人重複或缺漏')
        long,short,net=[integer(r[k]) for k in ['多方未平倉口數','空方未平倉口數','多空未平倉口數淨額']]
        if min(long,short)<0 or long-short!=net:raise ValueError('期交所法人未平倉核對失敗')
        found[name]=dict(name=name,long=long,short=short,net=net)
    if len(found)!=3:raise ValueError('期交所法人未平倉不足3類')
    return [found[n] for n in ['外資及陸資','投信','自營商']]

def futures(target):
    a=parse_positions(download('futContractsDateDown',target,'TXF'),target,'臺股期貨')
    a.append(dict(name='三大法人合計',**{k:sum(r[k] for r in a) for k in ['long','short','net']}))
    return dict(as_of=target,rows=a,unit='口',source_name='臺灣期貨交易所',source_url=BASE+'futContractsDate',note='期交所指定日期臺股期貨法人未平倉；三大法人合計不是全市場未平倉總量。')

def parse_retail(market_raw,institution_raw,target):
    a=rows(market_raw,['交易日期','契約','到期月份(週別)','未沖銷契約數','交易時段']);contracts={}
    for r in a:
        if r['契約'].strip()!='MTX' or r['交易時段'].strip()!='一般':continue
        exp=r['到期月份(週別)'].strip()
        if not re.fullmatch(r'\d{6}(?:W[1-5])?',exp):continue
        if r['交易日期'].replace('/','-')!=target:raise ValueError('小台日行情日期不符')
        if exp in contracts:raise ValueError('小台日盤契約重複')
        v=integer(r['未沖銷契約數'])
        if v<0:raise ValueError('小台未平倉不可為負')
        contracts[exp]=v
    total=sum(contracts.values());inst=parse_positions(institution_raw,target,'小型臺指期貨')
    long=total-sum(r['long'] for r in inst);short=total-sum(r['short'] for r in inst)
    if total<=0 or not 0<=long<=total or not 0<=short<=total:raise ValueError('小台散戶部位核對失敗')
    return dict(as_of=target,value=(long-short)/total*100,retail_long=long,retail_short=short,total_open_interest=total,unit='%',source_name='臺灣期貨交易所／非三大法人估計',source_url=BASE+'futContractsDate',note='小台日盤總未平倉扣三大法人多／空方部位；屬非三大法人估計，不能逐戶辨認散戶。正值偏多，風控反向解讀。')

def retail(target):
    return parse_retail(download('futDataDown',target,'MTX'),download('futContractsDateDown',target,'MXF'),target)
