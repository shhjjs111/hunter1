"""工具注册表单元测试 —— 离线。

TDD：本文件先于实现编写，当前应为 RED。
"""

from __future__ import annotations

import pytest

from hunter1.slices.assistant.tools import ToolRegistry, tool


def _search(keyword: str = "", limit: int = 10) -> str:
    """搜索岗位。"""
    return f"找到 {limit} 条含「{keyword}」的岗位"


def _detail(job_id: str) -> str:
    """看岗位详情。"""
    if job_id == "missing":
        raise ValueError("岗位不存在")
    return f"岗位 {job_id} 的详情"


class TestTool:
    def test_from_function_builds_schema(self) -> None:
        spec = tool(_search)
        assert spec.name == "_search"
        assert spec.description == "搜索岗位。"
        schema = spec.parameters
        assert schema["type"] == "object"
        assert "keyword" in schema["properties"]
        assert "limit" in schema["properties"]
        # 有默认值的参数不该进 required
        assert schema["required"] == []

    def test_required_parameters_detected(self) -> None:
        spec = tool(_detail)
        assert spec.parameters["required"] == ["job_id"]

    def test_type_mapping(self) -> None:
        spec = tool(_search)
        assert spec.parameters["properties"]["keyword"]["type"] == "string"
        assert spec.parameters["properties"]["limit"]["type"] == "integer"

    def test_invoke_calls_function(self) -> None:
        spec = tool(_search)
        assert spec.invoke({"keyword": "产品", "limit": 3}) == "找到 3 条含「产品」的岗位"

    def test_invoke_uses_defaults(self) -> None:
        spec = tool(_search)
        assert spec.invoke({}) == "找到 10 条含「」的岗位"

    def test_schema_shape_for_openai(self) -> None:
        spec = tool(_search)
        payload = spec.as_openai_tool()
        assert payload["type"] == "function"
        assert payload["function"]["name"] == "_search"
        assert "parameters" in payload["function"]


class TestToolRegistry:
    def test_register_and_get(self) -> None:
        registry = ToolRegistry()
        registry.register(tool(_search))
        assert registry.get("_search") is not None
        assert registry.get("nope") is None

    def test_specs_is_openai_tools_array(self) -> None:
        registry = ToolRegistry([tool(_search), tool(_detail)])
        specs = registry.openai_tools()
        assert len(specs) == 2
        assert all(item["type"] == "function" for item in specs)

    def test_invoke_returns_tool_result(self) -> None:
        registry = ToolRegistry([tool(_search)])
        result = registry.invoke("_search", {"keyword": "测试", "limit": 1}, call_id="c1")
        assert result.call_id == "c1"
        assert result.ok
        assert "测试" in result.content

    def test_unknown_tool_is_reported_not_raised(self) -> None:
        registry = ToolRegistry()
        result = registry.invoke("ghost", {}, call_id="c1")
        assert not result.ok
        assert "ghost" in (result.error or "")

    def test_tool_exception_becomes_error_result(self) -> None:
        """工具抛异常不能炸掉整个对话循环 —— 转成 error 结果让模型看见。"""
        registry = ToolRegistry([tool(_detail)])
        result = registry.invoke("_detail", {"job_id": "missing"}, call_id="c1")
        assert not result.ok
        assert "岗位不存在" in (result.error or "")

    def test_unknown_argument_is_reported_helpfully(self) -> None:
        """多传参数时，错误要告诉模型「本工具接受什么」。"""
        registry = ToolRegistry([tool(_detail)])
        result = registry.invoke("_detail", {"wrong_param": 1}, call_id="c1")
        assert not result.ok
        assert "wrong_param" in (result.error or "")
        assert "job_id" in (result.error or "")  # 顺带告知正确的参数名

    def test_missing_required_argument_is_reported(self) -> None:
        registry = ToolRegistry([tool(_detail)])
        result = registry.invoke("_detail", {}, call_id="c1")
        assert not result.ok
        assert "缺少必填参数" in (result.error or "")
        assert "job_id" in (result.error or "")

    def test_duplicate_registration_rejected(self) -> None:
        registry = ToolRegistry([tool(_search)])
        with pytest.raises(ValueError):
            registry.register(tool(_search))

    def test_names_are_sorted_and_unique(self) -> None:
        registry = ToolRegistry([tool(_detail), tool(_search)])
        names = [spec["function"]["name"] for spec in registry.openai_tools()]
        assert names == ["_detail", "_search"]


class TestPublicToolNames:
    def test_tool_accepts_explicit_name(self) -> None:
        spec = tool(_search, name="search_jobs")
        assert spec.name == "search_jobs"
        assert spec.as_openai_tool()["function"]["name"] == "search_jobs"

    def test_tool_accepts_explicit_description(self) -> None:
        spec = tool(_search, name="search_jobs", description="按关键词搜岗位")
        assert spec.description == "按关键词搜岗位"


def test_annotation_to_json_type() -> None:
    """四种基础类型都要正确映射（用真实函数写法，不用闭包参数化）。"""

    def _str_tool(x: str) -> str:
        return ""

    def _int_tool(x: int) -> str:
        return ""

    def _float_tool(x: float) -> str:
        return ""

    def _bool_tool(x: bool) -> str:
        return ""

    assert tool(_str_tool).parameters["properties"]["x"]["type"] == "string"
    assert tool(_int_tool).parameters["properties"]["x"]["type"] == "integer"
    assert tool(_float_tool).parameters["properties"]["x"]["type"] == "number"
    assert tool(_bool_tool).parameters["properties"]["x"]["type"] == "boolean"


def test_unknown_annotation_defaults_to_string() -> None:
    def _odd_tool(x: list[str]) -> str:  # 未支持的类型 → 安全退化为 string
        return ""

    assert tool(_odd_tool).parameters["properties"]["x"]["type"] == "string"
