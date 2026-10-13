# 站点快照（fixtures）

「选择器还认得真实页面」的离线证据。`tests/slices/crawl/test_sites.py` 用
`available_sites()` 逐个回读同名快照，断言每个站点都能从中抽出结构化岗位
（标题/公司/链接非空、URL 去重）。

生成/刷新用 `scripts/refresh_fixtures.py`（裁剪到前 N 条 + 祖先链，去掉脚本与样式）。

## `zhipin.html` 没有对应的注册站点

`zhipin`（BOSS直聘）已于 2026-10-13 从 `slices/crawl/sites.py` 的 `SITES` 移除 ——
原因见那里的注释：该站对所有 HTML 入口一律返回「安全验证」页，属**结构性不可用**，
不是偶发失败。

快照**刻意保留**在此，与 `sites.py` 里留档的选择器配套：若将来该站放开匿名列表页，
把 `SiteDefinition` 加回后这条快照可以直接复用作回归样本，不必重新抓取。

它不会被 `test_sites.py` 主动回读（该文件的参数化按 `available_sites()` 派生，
而 `zhipin` 已不在其中），所以留在这里**不会**让任何测试变红 —— 这一点是本文件
存在的理由：避免下一个人看到孤儿文件就顺手删掉，把留档的证据一起删没。
