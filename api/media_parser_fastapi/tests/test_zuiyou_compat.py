import json
import unittest
from urllib.parse import quote
from unittest.mock import patch

from app.parsers import zuiyou

URL='https://share.xiaochuankeji.cn/hybrid/share/post?pid=426922209'
VIDEO='https://cdn.example/real.mp4'
POST={'id':426922209,'content':'测试','videos':{'1':{'url':VIDEO}},'imgs':[{'id':1,'urls':{'origin':{'urls':['https://cdn.example/cover.jpg']}}}]}

class ZuiyouCompatTests(unittest.TestCase):
    def setUp(self):
        # 这些用例只验证页面兜底，禁止请求外网。
        self.api_patch = patch.object(zuiyou, '_api_result', side_effect=RuntimeError('offline'))
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)
        # 短链的网络失败与正常请求一样由 requests 异常表示。
        self.redirect_patch = patch.object(zuiyou, 'get_final_url', side_effect=zuiyou.requests.ConnectionError('offline'))
        self.redirect_patch.start()
        self.addCleanup(self.redirect_patch.stop)

    def test_nested_pc(self):
        html='<script>APP_INITIAL_STATE='+json.dumps({'postDetail':{'data':{'post':POST}}})+'</script>'
        self.assertEqual(zuiyou.parse_zuiyou_page(html,URL)['video_url'],VIDEO)

    def test_uri_mobile_state(self):
        raw=quote(json.dumps({'data':{'post':POST}}))
        html="<script>window.APP_INITIAL_STATE=JSON.parse(decodeURIComponent('"+raw+"'));</script>"
        self.assertEqual(zuiyou.parse_zuiyou_page(html,URL)['post_id'],'426922209')

    def test_single_quoted_json_parse(self):
        html="<script>window.APP_INITIAL_STATE=JSON.parse('"+json.dumps({'post':POST})+"');</script>"
        self.assertEqual(zuiyou.parse_zuiyou_page(html,URL)['video_url'],VIDEO)

    def test_video_url_alias(self):
        post={**POST,'videos':{'1':{'videoUrl':VIDEO}}}
        html='<script>window.APP_INITIAL_STATE='+json.dumps({'post':post})+'</script>'
        self.assertEqual(zuiyou.parse_zuiyou_page(html,URL)['video_url'],VIDEO)

    def test_video_cover_not_success(self):
        post={**POST,'videos':{'1':{'videoId':'no-play-url'}}}
        html='<script>window.APP_INITIAL_STATE='+json.dumps({'post':post})+'</script>'
        self.assertIsNone(zuiyou.parse_zuiyou_page(html,URL))

    def test_errors_distinguished(self):
        with patch.object(zuiyou,'fetch_page',return_value=('<title>打开App</title>',URL,{})):
            with self.assertRaisesRegex(RuntimeError,'未找到帖子数据'):
                zuiyou.parse_zuiyou(URL)
        html='<script>window.APP_INITIAL_STATE='+json.dumps({'post':{**POST,'id':999}})+'</script>'
        with patch.object(zuiyou,'fetch_page',return_value=(html,URL,{})):
            with self.assertRaisesRegex(RuntimeError,'pid 匹配'):
                zuiyou.parse_zuiyou(URL)

    def test_pc_official_alias(self):
        html='<script>window.APP_INITIAL_STATE='+json.dumps({'post':POST})+'</script>'
        def fetch(url,headers):
            return (html if 'share.izuiyou.com' in url else '<title>App</title>',url,{})
        with patch.object(zuiyou,'fetch_page',side_effect=fetch):
            self.assertEqual(zuiyou.parse_zuiyou(URL)['post_id'],'426922209')

    def test_no_eval(self):
        html='<script>APP_INITIAL_STATE=__import__("os").system("touch /tmp/never")</script>'
        self.assertIsNone(zuiyou.parse_zuiyou_page(html,URL))

if __name__=='__main__':unittest.main()
