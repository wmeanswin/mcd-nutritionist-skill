# 麦当劳 MCP 接入与数据落库

## 1. 接入方式

麦当劳中国 MCP Server 远程托管地址：`https://mcp.mcd.cn`，Streamable HTTP 协议，请求头携带 `Authorization: Bearer <MCP_TOKEN>`。

在 WorkBuddy 中的配置路径：左侧边栏【专家·技能·连接器】→【连接器】→ 右上角【自定义连接器】→【配置 MCP】，填入：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

保存后在【自定义连接器】中启用 `mcd-mcp`。Token 需在 open.mcd.cn 登录手机号后于控制台申请。
配置中只保留占位符 `YOUR_MCP_TOKEN`，切勿把真实 Token 写进仓库或 skill 文件。

- 限流：每个 Token 600 次/分钟，超出返回 429。
- 401：Token 无效或过期，需重新申请。
- 服务覆盖中国大陆地区（不含港澳台）。

## 2. 营养相关工具

### `list-nutrition-foods`（核心，本 skill 的数据源）

餐品营养信息列表：获取麦当劳常见餐品的营养成分数据，包括能量、蛋白质、脂肪、碳水化合物、钠、钙。

调用时机：用户咨询麦当劳餐品热量/营养，或需要搭配指定热量套餐时。

典型返回结构（字段名以实际返回为准，脚本已做别名兼容）：

```json
{
  "foods": [
    {
      "name": "巨无霸",
      "category": "汉堡",
      "serving": "1个",
      "energy": "2310 kJ",
      "protein": 25,
      "fat": 30,
      "carbohydrate": 45,
      "sodium": 900,
      "calcium": 150
    }
  ]
}
```

若返回被包在 `content[0].text`（MCP 文本块）里，`sync_db.py` 会自动解析。

### 关联工具（下单闭环，需用户确认后再调用）

| 工具 | 用途 |
|---|---|
| `query-meals` | 查询当前门店可售餐品菜单（分类、编码、标签） |
| `query-meal-detail` | 按餐品编码查套餐组成与可替换项 |
| `query-store-coupons` / `available-coupons` / `auto-bind-coupons` | 优惠券查询与领取 |
| `calculate-price` | 计算金额、配送费、优惠与应付总价 |
| `query-nearby-stores` / `delivery-query-stores` | 门店查询 |
| `create-order` | 创建订单（**必须经用户确认后再调用**） |
| `now-time-info` | 获取当前时间（判断早餐/正餐时段） |

## 3. 字段映射

`scripts/sync_db.py` 与 `scripts/mcd_nutrition.py` 通过别名把任意来源字段映射到内部字段名：

| 内部字段 | 常见别名 |
|---|---|
| name | 餐品名称 / 名称 / foodName / productName / mealName |
| code | 编码 / 餐品编码 / productCode / mealCode / sku |
| category | 分类 / 类别 / type / mealCategory / group |
| serving | 规格 / 份量 / 单位 / 净含量 / spec / portion |
| energy_kcal | 能量 / 热量 / kcal / 千卡 / energy（含 kJ 时自动 ÷4.184） |
| protein_g | 蛋白质 / 蛋白 / protein |
| fat_g | 脂肪 / fat / totalFat |
| carb_g | 碳水化合物 / 碳水 / carbohydrate / carb |
| sodium_mg | 钠 / sodium / Na（以 g 为单位时自动 ×1000） |
| calcium_mg | 钙 / calcium / Ca |

营养字段若嵌套在 `nutrition` / `nutrients` / `营养成分` 对象中，也会被自动展开。

## 4. 落库流程

1. 调用 `list-nutrition-foods`，把完整返回保存为 `mcp_raw.json`（不要截断）。
2. 执行 `python scripts/sync_db.py --input mcp_raw.json --output nutrition-db.json --source "mcp:list-nutrition-foods"`。
3. 校验：库内餐品数量、分类分布、是否存在明显异常值（如热量为 0 或 >2000 kcal 的单品）。
4. 后续所有计算均以该库为输入。
5. 菜单更新后重复 1-2 步（同名餐品覆盖，新餐品追加）。

## 5. 数据合规

- 官方营养数据仅用于本 skill 的计算与展示，不得转售或对外再分发。
- 输出中标注数据版本（来源 + 更新日期），并说明实际数值可能因原料批次、产地、烹制过程略有差异。
