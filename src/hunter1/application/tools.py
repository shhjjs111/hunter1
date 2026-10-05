"""工具注册表 —— 给助手用的「能力」集合。

设计目标：**新增一个工具 = 写一个带类型标注的函数 + 用 `@tool` 装饰**，
JSON Schema 自动生成，不手写、不漏字段。注册表把工具异常一律转成
`ToolResult(error=...)`，让模型看见失败并自我纠正，而不是炸掉整个对话。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hunter1.domain.assistant import ToolResult

_JSON_TYPES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}

# 字符串注解（`from __future__ import annotations` 下的常见形态）到 JSON 类型的映射
_JSON_TYPES_BY_NAME: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
}


@dataclass(frozen=True)
class Tool:
    """一个可被助手调用的工具。"""

    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., str]

    def invoke(self, arguments: dict[str, Any]) -> str:
        return self.func(**arguments)

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool(
    func: Callable[..., str],
    *,
    name: str | None = None,
    description: str | None = None,
) -> Tool:
    """从一个带类型标注的函数构建 `Tool`。

    不调用 `typing.get_type_hints` —— 本仓库模块普遍带
    `from __future__ import annotations`，注解是字符串；`get_type_hints` 会去
    eval 它们，遇到局部/闭包定义会 NameError。这里只做**名字到类型的静态映射**，
    既不 eval 任意表达式，也覆盖了工具参数用得到的全部类型。
    """
    signature = inspect.signature(func)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in signature.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        json_type = _json_type_of(param.annotation)
        entry: dict[str, Any] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
        properties[param_name] = entry

    return Tool(
        name=name or func.__name__,
        description=(description or (inspect.getdoc(func) or "")).strip(),
        parameters={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        func=func,
    )


def _json_type_of(annotation: object) -> str:
    """把参数注解决定为 JSON Schema 类型。字符串注解按名字映射。"""
    if isinstance(annotation, str):
        return _JSON_TYPES_BY_NAME.get(annotation.strip(), "string")
    return _JSON_TYPES.get(annotation, "string")  # type: ignore[arg-type]


class ToolRegistry:
    """一组具名工具。名字唯一。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for item in tools or []:
            self.register(item)

    def register(self, item: Tool) -> None:
        if item.name in self._tools:
            raise ValueError(f"tool already registered: {item.name}")
        self._tools[item.name] = item

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def openai_tools(self) -> list[dict[str, Any]]:
        return [self._tools[name].as_openai_tool() for name in self.names()]

    def invoke(self, name: str, arguments: dict[str, Any], *, call_id: str = "") -> ToolResult:
        """执行一个工具。**任何失败都转成 error 结果，绝不抛出。**"""
        item = self._tools.get(name)
        if item is None:
            return ToolResult(
                call_id=call_id,
                name=name,
                error=f"未知工具 {name}；可用工具：{', '.join(self.names()) or '(无)'}",
            )

        # 先按 schema 校验，给出「缺什么/多什么」这种模型能直接纠正的错误
        problem = _validate_arguments(item.parameters, arguments)
        if problem is not None:
            return ToolResult(call_id=call_id, name=name, error=problem)

        try:
            output = item.invoke(arguments)
        except Exception as exc:
            return ToolResult(call_id=call_id, name=name, error=f"{type(exc).__name__}: {exc}")
        return ToolResult(call_id=call_id, name=name, content=str(output))


def _validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """返回参数问题描述，没问题返回 None。

    一次把「缺什么」和「多什么」都报出来 —— 让模型一轮就能改对，
    而不是修完一个再撞下一个。
    """
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    problems: list[str] = []
    missing = [key for key in required if key not in arguments]
    if missing:
        problems.append(f"缺少必填参数: {', '.join(missing)}")

    unknown = [key for key in arguments if key not in properties]
    if unknown:
        problems.append(
            f"不认识的参数: {', '.join(unknown)}；本工具接受: {', '.join(properties) or '(无)'}"
        )

    return "；".join(problems) if problems else None


__all__ = ["Tool", "ToolRegistry", "tool"]
