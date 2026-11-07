"""Verified date intervals: absence of a flow row is not a missing trading record."""
from datetime import date, timedelta


def adjacent(day, offset):
    return (date.fromisoformat(day)+timedelta(days=offset)).isoformat()


def merge_ranges(ranges):
    merged=[]
    for item in sorted(ranges,key=lambda r:r['start']):
        start,end=item['start'],item['end']
        if start>end:
            raise ValueError('歷史涵蓋區間起訖錯誤')
        if merged and start<=adjacent(merged[-1]['end'],1):
            merged[-1]['end']=max(merged[-1]['end'],end)
        else:
            merged.append({'start':start,'end':end})
    return merged


def missing_ranges(start,end,verified,force=False):
    if force:
        return [(start,end)]
    cursor=start
    missing=[]
    for interval in merge_ranges(verified):
        if interval['end']<cursor:
            continue
        if interval['start']>end:
            break
        if interval['start']>cursor:
            missing.append((cursor,min(end,adjacent(interval['start'],-1))))
        cursor=max(cursor,adjacent(interval['end'],1))
        if cursor>end:
            return missing
    if cursor<=end:
        missing.append((cursor,end))
    return missing
