"""Separately dated research magnitude estimates; never convert direction odds to points."""
import argparse,json,math
from pathlib import Path
from . import db,v6_conditional as C
VERSION='v6-night-points-research-1.0.0'
CONFIG=db.ROOT/'config/v6_night_points'
STORE=db.DATA/'v6_night_point_estimates'

def release():
    r=json.loads((CONFIG/'release.json').read_text())
    for name,digest in r['assets'].items():
        if C.digest((CONFIG/name).read_bytes())!=digest:raise ValueError('幅度模型封存不符：'+name)
    m=json.loads((CONFIG/'models.json').read_text())
    if m['version']!=VERSION or m['role']!='research_only':raise ValueError('幅度模型版本不符')
    return r,m

def estimate(row,models):
    anchor=float(row['anchorClose']);sigma=float(row['dailySigma'])
    if not all(math.isfinite(x) and x>0 for x in [anchor,sigma]):raise ValueError('幅度模型錨點或波動無效')
    result=[]
    for target in row['targets']:
        h=target['horizon'];model=models['models'][str(h)]
        if not model['summary']['research_display_eligible']:raise ValueError('該期限幅度研究未通過預設顯示條件')
        z=[(float(row['features'][key])-model['mean'][i])/model['std'][i] for i,key in enumerate(model['features'])]
        theta=model['theta'];center=theta[0]+sum(b*x for b,x in zip(theta[1:],z));q=model['calibration_residual_quantiles'];scale=math.sqrt(h)*sigma
        levels=[anchor*math.exp((center+q[k])*scale) for k in ['q10','q50','q90']]
        if not all(math.isfinite(x) and x>0 for x in levels) or levels!=sorted(levels):raise ValueError('幅度模型點位或區間無效')
        low,median,high=levels
        result.append({'horizon':h,'targetDate':target['targetDate'],'medianClose':median,'medianChangePoints':median-anchor,
            'medianReturnPct':(median/anchor-1)*100,'low80Close':low,'high80Close':high,
            'low80ChangePoints':low-anchor,'high80ChangePoints':high-anchor,
            'historicalMaePct':model['summary']['median_mae_pct'],
            'historicalCoverage80Pct':model['summary']['central80_empirical_coverage']*100})
    return result

def annotate(row,*,store=None):
    """Explicit chat operation, including late calculations for an existing scenario."""
    cfg,models=release();store=Path(store or STORE)
    name=row['id']+'-'+cfg['assets']['models.json'][:12]+'.json';path=store/name
    if path.exists():return json.loads(path.read_text())
    now=C.clock()
    if C.stamp(row['savedAt'])>now:raise ValueError('來源情境保存時間在未來')
    targets=estimate(row,models)
    # Do not add a retrospective points forecast after its first target matured.
    if C.at(targets[0]['targetDate'],13,30)<=now:raise ValueError('首個期限已到期，不補造當時的點數預報')
    record={'version':VERSION,'scenarioId':row['id'],'scenarioSavedAt':row['savedAt'],'generatedAt':now.isoformat(),
        'anchorDate':row['anchorDate'],'anchorClose':row['anchorClose'],'role':'research_only',
        'sourceSnapshotContentSha256':C.digest(C.encode(row)),'modelSha256':cfg['assets']['models.json'],
        'protocolSha256':cfg['assets']['protocol.json'],'isOriginalIssueTimeEstimate':False,
        'note':'幅度模型另算中位數與80%參考區間；不是由上漲機率換算，也不是台指期目標價。補算時間另列，未驗證盤後準確率。',
        'targets':targets}
    store.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:f.write(C.encode(record))
    return record

def saved(row,*,store=None,now=None):
    """Read an existing annotation; never calculate, fetch, grade or write."""
    try:
        cfg,models=release();path=Path(store or STORE)/(row['id']+'-'+cfg['assets']['models.json'][:12]+'.json')
        p=json.loads(path.read_text())
        if p['version']!=VERSION or p['scenarioId']!=row['id'] or p['role']!='research_only' or p['modelSha256']!=cfg['assets']['models.json']:return None
        if p['sourceSnapshotContentSha256']!=C.digest(C.encode(row)):return None
        if [x['targetDate'] for x in p['targets']]!=[x['targetDate'] for x in row['targets']]:return None
        if not C.stamp(row['savedAt'])<=C.stamp(p['generatedAt'])<=(now or C.clock()):return None
        for t in p['targets']:
            values=[t[k] for k in ['medianClose','medianChangePoints','low80Close','high80Close','low80ChangePoints','high80ChangePoints']]
            if not all(math.isfinite(x) for x in values) or not 0<t['low80Close']<=t['medianClose']<=t['high80Close']:return None
            if abs(t['medianClose']-row['anchorClose']-t['medianChangePoints'])>1e-6:return None
        return p
    except (OSError,ValueError,TypeError,KeyError):return None

def main():
    from . import v6_night_scenario as S,v6_probability_history as H
    p=argparse.ArgumentParser(description='手動保存最新夜盤情境的研究幅度；不新增方向預報')
    p.add_argument('action',choices=['annotate-latest']);p.parse_args()
    rows=S.history()
    if not rows:raise ValueError('沒有已保存情境')
    result=annotate(rows[-1]);H.export();print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
