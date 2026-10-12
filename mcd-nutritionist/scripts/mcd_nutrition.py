#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦门营养师 · 麦当劳中国餐品营养计算与个性化搭配引擎

纯标准库实现，无需第三方依赖。支持四个子命令：

    profile   根据个人档案计算 BMR / TDEE / 单餐目标（热量、蛋白质、钠、钙）
    calc      计算任意点单组合的营养合计，并对标 NRV 与个人目标
    plan      在真实菜单内枚举组合，给出命中个人目标的推荐套餐
    rank      按营养指标给餐品排序（高蛋白密度 / 低钠 / 高钙等）

数据来自麦当劳 MCP 工具 `list-nutrition-foods`（经 sync_db.py 落库）。
未接入 MCP 时可使用 assets/nutrition.sample.json 作为参考兜底库。
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 常量：中国食品标签营养素参考值 NRV（GB 28050-2011，成人每日）
# ---------------------------------------------------------------------------
NRV = {
    "energy_kcal": 2000.0,   # NRV 8400 kJ ≈ 2000 kcal
    "protein_g": 60.0,
    "fat_g": 60.0,
    "carb_g": 300.0,
    "sodium_mg": 2000.0,
    "calcium_mg": 800.0,
}

ACTIVITY = {
    "sedentary": 1.20,   # 久坐，几乎不运动
    "light": 1.375,      # 每周 1-3 次轻量运动
    "moderate": 1.55,    # 每周 3-5 次中等运动
    "active": 1.725,     # 每周 6-7 次高强度运动
    "athlete": 1.90,     # 体力工作 / 双练
}

GOAL = {
    "cut": {"kcal_adj": -0.20, "protein_g_per_kg": 2.0, "label": "减脂"},
    "maintain": {"kcal_adj": 0.0, "protein_g_per_kg": 1.5, "label": "维持"},
    "bulk": {"kcal_adj": 0.10, "protein_g_per_kg": 1.8, "label": "增肌"},
}

MEAL_RATIO = {
    "breakfast": 0.25,
    "lunch": 0.35,
    "dinner": 0.30,
    "snack": 0.10,
    "any": 0.33,
}

NUTRIENTS = ["energy_kcal", "protein_g", "fat_g", "carb_g", "sodium_mg", "calcium_mg"]
NUTRIENT_LABEL = {
    "energy_kcal": "能量(kcal)",
    "protein_g": "蛋白质(g)",
    "fat_g": "脂肪(g)",
    "carb_g": "碳水(g)",
    "sodium_mg": "钠(mg)",
    "calcium_mg": "钙(mg)",
}

# ---------------------------------------------------------------------------
# 字段别名映射：兼容 MCP / 官网 / 手工整理的各种字段名
# ---------------------------------------------------------------------------
FIELD_ALIASES = {
    "name": ["name", "餐品名称", "名称", "foodname", "productname", "mealname", "title", "菜品名"],
    "code": ["code", "编码", "餐品编码", "productcode", "mealcode", "id", "sku"],
    "category": ["category", "分类", "类别", "type", "cat", "mealcategory", "group"],
    "serving": ["serving", "规格", "份量", "单位", "净含量", "spec", "size", "portion"],
    "energy_kcal": [
        "energykcal", "energy", "能量", "热量", "kcal", "千卡", "大卡", "卡路里",
        "energy_kcal", "热量kcal", "能量kcal", "热量(kcal)", "能量(kcal)",
    ],
    "protein_g": ["protein", "蛋白质", "蛋白", "proteing", "蛋白质(g)", "protein_g"],
    "fat_g": ["fat", "脂肪", "fatg", "脂肪(g)", "fat_g", "totalfat"],
    "carb_g": ["carb", "carbohydrate", "碳水化合物", "碳水", "carbg", "碳水化合物(g)", "carb_g"],
    "sodium_mg": ["sodium", "钠", "sodiummg", "钠(mg)", "sodium_mg", "na"],
    "calcium_mg": ["calcium", "钙", "calciummg", "钙(mg)", "calcium_mg", "ca"],
}

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _norm_key(key: str) -> str:
    return re.sub(r"[\s_\-()（）\[\]]", "", str(key)).lower()


def _to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUM_RE.search(str(value))
    return float(m.group()) if m else 0.0


def normalize_food(raw: dict, default_category: str = "其他") -> dict:
    """把任意来源的一条餐品记录标准化为内部格式。"""
    flat = {}
    for k, v in raw.items():
        flat[_norm_key(k)] = v
    # MCP 常见结构：营养字段嵌套在 nutrition / nutrients 里
    for nested_key in ("nutrition", "nutrients", "nutritioninfo", "营养信息", "营养成分"):
        nk = _norm_key(nested_key)
        if nk in flat and isinstance(flat[nk], dict):
            for k, v in flat[nk].items():
                flat.setdefault(_norm_key(k), v)
        if nested_key in raw and isinstance(raw[nested_key], dict):
            for k, v in raw[nested_key].items():
                flat.setdefault(_norm_key(k), v)

    def pick(field: str):
        for alias in FIELD_ALIASES[field]:
            a = _norm_key(alias)
            if a in flat:
                return flat[a], a
        # 次优：包含匹配
        for key, val in flat.items():
            for alias in FIELD_ALIASES[field]:
                a = _norm_key(alias)
                if len(a) >= 2 and a in key:
                    return val, key
        return None, ""

    name, _ = pick("name")
    code, _ = pick("code")
    category, _ = pick("category")
    serving, _ = pick("serving")

    def nutrient(field: str) -> float:
        # 已是内部标准字段名（含下划线变体）时直接取值，避免重复单位换算
        std_key = _norm_key(field)
        if std_key in flat:
            val = flat[std_key]
            num = _to_float(val)
            if field == "energy_kcal" and num > 3000:  # 异常大值按 kJ 处理
                num = num / 4.184
            return round(num, 2)
        val, key = pick(field)
        if val is None:
            return 0.0
        num = _to_float(val)
        if field == "energy_kcal":
            if "kj" in key or "焦" in key:
                num = num / 4.184
            elif num > 3000:  # 明显是 kJ 量级
                num = num / 4.184
        else:
            # 仅当字段名明确以"克/（g）"为单位时才 mg 化，避免误伤 protein/fat 等无单位字段
            if "克" in key or (key.endswith("g") and not key.endswith("mg")):
                num = num * 1000.0
        return round(num, 2)

    return {
        "name": str(name or "").strip() or "未命名餐品",
        "code": str(code or "").strip(),
        "category": str(category or default_category).strip() or default_category,
        "serving": str(serving or "").strip(),
        "energy_kcal": nutrient("energy_kcal"),
        "protein_g": nutrient("protein_g"),
        "fat_g": nutrient("fat_g"),
        "carb_g": nutrient("carb_g"),
        "sodium_mg": nutrient("sodium_mg"),
        "calcium_mg": nutrient("calcium_mg"),
    }


def load_db(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    foods = data.get("foods") if isinstance(data, dict) else data
    if foods is None:
        for key in ("items", "data", "list", "result"):
            if isinstance(data, dict) and isinstance(data.get(key), list):
                foods = data[key]
                break
    if not isinstance(foods, list):
        raise ValueError(f"无法从 {path} 解析出餐品列表")
    out = []
    for raw in foods:
        if isinstance(raw, dict):
            item = normalize_food(raw)
            # 保留 0 热量餐品（无糖饮料、纯水等），它们对低热量搭配很关键
            if item["name"]:
                out.append(item)
    return {
        "meta": data.get("meta", {}) if isinstance(data, dict) else {},
        "foods": out,
    }


# ---------------------------------------------------------------------------
# 个人档案与目标计算
# ---------------------------------------------------------------------------
def calc_profile(sex: str, age: int, height: float, weight: float,
                 activity: str, goal: str, meal: str = "any",
                 target_kcal: float | None = None) -> dict:
    # Mifflin-St Jeor
    if sex in ("male", "男", "m"):
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
        sex_label = "男"
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
        sex_label = "女"
    act = ACTIVITY.get(activity, 1.375)
    tdee = bmr * act
    adj = GOAL.get(goal, GOAL["maintain"])["kcal_adj"]
    daily_kcal = tdee * (1 + adj)
    ratio = MEAL_RATIO.get(meal, 0.33)

    protein_daily = weight * GOAL.get(goal, GOAL["maintain"])["protein_g_per_kg"]
    targets = {
        "energy_kcal": target_kcal if target_kcal else round(daily_kcal * ratio, 1),
        "protein_g": round(protein_daily * ratio, 1),
        "fat_g": round(daily_kcal * ratio * 0.28 / 9, 1),
        "carb_g": round(daily_kcal * ratio * 0.50 / 4, 1),
        "sodium_mg": round(NRV["sodium_mg"] * ratio, 1),
        "calcium_mg": round(NRV["calcium_mg"] * ratio, 1),
    }
    return {
        "sex": sex_label,
        "age": age,
        "height_cm": height,
        "weight_kg": weight,
        "activity": activity,
        "goal": GOAL.get(goal, GOAL["maintain"])["label"],
        "bmr_kcal": round(bmr, 1),
        "tdee_kcal": round(tdee, 1),
        "daily_kcal": round(daily_kcal, 1),
        "meal": meal,
        "meal_ratio": ratio,
        "targets": targets,
    }


# ---------------------------------------------------------------------------
# 组合计算
# ---------------------------------------------------------------------------
def sum_items(items: list[tuple[dict, float]]) -> dict:
    total = {k: 0.0 for k in NUTRIENTS}
    for food, qty in items:
        for k in NUTRIENTS:
            total[k] += food.get(k, 0.0) * qty
    return {k: round(v, 1) for k, v in total.items()}


def nrv_percent(total: dict) -> dict:
    return {k: round(total.get(k, 0.0) / NRV[k] * 100, 1) for k in NUTRIENTS}


def macro_split(total: dict) -> dict:
    kcal = total.get("energy_kcal", 0.0)
    if kcal <= 0:
        return {"protein": 0.0, "fat": 0.0, "carb": 0.0}
    return {
        "protein": round(total.get("protein_g", 0) * 4 / kcal * 100, 1),
        "fat": round(total.get("fat_g", 0) * 9 / kcal * 100, 1),
        "carb": round(total.get("carb_g", 0) * 4 / kcal * 100, 1),
    }


def find_food(db: dict, query: str) -> tuple[dict | None, bool]:
    """返回 (餐品, 是否精确匹配)。"""
    q = _norm_key(query)
    if not q:
        return None, False
    for f in db["foods"]:
        if _norm_key(f["name"]) == q:
            return f, True
    # 子串匹配：优先选与查询长度最接近的，避免"无糖可乐"被匹配成"可乐"
    cands = [f for f in db["foods"] if q in _norm_key(f["name"])]
    if cands:
        return min(cands, key=lambda f: abs(len(f["name"]) - len(query))), False
    cands = [f for f in db["foods"] if _norm_key(f["name"]) in q]
    if cands:
        return max(cands, key=lambda f: len(f["name"])), False
    return None, False


def parse_items(db: dict, spec: str) -> tuple[list[tuple[dict, float]], list[str]]:
    out: list[tuple[dict, float]] = []
    fuzzy: list[str] = []
    for chunk in re.split(r"[,，;；\n]+", spec):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^(.*?)[\s]*[*xX×][\s]*(\d+(?:\.\d+)?)$", chunk)
        if m:
            name, qty = m.group(1), float(m.group(2))
        else:
            name, qty = chunk, 1.0
        food, exact = find_food(db, name.strip())
        if food is None:
            raise SystemExit(f"未在营养库中找到餐品：{name.strip()}（可用 rank 子命令查看库内餐品）")
        if not exact:
            fuzzy.append(f"{name.strip()} → {food['name']}")
        out.append((food, qty))
    if not out:
        raise SystemExit("未解析到任何餐品，示例：--items '巨无霸*1, 玉米杯*1'")
    return out, fuzzy


# ---------------------------------------------------------------------------
# 建议规则
# ---------------------------------------------------------------------------
SUGAR_DRINK_HINTS = ["可乐", "雪碧", "芬达", "果汁", "橙汁", "奶茶", "冰爽茶", "柠檬茶"]
ZERO_HINTS = ["零度", "无糖", "0度", "健怡", "黑咖啡", "美式", "气泡水", "纯水", "矿泉水"]


def build_advice(items: list[tuple[dict, float]], total: dict, targets: dict | None) -> list[str]:
    tips = []
    sodium_cap = targets["sodium_mg"] if targets else NRV["sodium_mg"] * 0.35
    if total.get("sodium_mg", 0) > sodium_cap:
        tips.append(
            f"钠 {total['sodium_mg']:.0f}mg 已超本餐建议上限 {sodium_cap:.0f}mg"
            f"（占 NRV {nrv_percent(total)['sodium_mg']:.0f}%），建议少选酱汁重的汉堡或换成板烧/麦香鱼等低钠款。"
        )
    for food, qty in items:
        nm = food["name"]
        if any(h in nm for h in SUGAR_DRINK_HINTS) and not any(z in nm for z in ZERO_HINTS):
            tips.append(f"「{nm}」为含糖饮料，换成无糖/零度版本约可省 {food['energy_kcal'] * qty:.0f} kcal 与 {food['carb_g'] * qty:.0f}g 糖。")
        if "薯条" in nm:
            tips.append(f"「{nm}」为油炸精制碳水，换成玉米杯通常可省 150-250 kcal 并增加膳食纤维。")
    if targets:
        p_ratio = total.get("protein_g", 0) / targets["protein_g"] if targets["protein_g"] else 0
        if p_ratio < 0.7:
            tips.append(f"蛋白质仅达本餐目标的 {p_ratio * 100:.0f}%，建议加入板烧鸡腿堡、吉士汉堡、牛奶或拿铁补足。")
        c_ratio = total.get("calcium_mg", 0) / targets["calcium_mg"] if targets["calcium_mg"] else 0
        if c_ratio < 0.5:
            tips.append("钙摄入偏低，可搭配牛奶/拿铁/甜筒（乳制品）提升钙摄入。")
    split = macro_split(total)
    if split["fat"] > 40:
        tips.append(f"脂肪供能比 {split['fat']}%，偏高；优先选择非油炸肉饼（板烧）或减少酱料。")
    if not tips:
        tips.append("当前组合热量与营养素结构较为均衡，可维持。")
    return tips


# ---------------------------------------------------------------------------
# 组合推荐
# ---------------------------------------------------------------------------
def _single_score(food: dict, target_kcal: float, protein_target: float) -> float:
    kcal = food["energy_kcal"]
    if kcal <= 0:
        kcal = 1
    fit = 1 - min(1.0, abs(kcal - target_kcal * 0.5) / (target_kcal * 0.5))
    protein = min(1.0, food["protein_g"] / protein_target) if protein_target else 0
    sodium = 1 - min(1.0, food["sodium_mg"] / (NRV["sodium_mg"] * 0.35))
    return 0.4 * fit + 0.4 * protein + 0.2 * sodium


def plan_combos(db: dict, targets: dict, max_items: int = 3, top: int = 5,
                avoid: list[str] | None = None, prefer: list[str] | None = None,
                pool_size: int = 14, same_category: bool = False,
                strict: bool = True) -> list[dict]:
    foods = db["foods"]
    if avoid:
        foods = [f for f in foods if not any(a in f["name"] for a in avoid)]
    if not foods:
        return []

    by_cat: dict[str, list[dict]] = {}
    for f in foods:
        by_cat.setdefault(f["category"], []).append(f)

    for cat in by_cat:
        by_cat[cat].sort(
            key=lambda f: _single_score(f, targets["energy_kcal"], targets["protein_g"]),
            reverse=True,
        )
        if prefer:
            by_cat[cat].sort(key=lambda f: (0 if any(p in f["name"] or p in f["category"] for p in prefer) else 1),
                             reverse=False)
        by_cat[cat] = by_cat[cat][:pool_size]

    cats = list(by_cat.keys())
    combos = []
    sizes = range(1, max_items + 1)
    for size in sizes:
        if same_category:
            for combo in itertools.combinations(foods[:60], size):
                combos.append(list(combo))
        else:
            for cat_combo in itertools.combinations(cats, size):
                pools = [by_cat[c] for c in cat_combo]
                for combo in itertools.product(*pools):
                    combos.append(list(combo))
    if not combos:
        return []

    target = targets["energy_kcal"]
    sodium_cap = targets["sodium_mg"]
    min_protein = targets["protein_g"] * 0.6 if strict else 0.0
    sodium_hard = sodium_cap * 1.5 if strict else float("inf")
    results = []
    seen = set()
    selected_sets: list[set] = []
    for combo in combos:
        items = [(f, 1.0) for f in combo]
        total = sum_items(items)
        kcal = total["energy_kcal"]
        if abs(kcal - target) > target * 0.15:
            continue
        if kcal > target * 1.05:
            continue
        if strict:
            # 至少要有一件像样的蛋白质来源，避免"甜品+饮料"式的空热量组合
            if max(f["protein_g"] for f in combo) < 8:
                continue
            if total["protein_g"] < min_protein:
                continue
            if total["sodium_mg"] > sodium_hard:
                continue
        key = tuple(sorted(f["name"] for f in combo))
        if key in seen:
            continue
        seen.add(key)
        # 保证结果多样性：与已选组合只差一件时视为雷同，跳过
        cset = set(f["name"] for f in combo)
        if any(len(cset & s) >= max(1, len(cset) - 1) for s in selected_sets):
            continue
        selected_sets.append(cset)
        kcal_fit = 1 - min(1.0, abs(kcal - target) / (target * 0.3))
        protein_score = min(1.0, total["protein_g"] / targets["protein_g"]) if targets["protein_g"] else 0
        sodium_score = 1 - min(1.0, total["sodium_mg"] / sodium_cap) if sodium_cap else 0
        split = macro_split(total)
        balance = 1 - min(1.0, (abs(split["fat"] - 30) + abs(split["protein"] - 20) + abs(split["carb"] - 50)) / 90)
        calcium_score = min(1.0, total["calcium_mg"] / targets["calcium_mg"]) if targets["calcium_mg"] else 0
        score = (0.30 * kcal_fit + 0.30 * protein_score + 0.20 * sodium_score
                 + 0.10 * balance + 0.10 * calcium_score)
        results.append({
            "items": [{"name": f["name"], "category": f["category"], "serving": f.get("serving", "")} for f in combo],
            "total": total,
            "macro_split": split,
            "score": round(score * 100, 1),
        })
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top]


# ---------------------------------------------------------------------------
# 输出渲染
# ---------------------------------------------------------------------------
def render_table(items: list[tuple[dict, float]], total: dict) -> str:
    head = ["餐品", "数量"] + [NUTRIENT_LABEL[k] for k in NUTRIENTS]
    rows = [head]
    for food, qty in items:
        rows.append([food["name"], f"{qty:g}"] + [f"{food.get(k, 0) * qty:.0f}" for k in NUTRIENTS])
    rows.append(["合计", ""] + [f"{total[k]:.0f}" for k in NUTRIENTS])
    pct = nrv_percent(total)
    rows.append(["占 NRV%", ""] + [f"{pct[k]:.0f}%" for k in NUTRIENTS])
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(head))]
    lines = []
    for idx, row in enumerate(rows):
        lines.append("| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)) + " |")
        if idx == 0:
            lines.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    return "\n".join(lines)


def cmd_profile(args):
    p = calc_profile(args.sex, args.age, args.height, args.weight,
                     args.activity, args.goal, args.meal, args.target_kcal)
    if args.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
        return
    print(f"【个人档案】{p['sex']} · {p['age']}岁 · {p['height_cm']}cm · {p['weight_kg']}kg · {p['activity']} · 目标：{p['goal']}")
    print(f"BMR {p['bmr_kcal']:.0f} kcal / TDEE {p['tdee_kcal']:.0f} kcal / 每日目标 {p['daily_kcal']:.0f} kcal")
    print(f"本餐（{p['meal']}，占全天 {p['meal_ratio'] * 100:.0f}%）目标：")
    for k in NUTRIENTS:
        print(f"  - {NUTRIENT_LABEL[k]}：{p['targets'][k]:.0f}")


def cmd_calc(args):
    db = load_db(args.db)
    items, fuzzy = parse_items(db, args.items)
    targets = None
    if args.profile:
        targets = calc_profile(args.sex, args.age, args.height, args.weight,
                               args.activity, args.goal, args.meal, args.target_kcal)["targets"]
    total = sum_items(items)
    payload = {
        "items": [{"name": f["name"], "qty": q, **{k: f[k] for k in NUTRIENTS}} for f, q in items],
        "total": total,
        "nrv_percent": nrv_percent(total),
        "macro_split": macro_split(total),
        "targets": targets,
        "advice": build_advice(items, total, targets),
        "fuzzy_matches": fuzzy,
        "meta": db.get("meta", {}),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if fuzzy:
        print("⚠ 模糊匹配：" + "；".join(fuzzy) + "\n")
    print(render_table(items, total))
    print()
    print("供能比：" + " / ".join(f"{k} {v}%" for k, v in macro_split(total).items()))
    if targets:
        print("\n对标个人目标：")
        for k in NUTRIENTS:
            t = targets[k]
            v = total[k]
            print(f"  - {NUTRIENT_LABEL[k]}：{v:.0f} / {t:.0f}（{v / t * 100:.0f}%）" if t else "")
    print("\n建议：")
    for t in payload["advice"]:
        print(f"  · {t}")
    src = db.get("meta", {}).get("source", "")
    if src:
        print(f"\n数据来源：{src}")


def cmd_plan(args):
    db = load_db(args.db)
    prof = calc_profile(args.sex, args.age, args.height, args.weight,
                        args.activity, args.goal, args.meal, args.target_kcal)
    targets = prof["targets"]
    prefer = args.prefer
    if not prefer and args.meal == "breakfast":
        prefer = ["早餐"]
    combos = plan_combos(db, targets, max_items=args.max_items, top=args.top,
                         avoid=args.avoid, prefer=prefer,
                         same_category=args.same_category, strict=not args.no_strict)
    relaxed = False
    if not combos and not args.no_strict:
        combos = plan_combos(db, targets, max_items=args.max_items, top=args.top,
                             avoid=args.avoid, prefer=prefer,
                             same_category=args.same_category, strict=False)
        relaxed = True
    payload = {"profile": prof, "recommendations": []}
    for c in combos:
        items = []
        for it in c["items"]:
            food, _ = find_food(db, it["name"])
            if food:
                items.append((food, 1.0))
        c["advice"] = build_advice(items, c["total"], targets)
        payload["recommendations"].append(c)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"【目标】{prof['goal']} · 本餐（{prof['meal']}）目标 "
          f"{targets['energy_kcal']:.0f} kcal / 蛋白 {targets['protein_g']:.0f}g / "
          f"钠 ≤{targets['sodium_mg']:.0f}mg / 钙 {targets['calcium_mg']:.0f}mg\n")
    if not combos:
        print("未找到满足条件的组合，可增大 --max-items、调整目标热量或用 --no-strict 放宽约束。")
        return
    if relaxed:
        print("（未找到完全达标的组合，以下为放宽蛋白质/钠约束后的近似结果）\n")
    for i, c in enumerate(combos, 1):
        t = c["total"]
        print(f"{i}. {' + '.join(x['name'] for x in c['items'])}   [评分 {c['score']}]")
        print(f"   {t['energy_kcal']:.0f} kcal · 蛋白 {t['protein_g']:.0f}g · 脂肪 {t['fat_g']:.0f}g · "
              f"碳水 {t['carb_g']:.0f}g · 钠 {t['sodium_mg']:.0f}mg · 钙 {t['calcium_mg']:.0f}mg")
        print(f"   达标：热量 {t['energy_kcal'] / targets['energy_kcal'] * 100:.0f}% · "
              f"蛋白 {t['protein_g'] / targets['protein_g'] * 100:.0f}% · "
              f"钠 {t['sodium_mg'] / targets['sodium_mg'] * 100:.0f}%")
        for a in c["advice"][:2]:
            print(f"   提示：{a}")
        print()
    src = db.get("meta", {}).get("source", "")
    if src:
        print(f"数据来源：{src}")


def cmd_rank(args):
    db = load_db(args.db)
    foods = [f for f in db["foods"]
             if (not args.category or args.category in f["category"])]
    metric = args.by

    def key(f):
        kcal = f["energy_kcal"] or 1
        if metric == "protein-density":
            return f["protein_g"] / kcal * 100
        if metric == "protein":
            return f["protein_g"]
        if metric == "kcal":
            return -f["energy_kcal"]
        if metric == "sodium":
            return -f["sodium_mg"]
        if metric == "calcium":
            return f["calcium_mg"]
        if metric == "sodium-density":
            return -f["sodium_mg"] / kcal * 100
        return f["protein_g"] / kcal * 100

    foods.sort(key=key, reverse=True)
    foods = foods[: args.top]
    if args.json:
        print(json.dumps({"metric": metric, "foods": foods}, ensure_ascii=False, indent=2))
        return
    print(f"【{metric} 排行 · Top {len(foods)}】")
    for f in foods:
        print(f"  {f['name']}（{f['category']}）· {f['energy_kcal']:.0f} kcal · 蛋白 {f['protein_g']:.0f}g · "
              f"脂肪 {f['fat_g']:.0f}g · 碳水 {f['carb_g']:.0f}g · 钠 {f['sodium_mg']:.0f}mg · 钙 {f['calcium_mg']:.0f}mg")


def build_parser():
    p = argparse.ArgumentParser(description="麦门营养师 · 麦当劳中国营养计算引擎")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_profile_args(sp):
        sp.add_argument("--sex", default="male", choices=["male", "female", "男", "女"])
        sp.add_argument("--age", type=int, default=28)
        sp.add_argument("--height", type=float, default=172)
        sp.add_argument("--weight", type=float, default=68)
        sp.add_argument("--activity", default="light", choices=list(ACTIVITY.keys()))
        sp.add_argument("--goal", default="maintain", choices=list(GOAL.keys()))
        sp.add_argument("--meal", default="any", choices=list(MEAL_RATIO.keys()))
        sp.add_argument("--target-kcal", dest="target_kcal", type=float, default=None)

    sp = sub.add_parser("profile", help="计算个人能量与营养素目标")
    add_profile_args(sp)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("calc", help="计算指定组合的营养合计")
    sp.add_argument("--db", required=True)
    sp.add_argument("--items", required=True, help="例：'巨无霸*1, 玉米杯*1'")
    add_profile_args(sp)
    sp.add_argument("--profile", action="store_true", help="同时对标个人目标")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_calc)

    sp = sub.add_parser("plan", help="按个人目标推荐套餐组合")
    sp.add_argument("--db", required=True)
    add_profile_args(sp)
    sp.add_argument("--max-items", type=int, default=3)
    sp.add_argument("--top", type=int, default=5)
    sp.add_argument("--avoid", nargs="*", default=None, help="排除含关键词的餐品")
    sp.add_argument("--prefer", nargs="*", default=None, help="优先含关键词的餐品")
    sp.add_argument("--same-category", action="store_true", help="允许同类别多选")
    sp.add_argument("--no-strict", action="store_true",
                    help="放宽硬约束（蛋白质下限、钠上限、必须有蛋白来源）")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("rank", help="餐品营养排行")
    sp.add_argument("--db", required=True)
    sp.add_argument("--by", default="protein-density",
                    choices=["protein-density", "protein", "kcal", "sodium", "calcium", "sodium-density"])
    sp.add_argument("--category", default=None)
    sp.add_argument("--top", type=int, default=10)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_rank)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
