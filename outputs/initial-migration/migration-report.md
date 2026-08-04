# Adaptive Skills 首次迁移报告

日期：2026-08-04

## 结果摘要

- 已发现并登记 23 个顶层 Git 仓库。
- 已扫描 348 个 Git 管理的 Skill，其中 327 个通过结构校验，21 个未通过。
- 已从原始工作簿导入 348 条分类与评分注释；无歧义匹配。
- 原工作簿保持不变，另行生成包含最新扫描数据和 `Sources` 工作表的新工作簿。
- 所有源仓库的 HEAD 与迁移前一致，已有工作区脏状态计数也保持一致。

## 输入与输出完整性

| 项目 | 路径 | SHA-256 |
|---|---|---|
| 原始工作簿 | `/Users/leowang/skills/skills-inventory.xlsx` | `85b0a8ba34d1933347c943f1c4700889f8c6165d7ddf5a07ec7964d58af0f1ba` |
| 更新工作簿 | `/Users/leowang/projects/adaptive-skills/outputs/initial-migration/skills-inventory.updated.xlsx` | `383f3b82d7eff82d92963130cc8df627eb838a3d56bea9e7777dd3026be5e4d8` |

更新工作簿包含 5 个工作表：

- `技能总表`：349 行 × 22 列（表头 + 348 个 Skill）
- `Sources`：24 行 × 9 列（表头 + 23 个 Git 源）
- `场景速查`：保留原表
- `Arena 基准原始数据`：保留原表
- `评分说明`：保留原表

公式错误扫描未发现 `#REF!`、`#DIV/0!`、`#VALUE!`、`#NAME?` 或 `#N/A`。5 个工作表均已渲染并完成视觉检查。

## 注释迁移

- 已导入：348 行
- 一级分类已填充：348 行
- 评分已填充：348 行
- 歧义：0 行
- 未匹配：2 行

未匹配项均位于非 Git 管理目录，因此没有被本次 Git 源发现流程纳入目录：

| 原表行 | Skill | 路径 |
|---:|---|---|
| 334 | `skillshare` | `skillshare/SKILL.md` |
| 351 | `ai-api` | `wisers-skills/ai-api/SKILL.md` |

## 校验与风险

21 个未通过结构校验的 Skill 默认不会被项目应用：

| 校验规则 | 数量 |
|---|---:|
| 目录名与 frontmatter `name` 不一致 | 19 |
| 缺少 frontmatter `name` | 1 |
| `description` 超过 1024 字符 | 1 |

风险审计分布中有 10 个 `critical` 和 7 个 `high`。系统默认阻止应用这些 Skill，只有显式传入风险接受参数才允许继续。

### Critical

- Happycapy-skills：`ai-image-generation`、`ai-video-generation`
- autoresearch：两个 `autoresearch` 入口
- gstack：`gstack`、`browse`、`careful`
- opc-skills：两个 `seo-geo` 入口、`requesthunt`

### High

- Happycapy-skills：`happycapy-feishu`、`mobile-design`
- adaptive-workloop：`adaptive-workloop`
- baoyu-skills：`baoyu-post-to-wechat`
- gstack：`make-pdf`、`setup-browser-cookies`、`setup-gbrain`

## 验证证据

- 真实目录检索“制作演示文稿”时，首个结果为 Happycapy-skills 的 `pptx`，随后包含 `ljg-present`、`frontend-slides` 和 `revealjs`。
- 在临时项目中成功以软链接方式应用 `pptx`；项目状态为 `clean`。
- `source discover` 再次执行返回空列表，证明发现流程幂等。
- 自动测试：17/17 通过。
- Ruff 检查与格式检查通过；Python 源码编译检查通过。

## 后续决策

1. 为 `skillshare` 与 `wisers-skills/ai-api` 补充 Git 来源，或在后续版本加入显式的本地目录源类型。
2. 逐项复核 17 个高风险 Skill，决定保留、隔离或白名单化。
3. 修复或豁免 21 个结构校验失败项后重新扫描。
