"""验证最右公开详情 API 的请求契约和统一接口接入；不请求外网。"""
import unittest
from unittest.mock import patch

import requests

from app.parsers import core, zuiyou

URL='https://share.xiaochuankeji.cn/hybrid/share/post?pid=426922209'
SHORT='https://s.izuiyou.com/s/22X3v'
POST={'id':426922209,'content':'测试作品','imgs':[{'id':5,'url':'https://cdn.example/cover.jpg'}],
      'videos':{'5':{'url':'//cdn.example/movie.mp4'}},'member':{'name':'作者'}}

class ZuiyouAPITests(unittest.TestCase):
    def setUp(self):
        self.mock=patch.object(zuiyou.session,'post').start()
        self.addCleanup(patch.stopall)
        response=self.mock.return_value
        response.raise_for_status.return_value=None
        response.json.return_value={'ret':1,'data':{'post':POST}}

    def test_post_contract_and_source(self):
        with patch.object(zuiyou,'fetch_page') as page:
            result=zuiyou.parse_zuiyou(URL)
            page.assert_not_called()
        args=self.mock.call_args
        self.assertEqual(args.args[0],'https://share.xiaochuankeji.cn/planck/share/post/detail_h5')
        self.assertEqual(args.kwargs['json'],{'pid':426922209,'h_av':'5.2.13.011'})
        self.assertEqual(args.kwargs['headers']['Referer'],'https://share.izuiyou.com/')
        self.assertNotIn('verify',args.kwargs)
        self.assertEqual(result['video_url'],'https://cdn.example/movie.mp4')
        self.assertEqual(result['source'],'最右公开详情 API')

    def test_short_link_expands(self):
        with patch.object(zuiyou,'get_final_url',return_value=URL) as expand:
            self.assertEqual(zuiyou.parse_zuiyou(SHORT)['post_id'],'426922209')
            expand.assert_called_once()

    def test_core_expanded_url_does_not_expand_again(self):
        with patch.object(zuiyou,'get_final_url') as expand:
            self.assertEqual(zuiyou.parse_zuiyou(URL,share_url=SHORT)['type'],'video')
            expand.assert_not_called()

    def test_pid_is_not_first_query_parameter(self):
        result=zuiyou.parse_zuiyou('https://share.xiaochuankeji.cn/hybrid/share/post?utm=share&pid=426922209')
        self.assertEqual(result['post_id'],'426922209')

    def test_images(self):
        post={**POST,'videos':{},'imgs':[{'url':'https://cdn.example/1.jpg'},{'url':'https://cdn.example/2.jpg'}]}
        self.mock.return_value.json.return_value={'ret':1,'data':{'post':post}}
        result=zuiyou._api_result('426922209')
        self.assertEqual(result['type'],'image');self.assertEqual(len(result['image_urls']),2)

    def test_mismatch_and_missing_data(self):
        for payload in [{'ret':1,'data':{'post':{**POST,'id':999}}},{'ret':1,'data':{}},{'ret':0,'data':{'post':POST}}]:
            self.mock.return_value.json.return_value=payload
            with self.assertRaises(RuntimeError):zuiyou._api_result('426922209')

    def test_invalid_json_and_network(self):
        self.mock.return_value.json.side_effect=ValueError('not JSON')
        with self.assertRaisesRegex(RuntimeError,'JSON'):zuiyou._api_result('426922209')
        self.mock.side_effect=requests.ConnectionError('offline')
        with self.assertRaisesRegex(RuntimeError,'请求失败'):zuiyou._api_result('426922209')

    def test_api_failure_page_fallback(self):
        self.mock.side_effect=requests.ConnectionError('offline')
        with patch.object(zuiyou,'_parse_zuiyou_pages',return_value={'type':'video','video_url':'https://cdn.example/page.mp4'}) as fallback:
            self.assertEqual(zuiyou.parse_zuiyou(URL)['video_url'],'https://cdn.example/page.mp4')
            self.assertEqual(fallback.call_args.args[0],URL)

    def test_unified_endpoint(self):
        with patch.object(core,'get_final_url',return_value=URL):
            data = {'data': core.parse_one_url(SHORT)}
            self.assertEqual(data['data']['source'],'最右公开详情 API')
            self.assertEqual(data['data']['share_url'],SHORT)

    def test_no_media_is_not_success(self):
        self.mock.return_value.json.return_value={'ret':1,'data':{'post':{'id':426922209,'content':'纯文字'}}}
        with self.assertRaisesRegex(RuntimeError,'没有可用'):zuiyou._api_result('426922209')

if __name__=='__main__':unittest.main()
