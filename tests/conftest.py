"""
pytest 公共夹具

提供不依赖真实 LLM / embedding / 网络的离线替身：
- FakeChatModel：可编程返回内容的聊天模型（异步）
- temp_db：每个测试独立的 SQLite 临时库（构造即建表）
"""

import pytest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


class FakeMessage:
    def __init__(self, content: str):
        self.content = content


class FakeChatModel:
    """离线假聊天模型：ainvoke 固定返回构造时指定的内容，绝不触网"""

    def __init__(self, content: str = ""):
        self._content = content

    async def ainvoke(self, messages=None, **kwargs):
        return FakeMessage(self._content)

    async def __call__(self, *args, **kwargs):
        return FakeMessage(self._content)


@pytest.fixture
def temp_db_path(tmp_path) -> str:
    """返回临时 SQLite 文件路径（引擎尚未创建，SessionManager 构造时建表）"""
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def tmp_engine(tmp_path):
    """内存引擎 + StaticPool，用于验证 ORM 模型表结构（不触碰真实库）"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return engine
