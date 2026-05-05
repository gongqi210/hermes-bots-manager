"""lark-cli 解析单元测试。"""

from __future__ import annotations

from app.services.lark_cli import extract_open_feishu_url


def test_extract_url_finds_open_feishu_link() -> None:
    text = "请打开浏览器访问: https://open.feishu.cn/page/cli?token=abc123 完成"
    assert (
        extract_open_feishu_url(text)
        == "https://open.feishu.cn/page/cli?token=abc123"
    )


def test_extract_url_returns_none_when_absent() -> None:
    assert extract_open_feishu_url("纯 ASCII 二维码 ████") is None


def test_extract_url_stops_at_whitespace_and_quotes() -> None:
    text = 'Open "https://open.feishu.cn/page/cli?x=1" then continue'
    assert (
        extract_open_feishu_url(text) == "https://open.feishu.cn/page/cli?x=1"
    )
