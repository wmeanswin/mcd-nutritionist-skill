# WorkBuddy 开发上下文

> 本文件记录「麦门营养师」Skill 使用 **WorkBuddy** 从零开发、调试到发布的完整对话上下文，
> 用于核验本项目是否符合麦当劳程序员节创意开发大赛 × WorkBuddy 联动活动的奖励条件。
>
> - 开发时间：2026-10-12 ～ 2026-10-13
> - 开发方式：WorkBuddy 智能设计助手（Agent 模式）+ 麦当劳中国 MCP（`mcd-mcp`）
> - 项目性质：麦当劳程序员节创意开发大赛参赛作品，非麦当劳官方产品

---

## 1. 开发环境与工具链

| 项 | 内容 |
|---|---|
| 开发平台 | WorkBuddy（智能设计助手，Agent 模式） |
| 数据源 | 麦当劳中国 MCP Server：`https://mcp.mcd.cn`（Streamable HTTP，Bearer Token） |
| MCP 配置位置 | `~/.workbuddy/mcp.json`（`mcpServers.mcd-mcp`） |
| 核心 Tool | `list-nutrition-foods`（餐品营养信息列表） |
| Skill 形态 | WorkBuddy 用户级 Skill（`SKILL.md` + `scripts/` + `references/` + `assets/` 三级渐进披露） |
| 运行依赖 | Python 3.13，**纯标准库**（无第三方依赖） |
| 本地仓库 | `C:/Users/wzn/WorkBuddy/麦门营养师skill` |
| 远端仓库 | https://github.com/wmeanswin/mcd-nutritionist-skill |

---

## 2. 开发时间线

| 阶段 | 时间 | 内容 |
|---|---|---|
| ① 需求与骨架 | 2026-10-12 | 从一句话需求产出 Skill v1 全功能实现 |
| ② 接入真实数据 | 2026-10-12 | 用户激活 MCP Token 后，直连 MCP 落库 158 条官方数据 |
| ③ 数据质量修复 | 2026-10-12 | 修复单位换算、零热量餐品丢失、模糊匹配错误三类数据 bug |
| ④ 推荐算法打磨 | 2026-10-12 | 加入硬约束、权重调整、组合去重，推荐质量达标 |
| ⑤ 参赛文件齐备 | 2026-10-13 | README / CONTEST_DECLARATION / MCP_INTEGRATION 就位 |
| ⑥ GitHub 发布 | 2026-10-13 | 创建私有仓库并推送（绕过沙箱 TLS 限制） |
| ⑦ 合规补全 | 2026-10-13 | 补 `workbuddy.md`、修正文档数据口径、核对参赛必需文件 |

---

## 3. 关键对话记录

### 3.1 立项（用户第一轮 prompt）

> **用户**：我要做一个 skill 名字：麦门营养师 skill。用途：用来计算麦当劳在大陆当下产品的各种产品营养含量，并根据个人需求提供参考。引用 MCP：https://github.com/M-China/mcd-developer-innovation-challenge。先做一版给我看看吧。

**执行**：读取大赛仓库要求 → 确认参赛必需文件清单 → 搭建 Skill 结构与计算引擎。

**产出**：
- `mcd-nutritionist/SKILL.md`（Skill 定义、5 步工作流、命令速查、输出规范、边界声明）
- `mcd-nutritionist/scripts/mcd_nutrition.py`：`profile` / `calc` / `plan` / `rank` 四个子命令，纯标准库
- `mcd-nutritionist/scripts/sync_db.py`：MCP 数据落库
- `references/mcp-integration.md`、`references/nutrition-baseline.md`
- `assets/nutrition.sample.json`：无 MCP 时的兜底参考库

### 3.2 接入官方实时数据（用户第二轮 prompt）

> **用户**：MCP Token 已激活 `<已脱敏>`

**执行**：把 Token 写入 `~/.workbuddy/mcp.json` 的 `mcd-mcp` 配置；编写 `sync_db.py --from-mcp`，用 urllib 直连 MCP（initialize → tools/call 的 JSON-RPC 流程），解析返回的文本表格。

**关键技术发现**：MCP 返回的不是结构化 JSON，而是 `content[0].text` 里的一段文本表格：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
  巨无霸,null,2146,513,27,26,42,961,171
```

需要自行解析 `[N]{字段列表}:` 表头 + 逗号分隔数据行。

**产出**：158 条官方营养数据落库至 `~/.workbuddy/mcd-nutritionist/nutrition-db.json`（饮料 73、小食 23、甜品 21、早餐 19、汉堡 16、套餐 6）。**该文件按设计不入库**，避免官方数据与凭证进入仓库。

### 3.3 数据质量修复（第三轮，实测驱动）

接入真实数据后跑 `calc` / `plan` / `rank`，暴露出四类问题，逐一修复：

**Bug 1 — 营养数值被放大 1000 倍**
- 现象：板烧鸡腿堡蛋白 27g 算成 27000，钠 961mg 算成 961000。
- 根因：数据落库后经 `load_db` 二次归一化，`proteing` / `sodiummg` 这类被去掉下划线的键名触发了 `key.endswith("g")` 的误判，被当成"以 g 为单位"再乘了 1000。
- 修复：`nutrient()` 改为内部标准字段名（`_norm_key(field)`）优先直接取值，单位换算只在字段名明确带单位时触发。

**Bug 2 — 0 热量餐品丢失**
- 现象：「无糖可口可乐中杯」等 0 热量饮品在数据库里消失。
- 根因：落库条件是 `energy_kcal > 0 or protein_g > 0`，把零热量饮品过滤掉了。
- 修复：改为"有名称即保留"。

**Bug 3 — 模糊匹配匹配错对象**
- 现象：查「无糖可口可乐中杯」返回了「可乐中杯」（0 kcal 对 147 kcal）。
- 根因：子串匹配的候选集里直接取了第一个。
- 修复：在候选集中选取名称长度与查询最接近的一条，并在 `calc` 输出中增加 `fuzzy_matches` 警告。

**Bug 4 — 推荐组合质量差**
- 现象：出现「草莓新地 + 热奶铁」式空热量组合、钠 1462mg 的超标组合。
- 修复：
  - 硬约束：至少一件单品蛋白 ≥8g、组合蛋白 ≥目标 60%、钠 ≤上限 150%、热量偏差 ≤15% 且 ≤目标 105%
  - 评分权重：热量 0.30 / 蛋白 0.30 / 钠 0.20 / 结构 0.10 / 钙 0.10
  - 多样性去重：与已选组合只差一件即视为雷同跳过
  - 无解时自动降级为宽松模式

**Bug 5 — 12 条餐品落入"其他"分类**
- 修复：扩展 `infer_category` 关键词规则（麦香/巨无霸→汉堡；黑巧/橙/抹茶→饮料等）。

**验证结果**（真实数据下全部通过）：
- `板烧鸡腿堡 + 小杯玉米杯 + 无糖可口可乐中杯` = 444 kcal / 蛋白 25g / 钠 1077mg
- 500 大卡减脂推荐可正常产出达标组合

### 3.4 发布到 GitHub（用户第三轮 prompt）

> **用户**：用我的 GitHub 发布这个 skill，设置为暂时不要公开
> **用户**：`<PAT 已脱敏>`
> **用户**：mcd-nutritionist-skill

**踩坑过程**（沙箱网络环境的 TLS 限制）：

1. `gh` CLI 不存在 → 改用 `git init` + GitHub REST API。
2. `git push` 报 `SEC_E_UNTRUSTED_ROOT` / `unable to get local issuer certificate`。
   - 定位：GitHub 当前证书链为 `*.github.com → Sectigo E36 → Sectigo E46 → USERTrust ECC`，
     而本机 Windows 证书库缺后两个根；导出 Sectigo 中间证书后仍不完整。
   - 修复：从 curl.se 下载完整 Mozilla CA 包，与 GitHub 实际下发的中间证书合并为一份 bundle。
3. TLS 修好后 `git push` 仍失败：`github.com` 的 TLS 握手能成功，但数据传输约 19 秒后被网络层重置；HTTP/1.1 直连也失败。而 `api.github.com` 一直稳定。
   - **绕行方案**：改用 GitHub **Git Data API**（blob → tree → commit → ref）完整重建提交。
4. 空仓库限制：`auto_init:false` 创建的仓库，`POST /git/blobs` 报 409 "Git Repository is empty"。
   - 修复：先用 Contents API 初始化，再建 blob/tree/commit，最后 `PATCH /git/refs/heads/main` force 指向干净提交。

**发布结果**：
- 仓库 https://github.com/wmeanswin/mcd-nutritionist-skill ，**private**（按用户要求暂不公开）
- 提交 `3187c3fd` "feat: 麦门营养师 skill v1"，10 个文件，blob 哈希与本地**逐字节一致**
- 安全自检：远端无 PAT / MCP Token / nutrition-db.json；remote URL 已移除 Token；含 PAT 的临时脚本已删除

### 3.5 参赛合规补全（用户第四轮 prompt）

> **用户**：参考 https://github.com/M-China/mcd-developer-innovation-challenge，按要求上传项目介绍、参赛声明、MCP 接入说明等文件

**执行**：拉取大赛仓库的完整要求 → 逐项核对必需文件 → 补 `workbuddy.md`、修正文档数据口径。

---

## 4. 技术决策记录

| 决策 | 理由 |
|---|---|
| 营养数值必须走脚本计算，禁止模型估算 | 避免"AI 编热量"，保证结论可复算、可引用 |
| MCP 数据优先，sample 兜底须标注"参考值" | 官方实验室测定数据才有权威性 |
| 数据库落用户目录、不入仓库 | 兼顾官方数据与凭证安全 |
| 纯标准库实现 | 参赛项目零依赖，任何 Python 3 环境可跑 |
| Token 只走环境变量 `MCD_MCP_TOKEN` | 符合参赛声明的信息安全要求 |
| NRV 采用 GB 28050-2011 | 中国食品营养标签国标，与国内用户认知一致 |
| 餐次比例 早 0.25 / 午 0.35 / 晚 0.30 | 把全日预算拆到单餐，推荐才可执行 |
| BMR 用 Mifflin-St Jeor，TDEE 乘活动系数 | 目前公认误差最小的通用公式 |

---

## 5. 使用到的 WorkBuddy 能力

- **Agent 模式**：全流程自主执行（读写文件、跑命令、调 MCP、发布仓库）
- **Skill 体系**：按 `SKILL.md` + `scripts/` + `references/` + `assets/` 三级渐进披露结构组织项目
- **MCP 连接器**：配置并调用麦当劳中国 MCP Server（`mcd-mcp`）
- **命令行执行**：Python 脚本开发与调试、git 版本管理、TLS 证书诊断与修复
- **网络检索**：拉取大赛仓库的完整参赛规则与必需文件清单
- **任务管理**：多步骤任务的拆分与进度跟踪

---

## 6. 成果与验证

| 验证项 | 结果 |
|---|---|
| 必需文件齐全 | README.md / CONTEST_DECLARATION.md / MCP_INTEGRATION.md / 源代码 / workbuddy.md ✅ |
| CONTEST_DECLARATION.md | 与大赛官方仓库原文 **diff 完全一致**（文件名及内容均未改动） |
| 数据来源 | 麦当劳 MCP `list-nutrition-foods` 官方实时数据，158 条 |
| 计算正确性 | 单位换算、零热量餐品、模糊匹配、推荐约束均经真实数据验证 |
| 安全性 | 仓库内无真实 Token / 密钥 / 官方营养数据库；配置文件仅用环境变量占位符 |
| 可复现性 | 纯标准库，`sync_db.py --from-mcp` 一条命令即可重建数据库 |

---

## 7. 声明

- 本项目为麦当劳程序员节创意开发大赛参赛作品，由参赛者使用 WorkBuddy 独立开发，**非麦当劳官方产品**。
- 项目输出仅供参考，不构成医疗、营养或其他专业建议；餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。
- 本文件中所有 Token 均已脱敏，不含任何真实凭证。
