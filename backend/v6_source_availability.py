"""Current publication proof; never changes historical feature availability."""
from pathlib import Path
from urllib.parse import urlparse
import hashlib,json
import pandas as pd


def verified_positions(sources, now, as_of):
    """Accept only completed, date-matched TAIFEX downloads with exact file hashes.

    No observed_at means no override. Old/future forecasts cannot use a later
    download, and retail requires both institution and total open-interest files.
    """
    sources=Path(sources);now=pd.Timestamp(now)
    try:
        manifest=json.loads((sources/'comparison-manifest.json').read_text())
        if manifest['cash_cutoff']!=as_of:return {}
        entries={r['key']:r for r in manifest['sources']}
    except (OSError,ValueError,KeyError,TypeError):return {}
    year,month=as_of[:4],as_of[5:7]
    groups={'foreign':[f'positions-TXF-{year}.csv'],
            'retail':[f'positions-MXF-{year}.csv',f'mtx-month-{year}-{month}.csv']}
    proofs={}
    for key,names in groups.items():
        files=[]
        try:
            for name in names:
                row=entries[name];observed=pd.Timestamp(row['observed_at']);url=urlparse(row['url'])
                expected='/cht/3/futDataDown' if name.startswith('mtx-') else '/cht/3/futContractsDateDown'
                if (row['cutoff']!=as_of or url.scheme!='https' or url.hostname!='www.taifex.com.tw'
                    or url.path!=expected or pd.isna(observed) or observed.tzinfo is None
                    or observed>now or now-observed>pd.Timedelta(days=4)
                    or observed.tz_convert('Asia/Taipei').date().isoformat()<as_of):raise ValueError('unverified observation')
                digest=hashlib.sha256((sources/name).read_bytes()).hexdigest()
                if digest!=row['sha256']:raise ValueError('source changed since verification')
                files.append({'file':name,'url':row['url'],'sha256':digest,'observed_at':observed.isoformat()})
            proofs[key]={'as_of':as_of,'available_at':max(pd.Timestamp(r['observed_at']) for r in files).isoformat(),'files':files}
        except (OSError,KeyError,ValueError,TypeError):continue
    return proofs
