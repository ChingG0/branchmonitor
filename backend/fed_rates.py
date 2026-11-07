"""Fed official target range changes, dated by policy effective date."""
import calendar
from fractions import Fraction
from datetime import date,datetime,timedelta
import re
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
SOURCE='https://www.federalreserve.gov/monetarypolicy/openmarket.htm'


def parse_history(raw,today=None):
    today=today or datetime.now(ZoneInfo('America/New_York')).date()
    page=BeautifulSoup(raw,'html.parser');rows={}
    months={name:i for i,name in enumerate(calendar.month_name) if name}
    for table in page.select('table'):
        title=table.find_previous(['h3','h4'])
        if not title or not re.fullmatch(r'20\d{2}',title.get_text(strip=True)):continue
        year=int(title.get_text(strip=True))
        if year<2015:continue
        for tr in table.select('tr'):
            cells=[c.get_text(' ',strip=True) for c in tr.select('td')]
            if len(cells)!=4:continue
            stamp=re.match(r'([A-Za-z]+)\s+(\d{1,2})',cells[0]);level=re.fullmatch(r'(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)',cells[3])
            if not stamp or stamp[1] not in months or not level:continue
            d=date(year,months[stamp[1]],int(stamp[2]))
            if d>today:continue
            low,high=map(float,level.groups());increase,decrease=map(float,cells[1:3])
            if not 0<=low<high<30 or min(increase,decrease)<0:raise ValueError('Fed 利率歷史值無效')
            rows[d.isoformat()]=dict(date=d.isoformat(),low=low,high=high,change_bp=increase-decrease)
    result=sorted(rows.values(),key=lambda r:r['date'])
    if len(result)<2:raise ValueError('Fed 利率歷史資料不足')
    for before,current in zip(result,result[1:]):
        if abs((current['high']-before['high'])*100-current['change_bp'])>.01:raise ValueError('Fed 利率調整幅度核對失敗')
    return result


def parse_statement(raw):
    text=BeautifulSoup(raw,'html.parser').get_text(' ',strip=True)
    text=text.replace('–','-').replace('‑','-')
    match=re.search(r'target range for the federal funds rate(.{0,180}?)\bpercent\b',text,re.I)
    if not match:raise ValueError('Fed 最新聲明無法辨識目標區間')
    token=r'(?:\d+-\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)'
    pair=re.search('('+token+r')\s*(?:to|-)\s*('+token+')\s*$',match[1].strip())
    if not pair:raise ValueError('Fed 聲明利率區間格式改變')
    def number(value):
        if '-' in value:
            whole,part=value.split('-');return float(whole)+float(Fraction(part))
        return float(Fraction(value))
    return tuple(number(value) for value in pair.groups())


def load(fetch,calendar_parser,calendar_url):
    info=calendar_parser(fetch(calendar_url));history=parse_history(fetch(SOURCE));latest=history[-1]
    if date.fromisoformat(latest['date'])>date.fromisoformat(info['statement_date'])+timedelta(days=7):
        raise ValueError('Fed 決議與生效日期尚未一致')
    statement='https://www.federalreserve.gov/newsevents/pressreleases/monetary'+info['statement_date'].replace('-','')+'a.htm'
    low,high=parse_statement(fetch(statement))
    # A later meeting can leave rates unchanged; verify its actual statement
    # instead of requiring every meeting to appear as a rate change.
    if abs(low-latest['low'])>.0001 or abs(high-latest['high'])>.0001:
        raise ValueError('Fed 最新聲明與歷史目標區間尚未一致')
    return dict(as_of=latest['date'],low=latest['low'],high=latest['high'],history=history,
                last_change_bp=latest['change_bp'],decision_date=info['statement_date'],
                observed_through=datetime.now(ZoneInfo('America/New_York')).date().isoformat(),
                next_start=info['next_start'],next_end=info['next_end'],calendar_url=calendar_url,
                history_url=SOURCE,statement_url=statement,note='Fed 官方聯邦基金目標區間；階梯圖依實際生效日，FOMC 會議日期採美東。')
