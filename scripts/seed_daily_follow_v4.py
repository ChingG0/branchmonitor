"""Attach tested V4 buy lists to saved V1 reports without changing V1 selections."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--source',required=True,type=Path)
    args=parser.parse_args()
    tested=json.loads(args.source.read_text(encoding='utf-8'))
    picks=tested.get('picks',{}).get('v1_sector8',{})
    if not picks:raise SystemExit('測試檔沒有 v1_sector8 名單')
    conn=sqlite3.connect(args.database);conn.row_factory=sqlite3.Row
    updated=0
    try:
        for day,rows in picks.items():
            saved=conn.execute('SELECT payload FROM daily_follow_reports WHERE date=?',(day,)).fetchone()
            if not saved:continue
            payload=json.loads(saved['payload']);existing={row['symbol']:row for row in payload.get('buy',[])}
            buy=[]
            for tested_row in rows:
                row=dict(existing.get(tested_row['symbol'],{}))
                row.update(tested_row)
                if 'close' not in row:
                    quote=conn.execute('SELECT close FROM prices WHERE date=? AND symbol=?',(day,row['symbol'])).fetchone()
                    row['close']=quote['close'] if quote else None
                row.setdefault('sectors',[row.get('sector_name','未分類')]);buy.append(row)
            payload['v4']={'version':'daily-follow-v4','date':day,'buy':buy,'sector_weight_pct':8.0,
                           'sector_snapshot':tested.get('sector_dictionary',{}).get('snapshot'),
                           'hold_sessions':20,'wait_sessions':5,'pullback_pct':5.0,
                           'method':'92% V1 分數＋8% 同日族群價格趨勢；V4-A 隔日開盤買進，V4-B 開高買、開低等 -5%，成交後持有 20 日。'}
            conn.execute('UPDATE daily_follow_reports SET payload=? WHERE date=?',(json.dumps(payload,ensure_ascii=False),day));updated+=1
        conn.commit()
    finally:conn.close()
    print(json.dumps({'updated':updated,'source_dates':len(picks)},ensure_ascii=False))


if __name__=='__main__':main()
