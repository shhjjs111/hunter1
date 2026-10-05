"""端到端演示：求职助手完整链路（离线可跑）。

用法：

    ./.tools/python/python.exe examples/demo_assistant.py            # 离线（脚本化模型）
    ./.tools/python/python.exe examples/demo_assistant.py --live     # 接真实模型（需配 key）

离线模式用一个「脚本化模型」扮演 LLM：先请求调 `search_jobs`，拿到结果后再给出
自然语言回答。这样能在没有任何 API key 的情况下，验证**整条链路**：
    用户消息 → 组装上下文 → 模型请求工具 → 执行真实工具（查 SQLite）
    → 结果回灌 → 模型给答复 → 会话落库 → 读回
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hunter1.application.assistant import run_turn
from hunter1.application.job_tools import build_tools
from hunter1.domain.assistant import Message, Role, ToolCall
from hunter1.domain.llm import LLMResponse
from hunter1.domain.models import CaptureStatus, Job
from hunter1.infrastructure.db import Database


class ScriptedLLM:
    """扮演模型：第一轮请求调工具，第二轮给出自然语言回答。"""

    def __init__(self) -> None:
        self.round = 0

    def complete_with_tools(self, *, messages, tools, max_tokens=None) -> LLMResponse:
        self.round += 1
        if self.round == 1:
            # 模型看到用户问「产品岗」，决定先查
            return LLMResponse(
                content="",
                model="scripted",
                tool_calls=[
                    ToolCall(id="call_1", name="search_jobs", arguments={"keyword": "产品经理"})
                ],
            )
        # 第二轮：读回灌的工具结果，组织成回答
        tool_content = next((m.content for m in reversed(messages) if m.role is Role.TOOL), "")
        return LLMResponse(content=f"帮你查到这些：\n{tool_content}", model="scripted")

    # 助手只用 complete_with_tools，另两个是端口要求
    def complete(self, **_kw) -> LLMResponse:
        raise NotImplementedError

    def complete_structured(self, **_kw) -> LLMResponse:
        raise NotImplementedError


def _seed(db: Database) -> None:
    repo = db.jobs()
    seed = [
        ("AI产品经理", "北京", 88),
        ("大模型产品经理（2027校招）", "上海", 82),
        ("数据产品经理", "深圳", None),
        ("行政专员", "北京", None),
    ]
    for index, (title, city, score) in enumerate(seed):
        repo.upsert(
            Job(
                id=f"demo{index}".ljust(32, "0"),
                company_id="c1",
                title=title,
                detail_url=f"https://example.com/job/{index}",
                source="demo",
                city=city,
                match_score=score,
                capture_status=CaptureStatus.COMPLETE,
                jd_raw=f"{title}的岗位描述。",
                last_seen_at=datetime(2026, 10, 1 + index, tzinfo=UTC),
            )
        )


def main(argv: list[str]) -> int:
    live = "--live" in argv
    db_path = Path(tempfile.mkdtemp(prefix="hunter1-asst-")) / "demo.db"
    db = Database(db_path)
    db.initialize()
    _seed(db)
    print(f"岗位库已就绪：{db.jobs().count()} 条岗位\n")

    if live:
        from hunter1.infrastructure.llm import resolve_preset

        api_key = os.environ.get("HUNTER1_API_KEY", "")
        if not api_key:
            print("未设置 HUNTER1_API_KEY，无法使用 --live。")
            return 1
        llm = resolve_preset(os.environ.get("HUNTER1_PROVIDER", "deepseek"), api_key=api_key)
        print(f"[真实模型] {llm.base_url} / {llm.model}\n")
    else:
        llm = ScriptedLLM()  # type: ignore[assignment]
        print("[离线模式] 用脚本化模型演示完整链路\n")

    # 1) 建会话并写入用户消息
    conversations = db.conversations()
    conversation = conversations.create(title="找产品岗")
    user_message = Message(Role.USER, "帮我看看有哪些产品经理的岗位")
    conversations.append(conversation.id, user_message)
    print(f"用户：{user_message.content}")

    # 2) 跑助手回合
    registry = build_tools(jobs=db.jobs())
    result = run_turn(llm=llm, registry=registry, messages=[user_message])

    # 3) 打印工具调用轨迹
    print(f"\n[助手跑了 {result.iterations} 轮，调用了 {len(result.tool_results)} 次工具]")
    for tool_result in result.tool_results:
        mark = "OK" if tool_result.ok else "ERR"
        print(
            f"  · [{mark}] {tool_result.name}: {tool_result.content[:80].replace(chr(10), ' / ')}"
        )

    print(f"\n助手：\n{result.reply}\n")

    # 4) 落库并读回，证明会话持久化
    conversations.append(conversation.id, Message(Role.ASSISTANT, result.reply))
    saved = conversations.messages(conversation.id)
    print(f"会话已落库：{len(saved)} 条消息")
    for message in saved:
        print(f"  [{message.role}] {message.content[:60]}")

    # 5) 校验
    ok = (
        result.iterations == 2
        and len(result.tool_results) == 1
        and result.tool_results[0].ok
        and "AI产品经理" in result.reply
        and len(saved) == 2
    )
    db.dispose()
    print("\nPASS" if ok else "\n[FAIL] 演示未达预期")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
