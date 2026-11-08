from unittest.mock import patch
import pytest
from app.settings import Settings, DEFAULT_REWARDED_AD_UNIT_ID


def test_rewarded_ad_configuration():
    env = {
        'WECHAT_PRIVATE_INGRESS_ONLY': 'true',
        'DATABASE_URL': 'mysql+pymysql://unused/guagua_test_config',
        'WECHAT_ALLOWED_APPIDS': 'wxTestApp',
    }
    with patch.dict('os.environ', env, clear=True):
        assert Settings.from_env().rewarded_ad_unit_id == DEFAULT_REWARDED_AD_UNIT_ID == 'adunit-5db13f7adc24db74'
        with patch.dict('os.environ', {'REWARDED_AD_UNIT_ID': 'adunit-override'}):
            assert Settings.from_env().rewarded_ad_unit_id == 'adunit-override'
        with patch.dict('os.environ', {'REWARDED_AD_UNIT_ID': ''}):
            assert Settings.from_env().rewarded_ad_unit_id == ''
        with patch.dict('os.environ', {'REWARDED_AD_UNIT_ID': 'invalid'}):
            with pytest.raises(RuntimeError, match='REWARDED_AD_UNIT_ID'):
                Settings.from_env()
