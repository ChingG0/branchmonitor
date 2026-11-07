"""Explicit CLI source refresh for dual V6; never imported by dashboard reads."""
import argparse,concurrent.futures,datetime,hashlib,json,shutil,tempfile
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
import requests
from . import v6_comparison as C,v6_tx

EXTRAS={'stock2330':'2330.TW','stock2317':'2317.TW','stock2454':'2454.TW','stock2308':'2308.TW','spy':'SPY','tlt':'TLT','gold':'GLD','dollar':'DX-Y.NYB','sox':'^SOX'}

def _refresh_positions(stage,cash):
    """Refresh the separate comparison cache too; Yahoo refresh cannot update TAIFEX."""
    import csv,io
    day=datetime.date.fromisoformat(cash);year=day.year;manifest=[]
    jobs=[(f'positions-{code}-{year}.csv','https://www.taifex.com.tw/cht/3/futContractsDateDown',
           {'queryStartDate':f'{year}/01/01','queryEndDate':day.strftime('%Y/%m/%d'),'commodityId':code}) for code in ('TXF','MXF')]
    jobs.append((f'mtx-month-{year}-{day.month:02d}.csv','https://www.taifex.com.tw/cht/3/futDataDown',
                 {'down_type':'1','commodity_id':'MTX','commodity_id2':'','queryStartDate':f'{year}/{day.month:02d}/01','queryEndDate':day.strftime('%Y/%m/%d')}))
    for name,url,params in jobs:
        fresh=v6_tx._request(url,params);path=stage/name
        base=path.read_bytes() if path.exists() else fresh[:fresh.find(b'\n')+1]
        data,count=v6_tx._append_csv(base,fresh,v6_tx.SEED_END)
        rows=list(csv.reader(io.StringIO(data.decode('cp950'))))
        dates=[r[0].strip().replace('/','-') for r in rows[1:] if r]
        if not dates or max(dates)!=cash:raise ValueError(name+'尚未更新到已核對台股收盤日')
        path.write_bytes(data)
        manifest.append({'key':name,'url':url,'params':params,'cutoff':cash,'rows':count,'observed_at':datetime.datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),'sha256':hashlib.sha256(data).hexdigest()})
    return manifest

def _seed_core_cache(stage,cash,us_cut,sessions,live=None):
    """Reuse verified core inputs when a separate fetch is denied; retain actual dates."""
    live=live or v6_tx.LIVE;manifest=[]
    for name in ('financial','electronics'):
        source=live/(name+'.json');path=stage/source.name
        if not source.exists():continue
        base=path.read_bytes() if path.exists() else source.read_bytes()
        data,_=v6_tx._merge_yahoo(base,source.read_bytes(),cash,sessions);path.write_bytes(data)
        manifest.append({'key':name+'-verified-cache','source':str(source),'cutoff':cash,'sha256':hashlib.sha256(data).hexdigest()})
    source=live/'cnn-2021.json';path=stage/source.name
    if source.exists():
        value=json.loads(path.read_text() if path.exists() else source.read_text());fresh=json.loads(source.read_text())
        def completed(row):return datetime.datetime.fromtimestamp(row['x']/1000,datetime.timezone.utc).date().isoformat()<=us_cut
        rows={row['x']:row for row in value['fear_and_greed_historical']['data'] if completed(row)}
        rows.update({row['x']:row for row in fresh['fear_and_greed_historical']['data'] if completed(row)})
        value['fear_and_greed_historical']['data']=[row for _,row in sorted(rows.items())];path.write_text(json.dumps(value))
        manifest.append({'key':'feargreed-verified-cache','source':str(source),'cutoff':us_cut,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return manifest

def refresh():
    now=datetime.datetime.now(ZoneInfo('Asia/Taipei'));raw,_=C.raw_input();cash=max(raw['index'])
    # Daily US bars accepted only after their next Taiwan08:00 availability.
    us_cut=(now-datetime.timedelta(days=1 if now.hour>=8 else 2)).date().isoformat()
    stage=Path(tempfile.mkdtemp(prefix='v6-dual-sources-',dir=C.SOURCES.parent))
    shutil.copytree(C.SOURCES if C.SOURCES.exists() else v6_tx.LIVE,stage,dirs_exist_ok=True)
    requests_list=[*( (n,s,False) for n,s in v6_tx._NAMES.items()),*( (n,s,True) for n,s in EXTRAS.items())]
    def get(job):
        name,symbol,full=job;url='https://query1.finance.yahoo.com/v8/finance/chart/'+quote(symbol,safe='')
        start=datetime.datetime(2020,1,1,tzinfo=datetime.timezone.utc) if full else now-datetime.timedelta(days=24)
        params={'period1':int(start.timestamp()),'period2':int(now.timestamp()),'interval':'1d','events':'div,splits'}
        response=requests.get(url,params=params,headers={'User-Agent':'Mozilla/5.0'},timeout=(10,45));response.raise_for_status()
        x=response.json()['chart']['result'][0];assert x['meta']['symbol']==symbol
        path=stage/(name+'.json');cut=cash if symbol.endswith('.TW') else us_cut
        if full:
            # Filter all quote arrays in a verified complete daily response.
            data=response.content
            data,_=v6_tx._merge_yahoo(data,data,cut,set(raw['index']) if symbol.endswith('.TW') else None)
        else:data,_=v6_tx._merge_yahoo(path.read_bytes(),response.content,cut,set(raw['index']) if symbol.endswith('.TW') else None)
        path.write_bytes(data)
        return {'key':name,'symbol':symbol,'url':response.url,'observed_at':datetime.datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),'cutoff':cut,'sha256':hashlib.sha256(data).hexdigest()}
    try:
        cached=_seed_core_cache(stage,cash,us_cut,set(raw['index']))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:manifest=list(pool.map(get,requests_list))
        manifest.extend(cached)
        manifest.extend(_refresh_positions(stage,cash))
        url='https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv';r=requests.get(url,timeout=(10,45));r.raise_for_status()
        # Exclude bars beyond the verified completed-US date.
        import csv,io
        rows=list(csv.reader(io.StringIO(r.text)));rows=[rows[0]]+[a for a in rows[1:] if datetime.datetime.strptime(a[0],'%m/%d/%Y').date().isoformat()<=us_cut]
        out=io.StringIO();csv.writer(out,lineterminator='\n').writerows(rows);(stage/'vix.csv').write_text(out.getvalue())
        manifest.append({'key':'vix','url':url,'cutoff':us_cut,'sha256':hashlib.sha256((stage/'vix.csv').read_bytes()).hexdigest()})
        url='https://production.dataviz.cnn.io/index/fearandgreed/graphdata';path=stage/'cnn-2021.json'
        try:
            r=requests.get(url,headers={'User-Agent':'Mozilla/5.0'},timeout=(10,45));r.raise_for_status()
            fresh=r.json()['fear_and_greed_historical']['data'];old=json.loads(path.read_text());series=old['fear_and_greed_historical']['data'];merged={a['x']:a for a in series}
            for a in fresh:
                if datetime.datetime.fromtimestamp(a['x']/1000,datetime.timezone.utc).date().isoformat()<=us_cut:merged[a['x']]=a
            old['fear_and_greed_historical']['data']=[a for _,a in sorted(merged.items())];path.write_text(json.dumps(old))
            manifest.append({'key':'feargreed','url':url,'cutoff':us_cut,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        except (requests.RequestException,KeyError,ValueError) as error:
            # Preserve the dated cache, never pretend it is a new observation.
            # freeze() will withhold scenarios if it exceeds its freshness limit.
            manifest.append({'key':'feargreed','url':url,'warning':'來源暫不可用，保留原日期；發布時檢查新鮮度','error':str(error)[:120],'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        C.build_frame(raw,stage) # Validate schemas, dates, features before activation.
        (stage/'comparison-manifest.json').write_text(json.dumps({'fetched_at':now.isoformat(),'cash_cutoff':cash,'US_cutoff':us_cut,'sources':manifest},ensure_ascii=False,indent=2))
        if C.SOURCES.exists():
            backup=C.SOURCES.with_name('v6_compare_sources.old-'+now.strftime('%Y%m%dT%H%M%S'));C.SOURCES.rename(backup)
            try:stage.rename(C.SOURCES)
            except Exception:backup.rename(C.SOURCES);raise
            shutil.rmtree(backup)
        else:stage.rename(C.SOURCES)
        from . import v6_shared_distribution
        try:shared_prices=v6_shared_distribution.refresh_prices(cash)
        except (OSError,ValueError,requests.RequestException) as error:shared_prices={'status':'unavailable','reason':str(error)}
        return {'cash_cutoff':cash,'US_cutoff':us_cut,'sources':len(manifest),'shared_distribution_prices':shared_prices}
    except Exception:
        shutil.rmtree(stage,ignore_errors=True);raise

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--refresh',action='store_true',required=True);parser.parse_args();print(json.dumps(refresh(),ensure_ascii=False))
