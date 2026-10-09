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
# 控制字符同样不能进 version：它会被拼进文件名/目录名（`dest_dir / latest`、
# `hunter1-{latest}.zip`），在 Windows 上触发非法路径异常，且肉眼不可见、
# 排查成本高。藏在解析层挡掉，比在下载路径上补更靠前。
_CONTROL_IN_VERSION = re.compile(r"[\x00-\x1f\x7f]")
# 上面两条还不够：`version` 会原样成为**文件名/目录名**，Windows 的文件名规则
# 必须在这里一并挡住。实测（未修前）这些值全部放行：
#   `1:2`  → `hunter1-1:2.zip` 在 NTFS 上是「交替数据流」，写的是别的文件；
#   `CON`  → `dest_dir/CON` 指向**控制台设备**而不是目录；
#   `1.0.` → 尾点被 Windows 静默剥掉，与 `1.0` 撞成同一个目录（版本串味）；
#   `1.0.0?x` / `1.0|0` → 非法字符，拼出来的路径在 Windows 上直接不可用。
_WINDOWS_BAD_IN_VERSION = re.compile(r'[<>:"|?*]')
# 尾点/尾空格会被 Windows 静默剥掉 → 两个不同版本落进同一个目录。
_TRAILING_DOT_OR_SPACE = re.compile(r"[. ]$")
# 保留设备名：大小写不敏感，且**带扩展名也保留**（`CON.txt` 同样指向设备）。
_WINDOWS_RESERVED_IN_VERSION = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]|CONIN\$|CONOUT\$)(?:\..*)?$",
    re.IGNORECASE,
)


# ---- 体积闸门（防「解压炸弹」）----
#
# 自更新链此前只有路径与校验和两道防线：**没有任何体积闸门**。清单未签名
# （见 ARCHITECTURE.md），恶意包无需改清单就能塞进来；`ReleaseAsset.size` 定义
# 了却全链路无人消费（只有 make_manifest 的一处日志打印）。实测：4987 字节的
# zip 解压出 5,000,000 字节，全程无拦截。
#
#: 下载体积上限（压缩包本身）。便携工具的正常产物在几十 MB 量级，200MB 很宽松。
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
#: 解压后**总体积**上限。zip 炸弹靠「几 KB 压成几 GB」，这是最后的兜底。
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024
#: 膨胀比上限：解压后总体积不得超过压缩包的这么多倍（挡住「绝对体积不大但
#: 比例极夸张」的包，例如 5KB → 5MB）。
MAX_EXPANSION_RATIO = 100
#: 压缩包**条目数**上限。体积两道闸门对「海量 0 字节成员」都不生效（声明总量≈0、
#: 膨胀比也低），但每个成员都会创建文件系统条目 —— 数百万个足以耗尽 inode/目录项。
#: 正常产物是单个程序目录，几千个条目已很宽松。
MAX_ENTRIES = 100_000


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
        if _CONTROL_IN_VERSION.search(cleaned):
            raise ValueError(f"version 不能含控制字符：{value!r}")
        # 见上面三组正则的说明：version 会成为文件名/目录名，Windows 的文件名
        # 规则必须一并满足，否则失败方式不是「报错」而是「写错地方/写不进去」。
        if _WINDOWS_BAD_IN_VERSION.search(cleaned):
            raise ValueError(f'version 不能含 Windows 非法字符（<>:"|?*）：{value!r}')
        if _TRAILING_DOT_OR_SPACE.search(cleaned):
            raise ValueError(f"version 不能以点或空格结尾（Windows 会剥掉它）：{value!r}")
        if _WINDOWS_RESERVED_IN_VERSION.match(cleaned):
            raise ValueError(f"version 不能是 Windows 保留设备名：{value!r}")
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


def _canonical(version: tuple[int, ...]) -> tuple[int, ...]:
    """去掉尾部的 0 —— 让 `1.0` 与 `1.0.0` 在比较时是同一个版本。"""
    end = len(version)
    while end > 0 and version[end - 1] == 0:
        end -= 1
    return version[:end]


def is_newer(candidate: str, current: str) -> bool:
    """`candidate` 是否比 `current` 新。

    用数字元组比较，所以 `1.10.0 > 1.9.0`（字符串比较会判反）；
    `1.0` 与 `1.0.0` 视为相同 —— **两个方向都要相等**。原先直接比原始元组，
    而 Python 元组在公共前缀相同时「更长者更大」，于是
    `is_newer("1.0.0", "1.0")` 返回 True（反方向却 False）：运行版本是两段式、
    清单写三段式时会误报「可更新」并重复下载解压同一版本。
    """
    return _canonical(parse_version(candidate)) > _canonical(parse_version(current))


__all__ = [
    "MAX_DOWNLOAD_BYTES",
    "MAX_ENTRIES",
    "MAX_EXPANSION_RATIO",
    "MAX_EXTRACTED_BYTES",
    "ReleaseAsset",
    "ReleaseManifest",
    "is_newer",
    "parse_version",
]
