"""匯入「產業營運結構」試算表：逐檔的上游供應商、同業競爭者、下游客戶。

比產業價值鏈平台細：對手方是具名公司（含外商），且附代號可回連本機行情。
只讀檔案，不修改來源試算表。
"""
from __future__ import annotations

import re
from pathlib import Path

import openpyxl

from sectors import store, initialize, quotes

SOURCE = Path('/Users/ching/Downloads/產業營運結構.xlsx')
SHEETS = {'上游供應商': ('upstream', '上游產業'),
          '同業競爭者': ('peer', '細產業'),
          '下游客戶': ('downstream', '下游產業')}
# 對手方寫法不一：「亞泥(1102)」「中聯資源9930.tw」「Taiheiyo Cement(5233.JP)」。
# 四碼數字在台、港、陸、日股都存在，不能只看數字就當台股——「味丹國際(2317)」是港股，
# 直接對應會連到鴻海。因此帶國別後綴的一律排除，其餘再用本機股名交叉驗證。
TW_CODE = re.compile(r'[（(]\s*([1-9]\d{3})\s*[)）]|(?<![0-9A-Za-z.])([1-9]\d{3})\s*\.tw(?![0-9A-Za-z])', re.I)


def clean_name(text):
    return re.sub(r'\s*[（(][^)）]*[)）]\s*$', '', text).strip() or text.strip()


def tw_code(text, names):
    """解析台股代號；代號存在且名稱對得上本機行情才採用。

    來源多半寫成「東泥(1110)」，只有極少數寫「中聯資源9930.tw」。帶 .tw 的一律信任；
    純括號四碼要再比對名稱，因為四碼在港、陸、日股同樣存在（味丹國際(2317) 是港股，
    直接採用會連到鴻海）。來源另有少量錯字（敬鹏／敬鵬、申豊／申豐），
    因此同長度只差一個字也視為同一家。
    """
    match = TW_CODE.search(text)
    if not match:
        return None
    code = next(g for g in match.groups() if g)
    local = names.get(code)
    if not local:
        return None
    if text.lower().endswith('.tw'):
        return code
    candidate = re.sub(r'[\s\-*—]', '', clean_name(text))
    official = re.sub(r'[\s\-*—]', '', local)
    if candidate[:2] == official[:2] or candidate in official or official in candidate:
        return code
    if len(candidate) == len(official) and sum(
            a != b for a, b in zip(candidate, official)) == 1:
        return code
    return None


def local_names():
    with quotes() as conn:
        day = conn.execute("SELECT MAX(date) FROM market_days WHERE twse_status='ok'").fetchone()[0]
        return {r['symbol']: r['name'] for r in conn.execute(
            'SELECT symbol,name FROM prices WHERE date=?', (day,))}


def load():
    names = local_names()
    if not SOURCE.exists():
        raise SystemExit(f'找不到來源試算表：{SOURCE}')
    workbook = openpyxl.load_workbook(SOURCE, read_only=True, data_only=True)
    rows = []
    for sheet, (relation, _) in SHEETS.items():
        if sheet not in workbook.sheetnames:
            raise SystemExit(f'試算表缺少工作表「{sheet}」')
        stream = workbook[sheet].iter_rows(values_only=True)
        header = [str(v).strip() if v else '' for v in next(stream)[:4]]
        if header[:2] != ['代號', '股票']:
            raise SystemExit(f'「{sheet}」欄位格式改變：{header}')
        for record in stream:
            values = [str(v).strip() if v is not None else '' for v in (record + (None,) * 4)[:4]]
            symbol, name, category, party = values
            if not re.fullmatch(r'[1-9]\d{3}', symbol) or not party:
                continue
            rows.append((symbol, name, relation, category, party,
                         clean_name(party), tw_code(party, names)))
    return rows


def run():
    initialize()
    with store() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS structure (
          symbol TEXT NOT NULL, name TEXT NOT NULL, relation TEXT NOT NULL,
          category TEXT NOT NULL, party TEXT NOT NULL, party_name TEXT NOT NULL,
          party_symbol TEXT, PRIMARY KEY(symbol,relation,category,party));
        CREATE INDEX IF NOT EXISTS idx_structure_symbol ON structure(symbol);
        CREATE INDEX IF NOT EXISTS idx_structure_party ON structure(party_symbol);
        ''')
    rows = load()
    with store() as conn:
        conn.execute('DELETE FROM structure')
        conn.executemany('INSERT OR REPLACE INTO structure VALUES (?,?,?,?,?,?,?)', rows)
        stats = {r['relation']: (r['n'], r['subjects'], r['linked']) for r in conn.execute(
            '''SELECT relation,COUNT(*) n,COUNT(DISTINCT symbol) subjects,
                      SUM(party_symbol IS NOT NULL) linked FROM structure GROUP BY relation''')}
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('structure_source',?)", (SOURCE.name,))
    return stats


if __name__ == '__main__':
    for relation, (count, subjects, linked) in run().items():
        print(f'{relation:11} {count:5} 筆 · 主體 {subjects:3} 檔 · 對手方可連台股 {linked}')
