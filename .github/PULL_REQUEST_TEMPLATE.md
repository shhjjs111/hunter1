<!--
PR 标题遵循 Conventional Commits：<type>(<scope>): <说明>
type 取 feat / fix / refactor / docs / test / chore / perf
-->

## 动机

<!-- 为什么需要这个改动？解决什么问题？关联的 Issue：Closes # -->

## 改动点

<!-- 主要改了什么。跨切片时按切片分条。 -->

-

## 验证

<!-- 贴真实输出，不要写「应该没问题」 -->

- [ ] `bash scripts/check.sh` 全绿（贴退出码或尾部）
- [ ] 新行为 / 缺陷修复**有测试**；缺陷修复的测试能复现原缺陷（RED → GREEN）
- [ ] 改了 API 形状 ⇒ 已跑 `bash scripts/contracts.sh` 并**连快照一起提交**
- [ ] 未把无关改动卷进本 PR

```
（门禁输出）
```

## 设计取舍

<!--
有的话说明「为什么这样做而不是那样」——尤其是偏离既有约定的地方。
没有就删掉本节。
-->

## 自查

- [ ] 未深链其他切片的内部模块（只经 `__init__.py` 公开面）
- [ ] 未违反依赖方向白名单（改动越权时已同步 `test_architecture.py`）
- [ ] 路由响应有类型（未返回裸 `dict`）
- [ ] 无临时探针残留（`console.log` / `assert` / `debugger`）
- [ ] 无密钥、令牌、本机路径进入提交
