# MCP 集成说明

## 使用的 MCP Server

| 项目 | 内容 |
|---|---|
| 名称 | 麦当劳中国 MCP Server（`mcd-mcp`） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 认证 | `Authorization: Bearer <MCP_TOKEN>`（Token 在 open.mcd.cn 控制台申请） |
| 限流 | 600 次/分钟/Token（超出返回 429） |
| 覆盖范围 | 中国大陆地区（不含港澳台） |

## 使用的 Tool

### 核心：`list-nutrition-foods`

**用途**：获取麦当劳常见餐品的营养成分数据（能量、蛋白质、脂肪、碳水化合物、钠、钙）。

**调用时机**：用户咨询麦当劳餐品热量/营养，或需要搭配指定热量套餐时。本 skill 的全部营养计算都以此工具的返回为唯一数据源。

**入参**：无。

**返回**：文本块 `content[0].text`，内含一段业务 JSON，其 `data` 字段为文本表格：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
  巨无霸,null,2146,513,27,26,42,961,171
```

**解析**：`scripts/sync_db.py` 中的 `parse_mcp_text()` 提取 `data` 字符串 → 用 `[N]{字段列表}:` 解析表头 → 逐行映射为结构化记录 → 再经 `normalize_food()` 统一字段名与单位（kJ 自动换算为 kcal）。当前返回 160 条，去重后落库 158 条（条数随官方菜单更新而变化）。

### 辅助（可选，用户要求下单时才调用）

| Tool | 用途 | 调用前提 |
|---|---|---|
| `query-nearby-stores` / `delivery-query-stores` | 查询门店 | 用户明确就餐方式 |
| `query-meals` / `query-meal-detail` | 查菜单与套餐组成 | 需要校验餐品是否可售 |
| `query-store-coupons` / `available-coupons` | 优惠券 | 用户问优惠 |
| `calculate-price` | 计算金额与优惠 | 用户确认要买 |
| `create-order` | 创建订单 | **必须先向用户确认门店、商品、金额** |
| `now-time-info` | 获取当前时间 | 判断早餐/正餐时段 |

## 调用流程

```
用户提问（"减脂晚上吃什么" / "巨无霸多少卡" / "配个500大卡套餐"）
        │
        ▼
MCP: list-nutrition-foods ──► 原始文本表格
        │
        ▼
sync_db.py：解析 + 字段标准化 + 单位换算 + 分类推断
        │
        ▼
nutrition-db.json（~/.workbuddy/mcd-nutritionist/，158 条）
        │
        ▼
mcd_nutrition.py：profile → plan/calc/rank
        │
        ├──► 营养合计、NRV%、供能比
        ├──► 命中个人目标的组合推荐
        └──► 可执行改进建议（换无糖、薯条换玉米杯、补蛋白…）
```

分类推断规则（`infer_category`）：套餐（官方 500 大卡组合名）→ 早餐（麦满分/粥/卷/薯饼/油条）→ 甜品（新地/麦旋风/雪冰/派…）→ 饮料（可乐/咖啡/奶铁/牛奶/茶…）→ 汉堡（堡/麦香/巨无霸）→ 小食（薯条/鸡翅/鸡排/玉米杯…）。

## 业务价值

1. **数据权威性**：营养结论来自麦当劳官方实验室测定数据，而非模型记忆，避免"AI 编热量"。
2. **实时性**：菜单上新（如薄皮焦香 V 翅、蜜汁 BBQ 风味脆汁鸡）只需重新同步即可纳入计算。
3. **从数据到决策**：MCP 只给原始数据，本 skill 补上"个人目标 → 组合搜索 → 可执行建议"这一层，把营养信息透明化真正变成用户的点餐动作。
4. **闭环延展**：营养结论可无缝接到 `query-meals` → `calculate-price` → `create-order`，形成"算得清、点得对"的完整链路。

## 安全与合规

- Token 只通过环境变量 `MCD_MCP_TOKEN` 传入，不写入任何仓库文件；配置示例中使用 `YOUR_MCP_TOKEN` 占位符。
- 官方营养数据默认存放在用户目录，不随项目分发；随项目分发的 `assets/nutrition.sample.json` 是标注了"待校准"的参考库。
- 未使用任何非官方接口或逆向手段，仅调用麦当劳公开提供的 MCP 能力。
