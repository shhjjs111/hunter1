"""更新清单与版本规则的测试 —— 纯逻辑，无 IO。

版本比较是更新功能里最容易出错、又最难看出来的地方：按字符串比较会让
`1.10` 小于 `1.9`，于是新版本被判成旧版本，用户永远等不到更新，而且
不会有任何报错。所以这里把它钉死。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hunter1.domain.update import (
    ReleaseAsset,
    ReleaseManifest,
    is_newer,
    parse_version,
)

VALID_SHA = "a" * 64


def _asset(**overrides: object) -> ReleaseAsset:
    base: dict[str, object] = {
        "platform": "win32",
        "url": "https://example.com/hunter1-0.2.0-win32.zip",
        "sha256": VALID_SHA,
    }
    base.update(overrides)
    return ReleaseAsset(**base)  # type: ignore[arg-type]


class TestParseVersion:
    def test_reads_dotted_numbers(self) -> None:
        assert parse_version("0.2.0") == (0, 2, 0)

    def test_tolerates_v_prefix(self) -> None:
        assert parse_version("v1.2.3") == (1, 2, 3)

    def test_tolerates_whitespace(self) -> None:
        assert parse_version("  1.0  ") == (1, 0)

    def test_ignores_trailing_junk(self) -> None:
        # 形如 1.2.3-beta / 1.2.3+local 的常见写法
        assert parse_version("1.2.3-beta.1") == (1, 2, 3)

    @pytest.mark.parametrize("bad", ["", "   ", "latest", "v"])
    def test_rejects_unparseable(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_version(bad)


class TestIsNewer:
    def test_detects_newer(self) -> None:
        assert is_newer("0.2.0", "0.0.1") is True

    def test_detects_older(self) -> None:
        assert is_newer("0.0.1", "0.2.0") is False

    def test_equal_is_not_newer(self) -> None:
        assert is_newer("0.2.0", "0.2.0") is False

    def test_missing_patch_component_is_equal(self) -> None:
        assert is_newer("1.0", "1.0.0") is False

    def test_multi_digit_components_compare_numerically(self) -> None:
        """按字符串比会得出 1.10 < 1.9 —— 这条就是防它的。"""
        assert is_newer("1.10.0", "1.9.0") is True

    def test_minor_beats_patch(self) -> None:
        assert is_newer("1.2.0", "1.1.99") is True


class TestReleaseAsset:
    def test_requires_http_url(self) -> None:
        with pytest.raises(ValidationError):
            _asset(url="ftp://example.com/a.zip")

    def test_requires_64_hex_sha256(self) -> None:
        with pytest.raises(ValidationError):
            _asset(sha256="abc")

    def test_sha256_is_normalised_to_lowercase(self) -> None:
        assert _asset(sha256="A" * 64).sha256 == "a" * 64

    def test_size_is_optional(self) -> None:
        assert _asset().size is None

    def test_negative_size_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _asset(size=-1)

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _asset(mirror="https://mirror.example.com")


class TestReleaseManifest:
    def _manifest(self, **overrides: object) -> ReleaseManifest:
        base: dict[str, object] = {
            "version": "0.2.0",
            "assets": [{"platform": "win32", "url": "https://a.com/w.zip", "sha256": VALID_SHA}],
        }
        base.update(overrides)
        return ReleaseManifest(**base)  # type: ignore[arg-type]

    def test_parses_json(self) -> None:
        manifest = ReleaseManifest.model_validate_json(
            '{"version": "0.3.0", "notes": "修了几个 bug",'
            ' "assets": [{"platform": "linux", "url": "https://a.com/l.zip",'
            ' "sha256": "' + VALID_SHA + '"}]}'
        )
        assert manifest.version == "0.3.0"
        assert manifest.notes == "修了几个 bug"

    def test_asset_for_platform(self) -> None:
        manifest = self._manifest()
        asset = manifest.asset_for("win32")
        assert asset is not None and asset.url.endswith("w.zip")

    def test_asset_for_unknown_platform_is_none(self) -> None:
        """没有对应平台的产物时要能说清「没有」，而不是给一个错的。"""
        assert self._manifest().asset_for("plan9") is None

    def test_assets_default_to_empty(self) -> None:
        assert ReleaseManifest(version="0.1.0").assets == []

    def test_empty_version_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReleaseManifest(version="")
