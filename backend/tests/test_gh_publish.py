"""令牌读取逻辑的测试。

这里只测**安全敏感的纯逻辑**（令牌从哪来、拿不到时怎么报错）。网络部分
（校验、创建仓库、上传）不在单测范围内 —— 那需要真令牌，不适合进测试套件。

重点守两条不变量：
1. 环境变量优先于文件（便于 CI 注入，不必落盘）；
2. 拿不到令牌时**报错并给出操作方法**，不是静默继续 —— 静默继续会让后续
   请求以匿名身份打出去，得到一堆 404/401，很难倒查到「其实是没配令牌」。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gh_publish_script", ROOT / "scripts" / "gh_publish.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gh = _load()


class TestReadToken:
    def test_env_takes_priority(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "  from_env  ")
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", Path("/nonexistent"))
        assert gh.read_token() == "from_env"

    def test_falls_back_to_file(self, monkeypatch, tmp_path: Path) -> None:
        token_file = tmp_path / ".hunter1_gh_token"
        token_file.write_text("from_file\n", encoding="utf-8")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", token_file)
        assert gh.read_token() == "from_file"

    def test_blank_env_is_ignored_in_favour_of_file(self, monkeypatch, tmp_path: Path) -> None:
        """空的环境变量不该被当成有效令牌（CI 里常见 `GITHUB_TOKEN=` 这种空赋值）。"""
        token_file = tmp_path / ".hunter1_gh_token"
        token_file.write_text("from_file", encoding="utf-8")
        monkeypatch.setenv("GITHUB_TOKEN", "   ")
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", token_file)
        assert gh.read_token() == "from_file"

    def test_missing_everything_raises_with_instructions(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(gh, "DEFAULT_TOKEN_FILE", tmp_path / "absent")
        with pytest.raises(gh.PublishError) as excinfo:
            gh.read_token()
        message = str(excinfo.value)
        assert "gh_setup.sh" in message  # 告诉用户怎么补
        assert "GITHUB_TOKEN" in message


class TestNoTokenLeakInArgv:
    def test_remote_url_is_built_without_token(self) -> None:
        """远端地址里不该出现令牌 —— 那会写进 .git/config 并可被读到。"""
        source = (ROOT / "scripts" / "gh_publish.py").read_text(encoding="utf-8")
        assert "{owner}/{repo}.git" in source  # 地址模板不含凭据占位
        # 令牌只经 GIT_ASKPASS 的临时脚本传递
        assert "GIT_ASKPASS" in source
        assert "HUNTER1_GH_TOKEN" in source
