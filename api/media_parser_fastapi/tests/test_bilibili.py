"""Bilibili 离线回归检查；不请求外网。"""
import json
import unittest
from unittest.mock import patch

import requests

from app.parsers import bilibili, core
from app.parsers.common import detect_platform

BV = 'BV1WFWA6VEoS'
URL = f'https://www.bilibili.com/video/{BV}/'
VIDEO = {'bvid': BV, 'aid': 12345, 'title': '测试作品', 'pic': '//i0.hdslb.com/cover.jpg',
         'rights': {}, 'pages': [{'page': 1, 'cid': 101, 'duration': 30}, {'page': 2, 'cid': 102, 'part': '第二集', 'duration': 40}]}
PLAY = {'quality': 64, 'format': 'mp4720', 'accept_quality': [64], 'accept_description': ['高清 720P'],
        'durl': [{'url': 'https://cdn.example/video.mp4', 'backup_url': None}]}
HTML = '<script>window.__INITIAL_STATE__=' + json.dumps({'cid': 101, 'videoData': VIDEO}) + '</script>'


class BilibiliTests(unittest.TestCase):
    def setUp(self):
        self.fetch = patch.object(bilibili, 'fetch_page', return_value=(HTML, URL, {})).start()
        self.api = patch.object(bilibili, '_api_data', return_value=PLAY).start()
        self.addCleanup(patch.stopall)

    def test_platforms_and_host_boundary(self):
        for url in (URL, 'https://m.bilibili.com/video/av12345', 'https://b23.tv/abc'):
            self.assertEqual(detect_platform(url), 'bilibili')
        for url in ('https://fakebilibili.com', 'https://bilibili.com.evil.test'):
            self.assertIsNone(detect_platform(url))
        self.assertEqual(detect_platform('https://v.kuaishou.com/a'), 'kuaishou')

    def test_progressive_and_cover(self):
        result = bilibili.parse_bilibili(URL)
        self.assertEqual(result['video_url'], PLAY['durl'][0]['url'])
        self.assertEqual(result['quality'], 64)
        self.assertEqual(result['backup_video_urls'], [])
        self.assertEqual(result['cover'], 'https://i0.hdslb.com/cover.jpg')
        self.assertEqual(result['type'], 'video')

    def test_selected_part(self):
        result = bilibili.parse_bilibili(URL + '?p=2')
        self.assertEqual(result['cid'], 102)
        self.assertEqual(result['page'], 2)
        self.assertIn('第二集', result['title'])
        self.assertEqual(self.api.call_args.args[1]['cid'], 102)

    def test_invalid_part(self):
        for suffix in ('?p=0', '?p=abc', '?p=3'):
            with self.assertRaises(ValueError):
                bilibili.parse_bilibili(URL + suffix)

    def test_av(self):
        result = bilibili.parse_bilibili('https://www.bilibili.com/video/av12345/')
        self.assertEqual(result['bvid'], BV)

    def test_short_link(self):
        with patch.object(bilibili, 'get_final_url', return_value=URL):
            self.assertEqual(bilibili.parse_bilibili('https://b23.tv/test')['bvid'], BV)

    def test_wrong_short_link_target(self):
        with patch.object(bilibili, 'get_final_url', return_value='https://example.com/video/' + BV):
            with self.assertRaises(ValueError):
                bilibili.parse_bilibili('https://b23.tv/test')

    def test_metadata_api_fallback(self):
        self.fetch.side_effect = requests.ConnectionError('offline')
        self.api.side_effect = [VIDEO, PLAY]
        self.assertEqual(bilibili.parse_bilibili(URL)['cid'], 101)

    def test_embedded_playback(self):
        html = HTML + '<script>window.__playinfo__=' + json.dumps({'code': 0, 'data': PLAY}) + '</script>'
        self.fetch.return_value = html, URL, {}
        self.assertIn('__playinfo__', bilibili.parse_bilibili(URL)['source'])
        self.api.assert_not_called()

    def test_no_silent_dash_success(self):
        self.api.return_value = {'dash': {'video': [{'baseUrl': 'https://cdn.example/video.m4s'}], 'audio': []}}
        with self.assertRaisesRegex(RuntimeError, '合流 MP4'):
            bilibili.parse_bilibili(URL)

    def test_no_partial_segment_success(self):
        self.api.return_value = {**PLAY, 'durl': PLAY['durl'] * 2}
        with self.assertRaisesRegex(RuntimeError, '多段'):
            bilibili.parse_bilibili(URL)

    def test_no_preview_success(self):
        self.api.return_value = {**PLAY, 'is_preview': 1}
        with self.assertRaisesRegex(RuntimeError, '试看'):
            bilibili.parse_bilibili(URL)

    def test_paid_video_rejected(self):
        video = {**VIDEO, 'rights': {'ugc_pay': 1}}
        self.fetch.return_value = '<script>window.__INITIAL_STATE__=' + json.dumps({'videoData': video}) + '</script>', URL, {}
        with self.assertRaisesRegex(RuntimeError, '付费'):
            bilibili.parse_bilibili(URL)

    def test_api_validation(self):
        patch.stopall()
        with patch.object(bilibili.session, 'get') as get:
            get.return_value.raise_for_status.return_value = None
            get.return_value.json.return_value = {'code': -404, 'message': 'missing'}
            with self.assertRaises(ValueError):
                bilibili._api_data('https://api.bilibili.com/test', {}, {})
            get.return_value.json.side_effect = ValueError('HTML')
            with self.assertRaisesRegex(RuntimeError, 'JSON'):
                bilibili._api_data('https://api.bilibili.com/test', {}, {})

    def test_unified_parser_dispatch(self):
        with patch.object(core, 'get_final_url', return_value=URL):
            result = core.parse_one_url(URL)
            self.assertEqual(result['platform'], 'Bilibili')
        self.assertEqual(detect_platform(URL), 'bilibili')

    def test_expansion_failure_does_not_block_bv(self):
        with patch.object(core, 'get_final_url', side_effect=requests.ConnectionError('offline')):
            self.assertEqual(core.parse_one_url(URL)['type'], 'video')

if __name__ == '__main__':
    unittest.main()
