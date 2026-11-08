import re
from dataclasses import dataclass
from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Identity:
    appid: str
    openid: str


def trusted_identity(request: Request) -> Identity:
    # Safe ONLY behind the private WeChat gateway enforced by deployment.
    # X-WX-SOURCE / X-Forwarded-For are deliberately not treated as proof.
    values = []
    for name in ("x-wx-appid", "x-wx-openid"):
        headers = request.headers.getlist(name)
        if len(headers) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", headers[0]):
            raise HTTPException(401, detail="缺少有效微信可信身份")
        values.append(headers[0])
    appid, openid = values
    if appid not in request.app.state.settings.allowed_appids:
        raise HTTPException(403, detail="小程序未获授权")
    return Identity(appid, openid)
