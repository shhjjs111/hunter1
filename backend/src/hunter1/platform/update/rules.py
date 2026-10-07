"""更新清单与版本规则 —— 纯模型与规则，无 IO。

自更新最容易出错的一步是**版本比较**，而它出错时完全不响：把新版本判成旧版本，
用户就永远等不到更新，日志里也没有任何异常。因此规则独立成纯函数并单测。

另一个要点是**校验和是一等公民**：清单里每个产物都带 `sha256`，缺失或格式
不对直接拒绝解析 —— 拿到一个没法校验的包，比拿不到包更危险。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)")
# version 会被当成**路径成分**用（`dest_dir / latest`、`hunter1-{latest}.zip`），
# 所以含分隔符 / 上跳 / 盘符的值必须挡在解析层。`..` 单独列是因为它不带分隔符
# 也可能成段（`..` 自身就是一个路径段）。
_PATH_IN_VERSION = re.compile(r"[/\\]|\.\.|^[A-Za-z]:")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReleaseAsset(_Model):
    """某个平台的一份可下载产物。"""

    platform: str
    url: str
    sha256: str
    size: int | None = Field(default=None, ge=0)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError(f"下载地址必须是 http(s)：{value!r}")
        return cleaned

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        if not _HEX64.match(cleaned):
            raise ValueError("sha256 必须是 64 位十六进制")
        return cleaned

    @field_validator("platform")
    @classmethod
    def _platform(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("platform 不能为空")
        return cleaned


class ReleaseManifest(_Model):
    """一份版本清单（默认从 GitHub Release 或自建镜像取）。"""

    version: str
    notes: str | None = None
    published_at: str | None = None
    assets: list[ReleaseAsset] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _non_empty_version(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("version 不能为空")
        # 只查非空是不够的：`version` 随后被当成路径成分
        # （`prepare_update` 的 `dest_dir / status.latest` 与 `hunter1-{latest}.zip`）。
        # 一个伪造的清单只要把 version 写成 `9.9.9\..\..\..\evil`，解压就会落到底
        # 目录之外 —— 而它还能通过 `parse_version`（`.match` 只锚开头）。
        # 在解析层就挡掉，比在下载路径上补更靠前。
        #
        # 兼容红线：`v` 前缀与 `-beta`/`+local` 后缀是 `is_newer` 的既有语义，
        # 本校验不触碰它们（那些值不含分隔符/上跳/盘符）。
        if _PATH_IN_VERSION.search(cleaned):
            raise ValueError(f"version 不能含路径分隔符、上跳或盘符：{value!r}")
        return cleaned

    def asset_for(self, platform: str) -> ReleaseAsset | None:
        """取某个平台的产物；没有就返回 None（不猜、不退回别的平台）。"""
        for asset in self.assets:
            if asset.platform == platform:
                return asset
        return None


def parse_version(text: str) -> tuple[int, ...]:
    """把版本号解析成可比较的数字元组。

    容忍 `v` 前缀与 `-beta` / `+local` 这类后缀（取主干数字）。
    无法解析时抛 `ValueError` —— 宁可报错，也不要拿一个错误的比较结果去
    决定「要不要更新」。
    """
    match = _VERSION.match((text or "").strip())
    if match is None:
        raise ValueError(f"无法解析版本号：{text!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    """`candidate` 是否比 `current` 新。

    用数字元组比较，所以 `1.10.0 > 1.9.0`（字符串比较会判反）；
    `1.0` 与 `1.0.0` 视为相同。
    """
    return parse_version(candidate) > parse_version(current)


__all__ = ["ReleaseAsset", "ReleaseManifest", "is_newer", "parse_version"]
