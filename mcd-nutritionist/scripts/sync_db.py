#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把麦当劳 MCP 工具 `list-nutrition-foods` 的数据标准化落库。

三种用法：

    # 1) 直接从 MCP 拉取（推荐）：Token 走环境变量，不落盘、不进仓库
    export MCD_MCP_TOKEN='你的Token'
    python sync_db.py --from-mcp

    # 2) 已保存 MCP 原始响应时导入
    python sync_db.py --input mcp_raw.json --source "mcp:list-nutrition-foods"

    # 3) 从标准输入导入
    cat mcp_raw.json | python sync_db.py

默认输出：~/.workbuddy/mcd-nutritionist/nutrition-db.json
（不写入项目目录，避免官方数据与真实凭证进入版本库）

输出结构：
    {"meta": {"source": "...", "updated": "YYYY-MM-DD", "count": N},
     "foods": [{"name", "code", "category", "serving", "note",
                "energy_kcal", "protein_g", "fat_g", "carb_g",
                "sodium_mg", "calcium_mg"}, ...]}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcd_nutrition import normalize_food  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MCP_URL = "https://mcp.mcd.cn"
DEFAULT_OUTPUT = os.path.join(os.path.expanduser("~"), ".workbuddy",
                              "mcd-nutritionist", "nutrition-db.json")

# 麦当劳官方 500 大卡组合（名称固定，单独归类为"套餐"）
COMBO_NAMES = ["果然多肉", "“苹板”支撑Pro", "专“薯鱼”你", "绝代双翅", "牛气满满", "蜜汁快乐"]

CATEGORY_RULES = [
    ("早餐", ["麦满分", "早安营养卷", "粥", "薯饼", "油条", "炒双蛋堡", "叠叠卷"]),
    ("甜品", ["新地", "麦旋风", "圆筒", "冰淇淋", "雪冰", "奶冻", "爆珠", "拉明顿", "阿芙佳朵", "派"]),
    ("饮料", ["可乐", "雪碧", "咖啡", "美式", "奶铁", "牛奶", "燕麦", "红茶", "茶", "果汁",
              "怡泉", "纯悦", "豆浆", "玛奇朵", "卡布奇诺", "橙", "柠檬", "朱古力", "黑巧",
              "抹茶", "奶冻", "雪冰"]),
    ("汉堡", ["堡", "汉堡", "麦香", "巨无霸"]),
    ("小食", ["薯条", "麦乐鸡", "鸡翅", "鸡排", "V翅", "脆汁鸡", "鸡球", "玉米杯", "苹果片",
              "香肠", "笋卷", "鸡块"]),
]


def infer_category(name: str, note: str = "") -> str:
    if name in COMBO_NAMES:
        return "套餐"
    for cat, keys in CATEGORY_RULES:
        for k in keys:
            if k.lower() in name.lower():
                return cat
    return "其他"


# ---------------------------------------------------------------------------
# MCP 文本格式解析：`[160]{field1,field2,...}:` + 若干 `值,值,...` 行
# ---------------------------------------------------------------------------
HEADER_RE = re.compile(r"\[\s*(\d+)\s*\]\s*\{([^}]*)\}\s*:")


def parse_mcp_text(text: str) -> list[dict]:
    """解析 list-nutrition-foods 返回的文本表格。"""
    # 文本中可能先有说明，再嵌一段 JSON；取出 data 字段
    payload = text
    m_json = re.search(r"\{[^{}]*\"data\"\s*:", text)
    if m_json:
        start = text.find("{", m_json.start())
        # 从 start 起做一次 JSON 平衡截取
        depth, end = 0, None
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end:
            try:
                obj = json.loads(text[start:end])
                if isinstance(obj.get("data"), str):
                    payload = obj["data"]
                elif isinstance(obj.get("data"), list):
                    return obj["data"]
            except json.JSONDecodeError:
                payload = text

    m = HEADER_RE.search(payload)
    if not m:
        return []
    fields = [f.strip() for f in m.group(2).split(",")]
    rows = []
    for line in payload[m.end():].split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(fields):
            continue
        rows.append(dict(zip(fields, parts)))
    return rows


def iter_food_records(data):
    """从各种包装结构里挖出餐品记录列表。"""
    if isinstance(data, list):
        if data and isinstance(data[0], str):
            return parse_mcp_text("\n".join(data))
        return data
    if isinstance(data, dict):
        for key in ("foods", "items", "data", "list", "result", "results", "records", "content"):
            val = data.get(key)
            if isinstance(val, list):
                if key == "content" and val and isinstance(val[0], dict) and "text" in val[0]:
                    try:
                        return iter_food_records(json.loads(val[0]["text"]))
                    except Exception:
                        return parse_mcp_text(val[0]["text"])
                return iter_food_records(val)
            if isinstance(val, str) and key == "data":
                return parse_mcp_text(val)
    return []


def fetch_from_mcp(token: str) -> list[dict]:
    """用 JSON-RPC over HTTP 直接调用 MCP（无需 MCP 客户端）。"""
    import urllib.error
    import urllib.request

    def call(method: str, params: dict | None = None, rid: int = 1):
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method,
                           "params": params or {}}).encode("utf-8")
        req = urllib.request.Request(
            MCP_URL, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "mcd-nutritionist", "version": "1.0"}})
    res = call("tools/call", {"name": "list-nutrition-foods", "arguments": {}}, rid=2)
    content = (res.get("result") or {}).get("content") or []
    if not content:
        raise SystemExit(f"MCP 未返回数据：{json.dumps(res, ensure_ascii=False)[:300]}")
    text = content[0].get("text", "")
    records = iter_food_records({"content": [{"type": "text", "text": text}]})
    if not records:
        records = parse_mcp_text(text)
    return records


def main():
    ap = argparse.ArgumentParser(description="同步麦当劳 MCP 营养数据到本地营养库")
    ap.add_argument("--from-mcp", action="store_true", help="直接调用 MCP 拉取最新营养数据")
    ap.add_argument("--token", default=None, help="MCP Token；缺省时读取环境变量 MCD_MCP_TOKEN")
    ap.add_argument("--input", default=None, help="MCP 原始响应 JSON；省略则从 stdin 读取")
    ap.add_argument("--output", default=None, help=f"输出路径，默认 {DEFAULT_OUTPUT}")
    ap.add_argument("--merge", default=None, help="合并到已有营养库文件")
    ap.add_argument("--source", default=None, help="数据来源标注")
    ap.add_argument("--category", default=None, help="为缺失分类的记录补默认分类")
    args = ap.parse_args()

    if args.from_mcp:
        token = args.token or os.environ.get("MCD_MCP_TOKEN")
        if not token:
            raise SystemExit("缺少 MCP Token：设置环境变量 MCD_MCP_TOKEN 或传 --token")
        records = fetch_from_mcp(token)
        source = args.source or "mcp:list-nutrition-foods (live)"
    else:
        raw_text = open(args.input, "r", encoding="utf-8").read() if args.input else sys.stdin.read()
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            records = parse_mcp_text(raw_text)
            data = None
        if data is not None:
            records = iter_food_records(data)
            if not records:
                records = parse_mcp_text(raw_text)
        source = args.source or "mcp:list-nutrition-foods"

    if not records:
        raise SystemExit("未解析出餐品记录，请检查输入是否为 MCP list-nutrition-foods 的返回")

    foods, seen = [], set()
    for r in records:
        if not isinstance(r, dict):
            continue
        item = normalize_food(r, default_category=args.category or "其他")
        if not item["category"] or item["category"] == "其他":
            item["category"] = infer_category(item["name"])
        note = r.get("nutritionDescription") or r.get("note") or ""
        if note and note not in ("null", "None"):
            item["note"] = note
        # 保留 0 热量餐品（无糖饮料、纯水等）
        if item["name"]:
            if item["name"] in seen:
                continue
            seen.add(item["name"])
            foods.append(item)

    if args.merge and os.path.exists(args.merge):
        with open(args.merge, "r", encoding="utf-8") as f:
            old = json.load(f)
        old_map = {f["name"]: f for f in old.get("foods", [])}
        old_map.update({f["name"]: f for f in foods})
        foods = list(old_map.values())
        out_path = args.merge
    else:
        out_path = args.output or DEFAULT_OUTPUT

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    db = {
        "meta": {
            "source": source,
            "updated": date.today().isoformat(),
            "count": len(foods),
        },
        "foods": foods,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    cats: dict[str, int] = {}
    for f in foods:
        cats[f["category"]] = cats.get(f["category"], 0) + 1
    print(f"✅ 已写入 {len(foods)} 条餐品营养数据 → {out_path}")
    print("分类分布：" + "、".join(f"{k} {v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])))
    for f in foods[:3]:
        print(f"  例：{f['name']}（{f['category']}）· {f['energy_kcal']:.0f} kcal · "
              f"蛋白 {f['protein_g']:.0f}g · 钠 {f['sodium_mg']:.0f}mg")


if __name__ == "__main__":
    main()
