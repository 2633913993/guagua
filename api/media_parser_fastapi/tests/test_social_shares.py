import json
import unittest
from urllib.parse import quote
from unittest.mock import patch

import requests

from app.parsers import core, pipix, zuiyou
from app.parsers.common import detect_platform, extract_urls
from app.parsers.share_page import page_states

PPX_URL = 'https://h5.pipix.com/ppx/item/1234567890123456789'
PPX_ITEM = {'item_id_str': '1234567890123456789', 'content': '皮皮虾测试', 'video': {
    'video_id': 'current', 'video_high': {'width': 720, 'height': 1280, 'url_list': [{'url': 'https://cdn.example/pipix.mp4'}]},
    'cover_image': {'url_list': [{'url': 'https://cdn.example/cover.jpg'}]}}}
ZY_URL = 'https://share.izuiyou.com/hybrid/share/post?pid=123'
ZY_POST = {'id': 123, 'content': '最右测试', 'imgs': [{'id': 9, 'urls': {'origin': {'urls': ['https://cdn.example/img.jpg']}}}],
           'videos': {'9': {'url': 'https://cdn.example/zuiyou.mp4'}}}

def ppx_page(item=None, extra=None):
    state = {'ppxItemDetail': {'item': item or PPX_ITEM}, **(extra or {})}
    return '<script type="application/json" id="RENDER_DATA">' + quote(json.dumps(state)) + '</script>'

def zy_page(post=None):
    return '<script>window.APP_INITIAL_STATE=' + json.dumps({'post': post or ZY_POST}) + '</script>'

class SocialTests(unittest.TestCase):
    def setUp(self):
        # 这些用例只验证页面兜底，禁止请求外网。
        self.api_patch = patch.object(zuiyou, '_api_result', side_effect=RuntimeError('offline'))
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)
        # 短链的网络失败与正常请求一样由 requests 异常表示。
        self.redirect_patch = patch.object(zuiyou, 'get_final_url', side_effect=zuiyou.requests.ConnectionError('offline'))
        self.redirect_patch.start()
        self.addCleanup(self.redirect_patch.stop)

    def test_domains_and_existing_platforms(self):
        for url, expected in [('https://s.izuiyou.com/s/abc','zuiyou'),('https://share.xiaochuankeji.cn/hybrid/share/post?pid=1','zuiyou'),
                              ('https://h5.pipix.com/s/abc','pipix'),('https://www.bilibili.com/video/a','bilibili'),
                              ('https://v.douyin.com/a','douyin'),('https://xhslink.cn/a','xiaohongshu'),('https://v.kuaishou.com/a','kuaishou')]:
            self.assertEqual(detect_platform(url),expected)
        for host in ['izuiyou.com.evil.test','fakepipix.com','share.xiaochuankeji.cn.evil.test']:
            self.assertIsNone(detect_platform('https://'+host))

    def test_full_share_text(self):
        self.assertEqual(extract_urls('一条有趣的内容给你，不好看算我输。请戳链接>>https://s.izuiyou.com/s/22WvB'),['https://s.izuiyou.com/s/22WvB'])

    def test_pipix_encoded_state(self):
        result=pipix.parse_pipix_page(ppx_page(),PPX_URL)
        self.assertEqual(result['video_url'],'https://cdn.example/pipix.mp4')
        self.assertEqual(result['item_id'],'1234567890123456789')
        self.assertEqual(result['source'],'RENDER_DATA')
        self.assertIsNone(result['watermark_free'])

    def test_pipix_different_comment_ignored(self):
        item={**PPX_ITEM,'video':{'video_id':'current','video_download':{'url_list':['https://cdn.example/current.mp4']}}}
        extra={'ppxCellComment':{'comments':[{'item_id_str':'111','video':{'video_id':'wrong','video_high':{'url_list':['https://cdn.example/comment.mp4']}}}]}}
        self.assertEqual(pipix.parse_pipix_page(ppx_page(item,extra),PPX_URL)['video_url'],'https://cdn.example/current.mp4')

    def test_pipix_same_work_duplicate(self):
        item={**PPX_ITEM,'video':{'video_id':'current','video_download':{'url_list':['https://cdn.example/download.mp4']}}}
        extra={'ppxCellComment':{'comments':[PPX_ITEM]}}
        self.assertEqual(pipix.parse_pipix_page(ppx_page(item,extra),PPX_URL)['video_url'],'https://cdn.example/pipix.mp4')

    def test_pipix_images(self):
        item={'item_id_str':'1234567890123456789','images':[{'url_list':['https://cdn.example/one.jpg','https://mirror.example/one.jpg']},'https://cdn.example/two.jpg']}
        result=pipix.parse_pipix_page(ppx_page(item),PPX_URL)
        self.assertEqual(result['type'],'image');self.assertEqual(len(result['image_urls']),2)

    def test_pipix_wrong_id_and_cover(self):
        self.assertIsNone(pipix.parse_pipix_page(ppx_page(),'https://h5.pipix.com/ppx/item/999'))
        self.assertIsNone(pipix.parse_pipix_page('<meta property="og:image" content="https://cdn.example/cover.jpg">',PPX_URL))

    def test_zuiyou_video(self):
        result=zuiyou.parse_zuiyou_page(zy_page(),ZY_URL)
        self.assertEqual(result['type'],'video');self.assertEqual(result['video_url'],'https://cdn.example/zuiyou.mp4')
        self.assertEqual(result['cover'],'https://cdn.example/img.jpg')

    def test_zuiyou_images(self):
        result=zuiyou.parse_zuiyou_page(zy_page({**ZY_POST,'videos':{}}),ZY_URL)
        self.assertEqual(result['image_urls'],['https://cdn.example/img.jpg'])

    def test_zuiyou_target_and_no_recommendation(self):
        self.assertIsNone(zuiyou.parse_zuiyou_page(zy_page(),ZY_URL.replace('123','999')))
        self.assertIsNone(zuiyou.parse_zuiyou_page('<script>window.APP_INITIAL_STATE='+json.dumps({'recommend':[ZY_POST]})+'</script>',ZY_URL))

    def test_zuiyou_multiple_videos(self):
        post={**ZY_POST,'imgs':[],'videos':{'1':{'url':'https://cdn.example/one.mp4'},'2':{'url':'https://cdn.example/two.mp4'}}}
        with self.assertRaisesRegex(RuntimeError,'多个视频'):
            zuiyou.parse_zuiyou_page(zy_page(post),ZY_URL)

    def test_safe_json_parse(self):
        data={'post':ZY_POST}
        source='<script>window.APP_INITIAL_STATE = JSON.parse('+json.dumps(json.dumps(data))+');</script>'
        self.assertEqual(zuiyou.parse_zuiyou_page(source,ZY_URL)['post_id'],'123')
        self.assertEqual(page_states('<script>window.APP_INITIAL_STATE = alert(1)</script>',('window.APP_INITIAL_STATE',)),[])

    def test_network_errors(self):
        with patch.object(zuiyou,'fetch_page',side_effect=requests.exceptions.SSLError('TLS')):
            with self.assertRaisesRegex(RuntimeError,'TLS'):
                zuiyou.parse_zuiyou('https://s.izuiyou.com/s/abc')
        with patch.object(pipix,'fetch_page',side_effect=requests.ConnectionError('network')):
            with self.assertRaisesRegex(RuntimeError,'网络'):
                pipix.parse_pipix(PPX_URL)

    def test_unified_parser_dispatch(self):
        def expanded(url,headers=None):return PPX_URL if 'pipix' in url else ZY_URL
        with patch.object(core,'get_final_url',side_effect=expanded), \
             patch.object(pipix,'fetch_page',return_value=(ppx_page(),PPX_URL,{})), \
             patch.object(zuiyou,'fetch_page',return_value=(zy_page(),ZY_URL,{})):
            for url,platform in [('https://h5.pipix.com/s/a','皮皮虾'),('https://s.izuiyou.com/s/a','最右')]:
                self.assertEqual(core.parse_one_url(url)['platform'], platform)
        for url,platform in [('https://s.izuiyou.com/s/a','zuiyou'),('https://h5.pipix.com/s/a','pipix')]:
            self.assertEqual(detect_platform(url), platform)

if __name__=='__main__':unittest.main()
