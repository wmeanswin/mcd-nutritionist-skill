---
name: mcd-nutritionist
description: 麦当劳中国（大陆）餐品营养计算与个性化点餐参谋。当用户询问麦当劳某款餐品的热量/蛋白质/脂肪/碳水/钠/钙、想计算一顿点单的总营养、想按减脂/增肌/控钠/控糖等个人目标搭配套餐（如 500 大卡套餐）、或需要高蛋白低钠点单建议时使用。基于麦当劳 MCP `list-nutrition-foods` 实时营养数据，完成取数、计算、对标 NRV 与个人目标、输出推荐组合。关键词：麦当劳热量、麦门、麦当劳营养、500大卡、麦当劳减脂、点什么健康。
agent_created: true
---

# 麦门营养师

麦当劳中国（大陆地区）餐品的营养计算与个性化点餐参谋：把官方营养数据变成"这一顿该怎么点"的具体答案。

## 核心原则

1. **实时数据优先**：营养数据以麦当劳 MCP 工具 `list-nutrition-foods` 的返回为准。本地 `assets/nutrition.sample.json` 仅为无 MCP 时的兜底参考库，使用时必须在输出中明确标注"参考值，可能与官方实时数据存在偏差"。
2. **先算后说**：所有营养结论必须由 `scripts/mcd_nutrition.py` 计算得出，禁止凭印象估算热量或营养素。
3. **给出可执行建议**：不只报数字，要给出"换掉哪一款""加什么补足"的落地点单动作。
4. **不做医疗建议**：涉及疾病（糖尿病、肾病、高血压等）或特殊人群（孕妇、儿童）时给出通用营养原则，并提示以医生/临床营养师意见为准。

## 工作流

### 第 1 步：明确需求类型

| 用户意图 | 处理方式 |
|---|---|
| 单个/多个餐品营养查询 | `rank` 检索 + `calc` 计算 |
| 一顿点单的总营养 | `calc` 计算 + NRV% 对标 |
| 按个人目标搭配套餐 | 收集档案 → `plan` 推荐 |
| 某类营养诉求（高蛋白/低钠/低热量） | `rank` 排行 + `plan` 约束 |

### 第 2 步：获取营养数据

营养库默认位置：`~/.workbuddy/mcd-nutritionist/nutrition-db.json`（不写入项目目录）。

**优先**：在对话中直接调用 MCP 工具 `list-nutrition-foods` 拿到数据后落库：

```bash
python scripts/sync_db.py --input mcp_raw.json      # 导入已保存的 MCP 返回
cat mcp_raw.json | python scripts/sync_db.py        # 或从 stdin 导入
```

**备选**：若该 MCP 工具在当前会话不可直接调用，用脚本自带方式拉取（Token 只走环境变量）：

```bash
export MCD_MCP_TOKEN='<用户的Token>'
python scripts/sync_db.py --from-mcp
```

- 已存在库需补充时用 `--merge <库路径>` 合并（同名以新数据为准）。
- MCP 完全不可用时才改用 `assets/nutrition.sample.json`，且必须在回复中声明"数值为参考值，可能与官方实时数据存在偏差"。
- 当前库无数据或菜单更新时，重新执行同步；同步后核对 `meta.count` 与 `meta.updated`。
- MCP 接入方式、工具清单与字段映射见 `references/mcp-integration.md`。

### 第 3 步：收集个人档案（仅在需要个性化推荐时）

至少获取：性别、年龄、身高(cm)、体重(kg)、日常活动量、目标（减脂/维持/增肌）、本次餐次。
信息缺失时使用默认值（男 / 28 岁 / 172cm / 68kg / light / maintain / any）并明确告知所用假设，不要用追问打断对话。
忌口、过敏、不想吃的品类用 `--avoid` 传入；偏好用 `--prefer` 传入。

### 第 4 步：计算与推荐

```bash
# 个人目标
python scripts/mcd_nutrition.py profile --sex male --age 30 --height 175 --weight 70 \
    --activity light --goal cut --meal lunch

# 指定组合营养合计（同时对标个人目标）
DB=~/.workbuddy/mcd-nutritionist/nutrition-db.json

python scripts/mcd_nutrition.py calc --db $DB \
    --items "巨无霸*1, 中薯条*1, 无糖可口可乐中杯*1" --profile \
    --sex male --age 30 --height 175 --weight 70 --goal cut --meal lunch

# 按目标推荐套餐组合
python scripts/mcd_nutrition.py plan --db $DB \
    --sex female --age 26 --height 165 --weight 55 --activity light --goal cut \
    --meal dinner --max-items 3 --top 5 --avoid 薯条 可乐

# 餐品排行（高蛋白密度 / 低钠 / 高钙）
python scripts/mcd_nutrition.py rank --db $DB --by protein-density --top 10
python scripts/mcd_nutrition.py rank --db $DB --by sodium --category 汉堡 --top 8
```

所有子命令支持 `--json`，便于生成报告或二次加工。

**固定目标套餐**（如 500 大卡套餐）：直接传 `--target-kcal 500`，脚本按该热量硬性筛选组合（不超过目标 105%、偏差 ≤15%）。
`plan` 默认开启硬约束（必须有蛋白来源、蛋白质 ≥目标 60%、钠 ≤上限 150%）；无解时自动降级为近似结果并提示，也可用 `--no-strict` 主动放宽。

### 第 5 步：解读结果

按 `references/nutrition-baseline.md` 的口径解读：

- 热量：与本餐目标对比，说明占全天目标的百分比。
- 蛋白质：减脂/增肌人群重点关注，未达标时给出高蛋白替换项。
- 钠：中国 NRV 为 2000mg/天，单餐超 700mg 即需提示。
- 供能比：蛋白 15-25%、脂肪 25-35%、碳水 50-60% 为参考区间。
- 输出末尾附一句：营养数值来自麦当劳官方数据（或标注参考库），实际因原料批次与烹制过程可能有差异。

## 输出规范

推荐结果按"组合 + 关键数字 + 达标率 + 1-2 条改进提示"的结构输出，例如：

```
1. 板烧鸡腿堡 + 小杯玉米杯 + 无糖可口可乐中杯   [评分 78]
   444 kcal · 蛋白 25g · 脂肪 18g · 碳水 43g · 钠 1077mg · 钙 99mg
   达标：热量 68% · 蛋白 48% · 钠 154%
   提示：钠 1077mg 占全天 NRV 54%，建议减少酱料；蛋白质偏低，可加热牛奶补足。
```

若用户需要下单，再调用 MCP 的 `query-meals` / `query-store-coupons` / `calculate-price`，并**在真正调用 `create-order` 前必须向用户确认门店、商品与金额**。

## 资源

- `scripts/mcd_nutrition.py` — 计算引擎：`profile` / `calc` / `plan` / `rank` 四个子命令，纯标准库。
- `scripts/sync_db.py` — 把 MCP 原始返回标准化落库（支持 `--merge`）。
- `assets/nutrition.sample.json` — 无 MCP 时的兜底参考库（待校准，输出时须标注）。
- `references/mcp-integration.md` — MCP 接入、工具清单、字段映射。
- `references/nutrition-baseline.md` — NRV、目标热量与 macros 算法、麦当劳点餐营养策略。

## 边界

- 只做营养计算与点餐建议，不替用户直接下单（创建订单前需确认）。
- 不提供诊断、治疗或用药建议；慢病人群提示咨询医生/临床营养师。
- 不修改本地营养库中的官方数值；数据更新只能通过 MCP 重新同步。
