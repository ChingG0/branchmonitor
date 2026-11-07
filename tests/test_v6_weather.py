import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend import full_update, v6_weather


class V6WeatherTests(unittest.TestCase):
    def draft(self):
        return {
            'market_as_of': '2026-10-16', 'stage': '形成',
            'summary': '海外科技股轉弱，台股融資資料待覆核。',
            'drivers': ['美股科技股走低'],
            'watch_next': ['台指夜盤與市場廣度'],
            'weakening': ['指數收復前高'],
            'indicators': [{
                'category': 'margin', 'label': '融資餘額', 'value': 1234, 'unit': '交易單位',
                'as_of': '2026-10-16', 'source': '臺灣證交所',
                'url': 'https://www.twse.com.tw/', 'reading': '與前期比較'
            }],
            'news': [{
                'title': '海外市場波動', 'published_at': '2026-10-17T09:00:00+08:00',
                'source': '交易所', 'url': 'https://www.twse.com.tw/',
                'impact': '可能傳導至台指夜盤', 'direction': '觀察'
            }]
        }

    def test_publish_freezes_read_only_reports_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            first = v6_weather.publish(self.draft(), store=store,
                now=datetime(2026, 10, 18, 10, tzinfo=ZoneInfo('Asia/Taipei')),
                model={'score': 65}, local_indicators=[])
            second_draft = self.draft()
            second_draft['stage'] = '減弱'
            second = v6_weather.publish(second_draft, store=store,
                now=datetime(2026, 10, 18, 11, tzinfo=ZoneInfo('Asia/Taipei')),
                model={'score': 60}, local_indicators=[])
            latest = v6_weather.report(store=store)
            self.assertEqual(latest['snapshot']['id'], second['id'])
            self.assertEqual(latest['snapshot']['model']['score'], 60)
            self.assertEqual(v6_weather.report(first['id'], store=store)['snapshot']['model']['score'], 65)
            self.assertEqual(len(latest['history']), 2)
            self.assertEqual(json.loads((store / (first['id'] + '.json')).read_text())['stage'], '形成')
            self.assertIsNone(v6_weather.report('../missing', store=store)['snapshot'])

    def test_rejects_uncited_or_nonfinite_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = self.draft()
            draft['news'][0]['url'] = 'javascript:alert(1)'
            with self.assertRaises(ValueError):
                v6_weather.publish(draft, store=Path(tmp), model={}, local_indicators=[])
            draft = self.draft()
            draft['indicators'][0]['value'] = float('nan')
            with self.assertRaises(ValueError):
                v6_weather.publish(draft, store=Path(tmp), model={}, local_indicators=[])
            self.assertFalse(list(Path(tmp).glob('*.json')))

    def test_home_refresh_does_not_include_v6(self):
        self.assertNotIn('V6 台指期', full_update.NAMES)

    def test_darkpool_is_monitored_and_requires_cited_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft = self.draft()
            draft['indicators'].append({
                'category': 'darkpool', 'label': '美國 DIX', 'value': 44.65,
                'unit': '%', 'as_of': '2026-10-16', 'source': 'SqueezeMetrics',
                'url': 'https://squeezemetrics.com/monitor/dix',
                'reading': '需和前次資料與美股價格一起觀察'
            })
            v6_weather.publish(draft, store=Path(tmp), model={}, local_indicators=[])
            self.assertIn('darkpool', [row['key'] for row in v6_weather.report(store=Path(tmp))['categories']])
            self.assertEqual(v6_weather.report(store=Path(tmp))['snapshot']['indicators'][-1]['as_of'], '2026-10-16')


if __name__ == '__main__':
    unittest.main()
