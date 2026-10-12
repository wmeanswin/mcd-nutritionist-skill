# 麦门营养师 skill

> 麦当劳中国（大陆）餐品营养计算与个性化点餐参谋。基于麦当劳 MCP 的官方营养数据，回答"这一顿该怎么点"。

## 它解决什么问题

麦当劳官方公开了能量、蛋白质、脂肪、碳水化合物、钠、钙六项营养数据，但看数据不等于会点餐：
"我今天要减脂，晚上想吃麦当劳，能吃什么？" —— 这类问题需要把官方数据、个人能量目标、营养素结构约束放在一起算，才能给出可执行的答案。

麦门营养师把这件事做成了一个可复用的 Skill：

- **查**：任意餐品/组合的营养数据，对标中国 NRV
- **算**：按性别、年龄、身高、体重、活动量、目标算出你的单餐热量与蛋白质/钠/钙额度
- **配**：在真实菜单里枚举组合，给出命中你个人目标的套餐（含官方 500 大卡套餐场景）
- **评**：给出"换掉薯条""改无糖饮料""补一份牛奶"这类落地点单动作

## 安装

### 1. 准备 MCP（数据源）

到 `https://open.mcd.cn/mcp` 用手机号登录，在控制台申请 MCP Token，然后在 WorkBuddy 中：
【专家·技能·连接器】→【连接器】→【自定义连接器】→【配置 MCP】，填入：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamableHttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

启用 `mcd-mcp`。本 skill 只用到其中的 `list-nutrition-foods`（餐品营养信息列表）与可选的下单类工具。

### 2. 安装 Skill

把 `mcd-nutritionist/` 整个目录复制到用户级技能目录：

```bash
cp -r mcd-nutritionist ~/.workbuddy/skills/
```

或作为项目级技能：复制到项目根 `.workbuddy/skills/`。

### 3. 拉取营养数据（首次使用前）

```bash
export MCD_MCP_TOKEN='你的Token'
python ~/.workbuddy/skills/mcd-nutritionist/scripts/sync_db.py --from-mcp
```

输出示例：

```
✅ 已写入 158 条餐品营养数据 → ~/.workbuddy/mcd-nutritionist/nutrition-db.json
分类分布：饮料 73、小食 23、甜品 21、早餐 19、汉堡 16、套餐 6
```

条数随麦当劳官方菜单更新而变化，以上为开发完成时（2026-10-13）的实际同步结果。数据库默认放在 `~/.workbuddy/mcd-nutritionist/nutrition-db.json`，不进入项目目录，避免官方数据与凭证入库。

## 使用示例

### 场景一：这顿我想控制在 500 大卡

```bash
python scripts/mcd_nutrition.py plan --db ~/.workbuddy/mcd-nutritionist/nutrition-db.json \
  --target-kcal 500 --max-items 2 --top 4 \
  --sex male --age 30 --height 175 --weight 70 --goal maintain --meal lunch
```

```
1. 原味板烧鸡腿麦满分 + 热奶铁大杯   [评分 70.2]
   495 kcal · 蛋白 29g · 脂肪 20g · 碳水 47g · 钠 745mg · 钙 564mg
   达标：热量 99% · 蛋白 79% · 钠 106%
```

### 场景二：减脂期，女生晚餐吃什么

```bash
python scripts/mcd_nutrition.py plan --db ~/.workbuddy/mcd-nutritionist/nutrition-db.json \
  --sex female --age 28 --height 165 --weight 58 --activity light --goal cut \
  --meal dinner --max-items 3 --top 3
```

```
【目标】减脂 · 本餐（dinner）目标 432 kcal / 蛋白 35g / 钠 ≤600mg / 钙 240mg

1. 蒜了吧黄油风味薄皮脆汁鸡-带骨里脊 + 咖啡阿芙佳朵 + 热奶铁中杯   [评分 72.2]
   451 kcal · 蛋白 34g · 脂肪 14g · 碳水 42g · 钠 898mg · 钙 490mg
   达标：热量 104% · 蛋白 98% · 钠 150%
```

### 场景三：我已经想好了，帮我算算

```bash
python scripts/mcd_nutrition.py calc --db ~/.workbuddy/mcd-nutritionist/nutrition-db.json \
  --items "巨无霸*1, 中薯条*1, 可乐中杯*1" --profile \
  --sex male --age 30 --height 175 --weight 75 --goal cut --meal lunch
```

```
| 餐品     | 数量 | 能量(kcal) | 蛋白质(g) | 脂肪(g) | 碳水(g) | 钠(mg) | 钙(mg) |
| 合计     |    | 949      | 31     | 38   | 116  | 1126 | 199  |
| 占 NRV% |    | 47%      | 52%    | 63%  | 39%  | 56%  | 25%  |

建议：
  · 钠 1126mg 已超本餐建议上限 700mg（占 NRV 56%）…
  · 「可乐中杯」为含糖饮料，换成无糖/零度版本约可省 147 kcal 与 36g 糖。
```

### 场景四：谁蛋白质性价比最高

```bash
python scripts/mcd_nutrition.py rank --db ~/.workbuddy/mcd-nutritionist/nutrition-db.json \
  --by protein-density --top 8
```

## 子命令一览

| 子命令 | 作用 | 关键参数 |
|---|---|---|
| `profile` | 计算 BMR/TDEE/单餐目标 | `--sex --age --height --weight --activity --goal --meal` |
| `calc` | 指定组合的营养合计 + NRV% + 建议 | `--items "餐品*数量,..." --profile` |
| `plan` | 按个人目标枚举推荐组合 | `--target-kcal --max-items --top --avoid --prefer --no-strict` |
| `rank` | 餐品排行 | `--by protein-density\|protein\|kcal\|sodium\|calcium\|sodium-density` |

全部子命令支持 `--json`，便于生成报告或接入其他流程。

## 目录结构

```
mcd-nutritionist/
├── SKILL.md                        # Skill 定义与工作流
├── scripts/
│   ├── mcd_nutrition.py            # 计算引擎（profile / calc / plan / rank）
│   └── sync_db.py                  # MCP 数据落库（支持 --from-mcp / --merge）
├── references/
│   ├── mcp-integration.md          # MCP 接入、工具清单、字段映射
│   └── nutrition-baseline.md       # NRV、算法口径、点餐营养策略
└── assets/
    └── nutrition.sample.json       # 无 MCP 时的兜底参考库（待校准）
```

## 目标用户

- 减脂/增肌/健身人群：需要把麦当劳纳入热量与蛋白质预算
- 控钠、控糖人群：需要快速识别高钠高糖选项
- 普通消费者：想知道"这一顿到底吃了多少"
- 营养/健康内容创作者：需要可复算、可引用的麦当劳营养数据

## 说明

- 营养数值来自麦当劳 MCP `list-nutrition-foods` 官方数据（每 5 年由认可实验室测定更新），实际值可能因原料批次、产地、季节、加工与烹制差异略有出入。
- 本项目输出仅供参考，不构成医疗、营养或其他专业建议；慢病人群请遵医嘱。
- 项目为麦当劳程序员节创意开发大赛参赛作品，非麦当劳官方产品。餐品信息、价格及供应状态以麦当劳官方渠道实时结果为准。
