"""Only operator-controlled environment variables; no client authentication switches."""
import os
import re
from dataclasses import dataclass


DEFAULT_REWARDED_AD_UNIT_ID = "adunit-5db13f7adc24db74"


@dataclass(frozen=True)
class Settings:
    database_url: str
    allowed_appids: frozenset[str]
    daily_free_quota: int = 5
    share_reward: int = 5
    proxy_base_url: str = ""
    media_proxy_enabled: bool = False

    rewarded_ad_unit_id: str = DEFAULT_REWARDED_AD_UNIT_ID

    def __post_init__(self):
        if self.rewarded_ad_unit_id and not re.fullmatch(r"adunit-[A-Za-z0-9]+", self.rewarded_ad_unit_id):
            raise RuntimeError("REWARDED_AD_UNIT_ID 格式错误")
        if self.media_proxy_enabled:
            raise RuntimeError("公网代理已停用，请设置 MEDIA_PROXY_ENABLED=false 并配置云存储转存")

    @classmethod
    def from_env(cls):
        # This is a deployment attestation, NOT a header signature verification.
        # Set only after blocking public/custom-domain/container ingress to business routes.
        # Downloads also use private ingress; files are delivered by cloud storage.
        if os.getenv("WECHAT_PRIVATE_INGRESS_ONLY") != "true":
            raise RuntimeError("必须先关闭业务接口的全部公网入口，再设置 WECHAT_PRIVATE_INGRESS_ONLY=true")
        url = os.getenv("DATABASE_URL", "")
        if not url.startswith("mysql+pymysql://"):
            raise RuntimeError("DATABASE_URL 必须使用 mysql+pymysql://")
        appids = frozenset(filter(None, os.getenv("WECHAT_ALLOWED_APPIDS", "").split(",")))
        if not appids or any(not a.isascii() or not a.isalnum() or len(a) > 64 for a in appids):
            raise RuntimeError("必须配置 WECHAT_ALLOWED_APPIDS")
        quota = int(os.getenv("DAILY_FREE_QUOTA", "5"))
        if not 0 <= quota <= 1_000_000:
            raise RuntimeError("DAILY_FREE_QUOTA 超出范围")
        reward = int(os.getenv("SHARE_REWARD", "5"))
        if not 0 <= reward <= 10000:
            raise RuntimeError("SHARE_REWARD 超出范围")
        proxy_base = os.getenv("PROXY_BASE_URL", "").strip().rstrip("/")
        proxy_enabled = os.getenv("MEDIA_PROXY_ENABLED", "false").strip().lower()
        if proxy_enabled not in ("true", "false"):
            raise RuntimeError("MEDIA_PROXY_ENABLED 必须为 true 或 false")
        return cls(url, appids, quota, reward, proxy_base, proxy_enabled == "true",
                   os.getenv("REWARDED_AD_UNIT_ID", DEFAULT_REWARDED_AD_UNIT_ID).strip())
