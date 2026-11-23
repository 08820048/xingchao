import pytest

from src.voicefit import unfit_reason


@pytest.mark.parametrize("text", [
    "哈哈哈你这也太离谱了吧",
    "别难过啦，明天会好起来的，我陪着你～",
    "今天天气不错，适合出去走走，记得带伞，下午可能有雨。",
    "好的",
    "我觉得可以，不过我更想吃火锅，你呢？",
    "2024年我去了三个城市，感觉还不错",
])
def test_fit(text):
    assert unfit_reason(text) is None


@pytest.mark.parametrize("text,word", [
    ("详情看这里 https://example.com/a", "链接"),
    ("发邮件到 a@b.com 就行", "链接"),
    ("用 `git status` 看一下", "代码"),
    ("```python\nprint(1)\n```", "代码"),
    ("步骤如下：\n1. 打开设置\n2. 点击账号\n3. 退出登录", "清单"),
    ("- 苹果\n- 香蕉", "清单"),
    ("发送 /积分 查看", "格式"),
    ("路径是 /etc/nginx/nginx.conf", "格式"),
    ("我的QQ是123456789，加我", "数字"),
    ("今天涨了1.5%，成交额3200万，换手率12.8%，市盈率35.6", "数字"),
    ("这个函数叫 getUserProfileById 然后 returns promise object", "英文"),
    ("😂😂😂 !!! ??? ~~~", "中文"),
    ("哈" * 160, "太长"),
    ("一\n二\n三\n四", "分段"),
])
def test_unfit(text, word):
    reason = unfit_reason(text)
    assert reason and word in reason, reason


def test_explicit_limit_allows_longer():
    assert unfit_reason("哈" * 200, limit=300) is None
