import unittest
from unittest.mock import Mock, patch

import requests
from app.parsers import core, jianying
from app.parsers.common import detect_platform, extract_urls

URL = 'https://lv.ulikecam.com/activity/lv/sharevideo?template_id=7627885230975864126&item_type=1'
PAYLOAD = {'ret': '0', 'data': {'templates': [{'web_id': '7627885230975864126', 'status': 2,
           'title': '#电影感', 'video_url': 'https://cdn.example/video.mp4', 'cover_url': 'https://cdn.example/cover.jpg'}]}}


class JianyingTests(unittest.TestCase):
    def test_share_text_and_domain(self):
        self.assertEqual(extract_urls('剪映，轻而易剪#电影感 🎬 '+URL+' 复制此链接，打开【剪映】'), [URL])
        self.assertEqual(detect_platform(URL), 'jianying')
        for host in ('lv.ulikecam.com.evil.test', 'fakeulikecam.com', 'ulikecam.com'):
            self.assertIsNone(detect_platform('https://'+host))

    def test_routing_and_request(self):
        response = Mock()
        response.json.return_value = PAYLOAD
        with patch.object(core, 'get_final_url', side_effect=requests.ConnectionError()), \
             patch.object(jianying.session, 'post', return_value=response) as post:
            result = core.parse_one_url(URL)
        self.assertEqual(result['platform'], '剪映')
        self.assertEqual(result['video_url'], 'https://cdn.example/video.mp4')
        self.assertEqual(result['share_url'], URL)
        self.assertIsNone(result['watermark_free'])
        self.assertEqual(post.call_args.kwargs['json']['id'], ['7627885230975864126'])
        self.assertEqual(post.call_args.kwargs['headers']['pf'], '1')

    def test_wrong_work_and_unavailable(self):
        with self.assertRaises(RuntimeError):
            jianying.template_result(PAYLOAD, '999')
        for changes in ({'status': 4}, {'video_url': None}):
            item = {**PAYLOAD['data']['templates'][0], **changes}
            with self.assertRaises(RuntimeError):
                jianying.template_result({'data': {'templates': [item]}}, item['web_id'])

    def test_invalid_link(self):
        with patch.object(jianying.session, 'post') as post:
            with self.assertRaises(ValueError):
                jianying.parse_jianying('https://lv.ulikecam.com/activity/lv/sharevideo')
            post.assert_not_called()

    def test_api_failure(self):
        response = Mock()
        response.json.return_value = {'ret': '1014'}
        with patch.object(jianying.session, 'post', return_value=response):
            with self.assertRaisesRegex(RuntimeError, '暂未返回'):
                jianying.parse_jianying(URL)
        with patch.object(jianying.session, 'post', side_effect=requests.Timeout()):
            with self.assertRaisesRegex(RuntimeError, '请求失败'):
                jianying.parse_jianying(URL)

    def test_paid_preview_is_not_success(self):
        item = {**PAYLOAD['data']['templates'][0], 'purchase_info': {'need_purchase': True}}
        with self.assertRaisesRegex(jianying.JianyingPreviewOnlyError, '暂未获取到无水印'):
            jianying.template_result({'data': {'templates': [item]}}, item['web_id'])
