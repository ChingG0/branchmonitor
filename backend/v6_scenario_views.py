"""Dated conditional scenario readings from the already-frozen price inputs."""
import math

def market_view(row):
    def finite(key):
        v=row.get(key)
        return float(v) if v is not None and math.isfinite(float(v)) else None
    close,ma5,tsmc,peer=[finite(k) for k in ('close','ma5','tsmc_gap5','peer_weak')]
    if close is None or ma5 is None or ma5<=0:
        return {'summary':'價格資料不足，暫不形成具體市場觀點。','evidence':[],'note':'僅解讀保存時已知資料，不改變分數、機率或交易規則。'}
    weak=close<ma5 and tsmc is not None and tsmc<0
    strong=close>=ma5 and tsmc is not None and tsmc>=0
    summary=('短線價格與台積電仍偏弱，上漲劇本先視為跌後反彈修復；收復最新日高點與5MA後，再評估是否轉強。' if weak else
             '短線價格與台積電同步改善，上漲情境較有價格支持；若又跌回5MA並失守最新日低點，需重新評估。' if strong else
             '大盤與權值股訊號尚未一致，先觀察支撐是否守住及反彈能否收復5MA，等待方向確認。')
    evidence=[f'加權距5MA {(close/ma5-1)*100:+.2f}%（5MA約{ma5:,.0f}點）']
    if tsmc is not None:evidence.append(f'台積電距5MA {tsmc*100:+.2f}%')
    spy,tlt,gold=[finite(k) for k in ('spy_r5','tlt_r5','gold_r5')]
    if all(v is not None for v in (spy,tlt,gold)):
        evidence.append('股債金近5日同跌：'+('是' if spy<0 and tlt<0 and gold<0 else '否，不能直接視為全面變現壓力'))
    return {'summary':summary,'evidence':evidence,'note':'這是已保存價量與跨市場資料的條件判讀，不加入未查核新聞，也不另加分或改變三種機率。'}

def scenario_views(row):
    def level(key):
        value=row.get(key)
        return float(value) if value is not None and math.isfinite(float(value)) else None
    def text(value):return f'{value:,.0f}點' if value is not None else '資料待補'
    high,low,ma5=level('high'),level('low'),level('ma5');close=level('close')
    lower,upper=close*.995 if close else None,close*1.005 if close else None
    return {
      'up':{'title':'反彈修復，尚待確認反轉','reading':'此劇本是第3日收盤高於起點0.5%以上；過程仍可能先跌。反彈與多頭趨勢反轉要分開看。',
        'support':f'收紅且收復最新日高點{text(high)}，再觀察能否站回5MA約{text(ma5)}，並由台積電與權值股同步改善確認。',
        'weakening':'反彈受制於原破位區、外資續增空，或回升後又跌回最新日低點，會削弱修復情境。'},
      'flat':{'title':'跌後整理，等待方向選擇','reading':f'此劇本是第3日收盤回到{text(lower)}～{text(upper)}，不是預測盤中只在這個區間。中途仍可能大幅震盪。',
        'support':'低點暫時守住，但反彈仍無法持續收復短期均線；美元與外資部位也未出現一致的新方向。',
        'weakening':'若價格有效離開整理區，且權值股與外資同向確認，就應重新評估上漲或下跌情境。'},
      'down':{'title':'弱勢延續，觀察低點是否失守','reading':'此劇本是第3日收盤低於起點0.5%以上；要區分台股自身走弱和股債金共同承壓，不能見美元上漲就斷言股災。',
        'support':f'最新日低點{text(low)}失守且無法收復，同時台積電偏弱、外資增空或美元走強，會增加續弱情境的支持。',
        'weakening':f'破低後迅速收復，進而收紅越過前日高點；若也站回5MA約{text(ma5)}，下跌延續的條件便減弱。'}
    }
