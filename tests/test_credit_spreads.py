import unittest
from datetime import date,datetime
from zoneinfo import ZoneInfo
from backend import credit_spreads as c

NOW=datetime(2026,10,23,10,tzinfo=ZoneInfo('Asia/Taipei'))

def page(bond,day='22.10.26',price='96,009',currency='USD'):
    maturity=date.fromisoformat(bond['maturity']).strftime('%d.%m.%Y')
    info={'ISIN':bond['isin'],'Emittent':bond['issuer'],'Währung':currency,'Kupon-Art':'Fest','Zinszahlung':'6 Monate',
          'Fälligkeit':maturity,'Ausgabedatum':'18.06.2026','Nominalzinssatz':str(bond['coupon']).replace('.',',')}
    meta=''.join(f'<tr><th>{k}</th><td>{v}</td></tr>' for k,v in info.items())
    # A stale default quote is deliberately present; only gettex can be selected.
    return f'<table>{meta}<tr><th>Aktuell</th><td>104,40</td></tr><tr><th>Zeit</th><td>01.04.22 09:48</td></tr></table><table><thead><tr><th>Börse</th><th>Aktuell</th><th>Datum</th><th>Zeit</th></tr></thead><tbody><tr><td>mobile summary</td><th>gettex</th><td>Realtime Kurs {price}</td><td>{day}</td><td>20:40</td></tr></tbody></table>'

def xml(day='2026-10-22'):
    return ('<feed xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"><m:properties>'
            f'<d:NEW_DATE>{day}T00:00:00</d:NEW_DATE>'+''.join(f'<d:{k}>{4+i*.1}</d:{k}>' for i,k in enumerate(c.TENORS))+'</m:properties></feed>')


class CreditTests(unittest.TestCase):
    def test_nvidia_issued_price_reproduces_term_sheet_yield(self):
        y,accrued=c.estimate_ytm(99.81,4.5,'2031-06-15','2026-06-18','2026-12-15','2026-06-18')
        self.assertAlmostEqual(y,4.543,places=3)
        self.assertEqual(accrued,0)
        self.assertAlmostEqual((4.543-4.193)*100,35)

    def test_jpm_long_first_coupon_reproduces_official_issue_yield(self):
        y,_=c.estimate_ytm(99.655,4.25,'2027-10-01','2015-09-25','2016-04-01','2015-09-25')
        self.assertAlmostEqual(y,4.287,places=3)

    def test_par_and_price_yield_direction_and_bad_inputs(self):
        args=(5,'2031-06-15','2021-06-15','2021-12-15','2026-06-15')
        self.assertAlmostEqual(c.estimate_ytm(100,*args)[0],5,places=8)
        self.assertGreater(c.estimate_ytm(95,*args)[0],c.estimate_ytm(105,*args)[0])
        with self.assertRaises(ValueError):c.estimate_ytm(0,*args)
        with self.assertRaises(ValueError):c.estimate_ytm(100,5,'2020-01-01','2010-01-01','2010-07-01','2026-10-22')

    def test_interpolation_is_remaining_tenor_and_never_extrapolates(self):
        value,terms=c.interpolate({3:4,5:5,10:6},4)
        self.assertEqual((value,terms),(4.5,[3,5]))
        with self.assertRaises(ValueError):c.interpolate({3:4,5:5},10)
        self.assertEqual(c.days360(date(2026,2,28),date(2026,8,31)),180)

    def test_curve_rejects_future_and_uses_source_dates(self):
        curve=c.parse_treasury(xml(),date(2026,10,23))
        self.assertIn('2026-10-22',curve)
        with self.assertRaises(ValueError):c.parse_treasury(xml('2026-10-24'),date(2026,10,23))

    def test_identity_currency_and_venue_timestamp_checks(self):
        b=next(b for b in c.BONDS if b['ticker']=='NVDA');q=c.parse_quote(page(b),b,NOW)
        self.assertEqual((q['as_of'],q['clean_price'],q['venue']),('2026-10-22',96.009,'gettex'))
        for html in [page(b,currency='EUR'),page(b,day='24.10.26'),page(b,price='0,00'),page(b).replace(b['isin'],'US0000000000')]:
            with self.assertRaises(ValueError):c.parse_quote(html,b,NOW)

    def test_same_day_refresh_does_not_create_change_or_mix_bonds(self):
        row={'isin':'bond','venue':'gettex','method_version':1,'as_of':'2026-10-22','quote_at':'2026-10-22T20:40','spread_bp':70}
        first=c.merge_history(dict(row),None)
        same=c.merge_history(dict(row,spread_bp=75),first)
        self.assertEqual(len(same['history']),1);self.assertIsNone(same['change_bp'])
        next_=c.merge_history(dict(row,as_of='2026-10-23',quote_at='2026-10-23T20:40',spread_bp=80),same)
        self.assertEqual(next_['change_bp'],5)
        newbond=c.merge_history(dict(row,isin='other'),same)
        self.assertIsNone(newbond['change_bp'])
        with self.assertRaises(ValueError):c.merge_history(dict(row,as_of='2026-10-21'),same)

    def test_unmatched_treasury_day_retains_dated_last_good_bond(self):
        b=next(b for b in c.BONDS if b['ticker']=='NVDA')
        prior={'rows':[dict(ticker='NVDA',isin=b['isin'],venue='gettex',method_version=1,as_of='2026-10-21',spread_bp=35,history=[])]}
        def fetch(url):
            if url.startswith('https://home.treasury.gov'):return xml('2026-10-21')
            return page(b)
        r=c.load(fetch,prior,now=NOW)
        nvda=next(x for x in r['rows'] if x['ticker']=='NVDA')
        self.assertEqual((nvda['status'],nvda['as_of'],nvda['spread_bp']),('retained','2026-10-21',35))
        self.assertIn('同日',nvda['error'])
        tesla=next(x for x in r['rows'] if x['ticker']=='TSLA')
        self.assertEqual(tesla['status'],'unavailable');self.assertIsNone(tesla['spread_bp'])

    def test_history_deduplicates_pixels_and_rejects_different_quote(self):
        data={'datetimeLast':'22.10.2026','textLast':'96,009','chartQuoteInfo':[0,{'dateTime':'21.10.2026','text':'95,443'},{'dateTime':'21.10.2026','text':'95,443'},{'dateTime':'22.10.2026','text':'96,009'},{'dateTime':'23.10.2026','text':'97,000'}]}
        def raw():return '<root><replaceelement id="chartMapWrapper"><![CDATA[<div id="chartData">'+__import__('json').dumps(data)+'</div>]]></replaceelement></root>'
        quote={'as_of':'2026-10-22','clean_price':96.009,'issued':'2026-06-18'}
        h=c.parse_price_history(raw(),quote)
        self.assertEqual(h,[{'date':'2026-10-21','value':95.443},{'date':'2026-10-22','value':96.009}])
        with self.assertRaises(ValueError):c.parse_price_history(raw(),dict(quote,clean_price=99))
        data['chartQuoteInfo'].append({'dateTime':'21.10.2026','text':'95,111'})
        with self.assertRaises(ValueError):c.parse_price_history(raw(),quote)

    def test_cds_reference_is_approximation_and_never_uses_bond_spread(self):
        self.assertEqual(c.cds_probability(0),0)
        self.assertAlmostEqual(c.cds_probability(50),4.0810543,places=6)
        self.assertAlmostEqual(c.cds_probability(500),34.075937,places=5)
        for args in [(-1,), (50,1), (50,.4,0)]:
            with self.assertRaises(ValueError):c.cds_probability(*args)

    def test_intraday_quote_uses_latest_matched_history_day_not_mixed_dates(self):
        import json
        b=next(b for b in c.BONDS if b['ticker']=='NVDA')
        data={'datetimeLast':'27.10.2026','textLast':'96,009','chartQuoteInfo':[{'dateTime':'26.10.2026','text':'95,443'},{'dateTime':'27.10.2026','text':'96,009'}]}
        history='<root><replaceelement id="chartMapWrapper"><![CDATA[<div id="chartData">'+json.dumps(data)+'</div>]]></replaceelement></root>'
        def fetch(url):
            if 'treasury.gov' in url:return xml('2026-10-26')
            if 'chart_map' in url:return history
            return page(b,day='27.10.26').replace('</table>','<select id="marketSelect"><option value="123">gettex</option></select></table>',1)
        r=c.load(fetch,now=datetime(2026,10,28,8,tzinfo=ZoneInfo('Asia/Taipei')))
        row=next(x for x in r['rows'] if x['ticker']=='NVDA')
        self.assertEqual((row['as_of'],row['treasury_date'],row['clean_price']),('2026-10-26','2026-10-26',95.443))
        self.assertEqual(row['latest_quote']['as_of'],'2026-10-27')
        self.assertEqual(row['price_history'][-1]['date'],'2026-10-26')
        self.assertEqual(row['quote_at'],'')

    def test_euro_bond_is_price_only_and_short_usd_bond_uses_short_curve(self):
        euro=next(b for b in c.BONDS if b['ticker']=='ASML')
        def fetch(url):
            if url.startswith('https://home.treasury.gov'):return xml()
            return page(euro,currency='EUR').replace('6 Monate','Jahr').replace('18.06.2026','17.05.2022')
        # Provide one valid USD issuer alongside the independently priced EUR bond.
        usd=next(b for b in c.BONDS if b['ticker']=='NVDA')
        def mixed(url):
            if url.endswith(usd['isin']):return page(usd)
            return fetch(url)
        r=c.load(mixed,now=NOW)
        row=next(x for x in r['rows'] if x['ticker']=='ASML')
        self.assertEqual(row['status'],'price_only');self.assertIsNone(row['spread_bp'])
        self.assertNotIn('yield_pct',row)
        self.assertEqual(row['clean_price'],96.009)
        self.assertEqual(c.interpolate({.5:4,1:5},.75),(4.5,[.5,1]))


if __name__=='__main__':unittest.main()
