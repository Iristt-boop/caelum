"""饮食记录 —— 帮糖糖算热量和碳蛋脂。

她打字说吃了什么，核心流程：
1. search_food：搜食物库拿营养数据
2. add_meal：把食物+份量记成一餐，自动算营养
3. check_budget：看今天还剩多少热量预算

糖糖的每日预算 1250 kcal，已定。
估量（一碗/半盘）会标「估算」，称重不标。
"""

from __future__ import annotations

import logging

from agent.llm import ToolSpec
from tools.http import RestClient

logger = logging.getLogger(__name__)

# ---- ToolSpec ----

SEARCH_FOOD = ToolSpec(
    side_effect="read",
    name="search_food",
    description=(
        "在糖糖的食物库里搜。她说「回锅肉多少卡」「米饭热量」时，"
        "先查这个。返回 food_id，拿去记餐。支持中文模糊搜 + 别名。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "食物名或别名，如 回锅肉、米饭、鸡蛋"},
        },
        "required": ["query"],
    },
)

ADD_MEAL = ToolSpec(
    side_effect="write",
    name="add_meal",
    description=(
        "记一餐。糖糖说「中午吃了半盘回锅肉+一碗米饭」时——\n"
        "1. 先把「回锅肉」「米饭」用 search_food 查出来\n"
        "2. 再用这个记：每样食物配上 amount（份数）和 unit（碗/盘/个/克）\n"
        "bridge 会自动算营养 + 更新预算。估量结果会标「估算」。\n"
        "date 默认今天，meal_type 是 breakfast/lunch/dinner/snack。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "日期，如 2026-08-05，默认今天"},
            "meal_type": {
                "type": "string",
                "enum": ["breakfast", "lunch", "dinner", "snack"],
                "description": "哪一餐",
            },
            "items": {
                "type": "array",
                "description": "吃的东西",
                "items": {
                    "type": "object",
                    "properties": {
                        "food_id": {"type": "integer", "description": "search_food 返回的 id"},
                        "amount": {"type": "number", "description": "数量：克数或份数"},
                        "unit_type": {
                            "type": "string",
                            "enum": ["gram", "estimate"],
                            "description": "gram=称重, estimate=估量",
                        },
                        "unit_name": {"type": "string", "description": "估量单位：碗/盘/个/块/勺/杯"},
                    },
                    "required": ["food_id", "amount", "unit_type"],
                },
            },
            "note": {"type": "string", "description": "备注，可选"},
        },
        "required": ["date", "meal_type", "items"],
    },
)

CHECK_BUDGET = ToolSpec(
    side_effect="read",
    name="check_budget",
    description=(
        "查糖糖今天的饮食预算还剩多少。1250 kcal 上限。"
        "她问「今天还能吃多少」「还剩多少热量」时用这个。"
        "返回预算、已吃、剩余、按餐分类的明细。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "日期，默认今天"},
        },
    },
)

TODAY_DIET = ToolSpec(
    side_effect="read",
    name="today_diet",
    description=(
        "今日饮食小结。返回吃了什么、各餐明细、总热量、碳蛋脂、运动、体重。"
        "她说「今天吃了什么」「今日总结」「我今天吃超了吗」时用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "日期，默认今天"},
        },
    },
)

DELETE_MEAL_ITEM = ToolSpec(
    side_effect="write",
    name="delete_meal_item",
    description=(
        "删掉一条已经记下的食物。\n"
        "\n"
        "**她说「记错了」「不是这个」「热量不对」「删掉」时用这个** —— "
        "不要直接 add_meal 重记一遍！那样只会多出一条，错的那条还在，"
        "她会看到两笔（2026-08-10 她专门提的）。\n"
        "\n"
        "正确做法：先 today_diet 看一眼，每条食物后面都跟着 `[id=数字]`，"
        "拿那个 id 来删。要改数值就「先删掉、再重新 add_meal」。\n"
        "⚠️ 一餐里的东西全删完，那一餐会自动消失，不留空壳。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "item_id": {
                "type": "integer",
                "description": "食物条目 id，从 today_diet 结果里的 [id=…] 抄",
            },
        },
        "required": ["item_id"],
    },
)

ADD_EXERCISE = ToolSpec(
    side_effect="write",
    name="add_exercise",
    description=(
        "记一笔运动。糖糖说「跑了30分钟」「今天走了8000步」时用。"
        "type 是运动类型（跑步/步行/瑜伽/力量/其他），duration_min 是分钟。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "日期，默认今天"},
            "type": {"type": "string", "description": "跑步/步行/瑜伽/力量/其他"},
            "duration_min": {"type": "number", "description": "分钟"},
            "calories": {"type": "number", "description": "消耗热量 kcal，可选"},
            "note": {"type": "string", "description": "备注"},
        },
        "required": ["date", "type"],
    },
)

LOG_WEIGHT = ToolSpec(
    side_effect="write",
    name="log_weight",
    description=(
        "记一笔体重。糖糖说「今天称了」「体重XX公斤」时用。"
        "她会主动报数字，不主动问。同一天记两次会覆盖。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "日期，默认今天"},
            "weight_kg": {"type": "number", "description": "体重 kg，如 55.5"},
            "note": {"type": "string", "description": "备注，如 早上空腹"},
        },
        "required": ["date", "weight_kg"],
    },
)

ADD_FOOD = ToolSpec(
    side_effect="write",
    name="add_food",
    description=(
        "往食物库里加新食材/菜品。糖糖说「帮我录一个新食物」「这个菜库里没有」时用——\n"
        "问她每100g的热量（必须），蛋白质/碳水/脂肪（有就填，没有就0），生重还是熟重。\n"
        "category 是分类（肉类/蔬菜/主食/豆制品/饮品/零食/水果/乳制品/调味品/其他），"
        "aliases 是别名，逗号分隔。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "食物名，如 红烧排骨"},
            "cal_100g": {"type": "number", "description": "每100g热量 kcal，必填"},
            "protein_100g": {"type": "number", "description": "每100g蛋白质 g，默认 0"},
            "carbs_100g": {"type": "number", "description": "每100g碳水 g，默认 0"},
            "fat_100g": {"type": "number", "description": "每100g脂肪 g，默认 0"},
            "category": {"type": "string", "description": "分类：肉类/蔬菜/主食/豆制品/饮品/零食/水果/乳制品/调味品/其他"},
            "aliases": {"type": "string", "description": "别名，逗号分隔，可选"},
            "is_cooked": {"type": "integer", "description": "1=熟重 0=生重，默认 1"},
        },
        "required": ["name", "cal_100g"],
    },
)


# ---- 实现 ----

def make_handlers(bridge: RestClient) -> dict[str, object]:
    def _today() -> str:
        from personality.mood import now_cst
        return now_cst().strftime("%Y-%m-%d")

    def search_food(args: dict) -> str:
        q = str(args.get("query", "")).strip()
        if not q:
            return "搜什么？说个食物名。"

        r = bridge.get("/api/diet/foods", {"q": q})
        if not r.ok:
            raise RuntimeError(f"搜食物失败: {r.error}")

        items = r.data if isinstance(r.data, list) else []
        if not items:
            return f"没找到「{q}」。可以换个说法试试，或者跟我说营养数据我帮你录进库。"

        lines = [f"搜「{q}」共 {len(items)} 条："]
        for f in items[:10]:
            unit = "熟重" if f.get("is_cooked") else "生重"
            lines.append(
                f"  id={f['id']}  {f['name']}  "
                f"{f['cal_100g']}kcal/100g  "
                f"P{f.get('protein_100g',0)}g C{f.get('carbs_100g',0)}g F{f.get('fat_100g',0)}g  "
                f"({unit})"
            )
        return "\n".join(lines)

    def add_meal(args: dict) -> str:
        date = (args.get("date") or _today())
        meal_type = args.get("meal_type", "snack")
        note = args.get("note", "")
        items = args.get("items") or []

        if not items:
            return "没写吃了什么。items 不能为空。"

        body = {
            "date": date,
            "meal_type": meal_type,
            "note": note,
            "items": [
                {
                    "food_id": it.get("food_id"),
                    "amount": it.get("amount"),
                    "unit_type": it.get("unit_type", "estimate"),
                    "unit_name": it.get("unit_name", ""),
                }
                for it in items
            ],
        }
        r = bridge.post("/api/diet/meals", body)
        if not r.ok:
            raise RuntimeError(f"记餐失败: {r.error}")

        d = r.data or {}
        total = d.get("total_cal", 0)
        p = d.get("total_protein", 0)
        c = d.get("total_carbs", 0)
        f = d.get("total_fat", 0)
        return (
            f"记好了：{date} {meal_type}，"
            f"热量 {total} kcal，"
            f"蛋白质 {p}g 碳水 {c}g 脂肪 {f}g。"
        )

    def check_budget(args: dict) -> str:
        date = args.get("date") or _today()
        r = bridge.get("/api/diet/budget", {"date": date})
        if not r.ok:
            raise RuntimeError(f"查预算失败: {r.error}")

        d = r.data or {}
        budget = d.get("budget_kcal", 1250)
        eaten = d.get("eaten", 0)
        rem = d.get("remaining", 1250)
        meals = d.get("meals", [])

        pct = round(eaten / budget * 100) if budget else 0
        lines = [f"{date} 热量预算 {budget} kcal，已吃 {eaten}（{pct}%），剩 {rem}。"]
        for m in meals:
            mt = {"早餐": "早", "午餐": "午", "晚餐": "晚", "加餐": "加餐",
                  "breakfast": "早", "lunch": "午", "dinner": "晚", "snack": "加餐"}.get(
                m.get("meal_type", ""), m.get("meal_type", "")
            )
            lines.append(
                f"  {mt}餐 {m.get('total_cal', 0)} kcal  "
                f"P{m.get('total_protein', 0)}g C{m.get('total_carbs', 0)}g F{m.get('total_fat', 0)}g"
            )
        return "\n".join(lines)

    def today_diet(args: dict) -> str:
        date = args.get("date") or _today()
        r = bridge.get("/api/diet/summary", {"date": date})
        if not r.ok:
            raise RuntimeError(f"查小结失败: {r.error}")

        d = r.data or {}
        budget = d.get("budget", 1250)
        eaten = d.get("eaten", 0)
        rem = d.get("remaining", 1250)
        exercise = d.get("exercise_cal", 0)
        weight = d.get("weight")
        meals = d.get("meals", [])

        pct = round(eaten / budget * 100) if budget else 0
        lines = [
            f"{date} 饮食小结",
            f"热量 {eaten}/{budget} kcal（{pct}%），剩 {rem}" + (" ⚠️ 吃超了" if rem < 0 else ""),
        ]
        if exercise:
            lines.append(f"运动消耗 {exercise} kcal")
        if weight:
            lines.append(f"体重 {weight['weight_kg']} kg")

        for m in meals:
            mt = {"早餐": "早餐", "午餐": "午餐", "晚餐": "晚餐", "加餐": "加餐",
                  "breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}.get(
                m.get("meal_type", ""), m.get("meal_type", "")
            )
            items = m.get("items", [])
            # ⚠️ 带上 item_id —— 没有它，记错了就只能重记一遍，
            # 而重记只会新增一条、错的那条还在（糖糖 2026-08-10 报的）。
            # 有 id 才能用 delete_meal_item 删掉重来。
            detail = "、".join(
                f"{it['food_name']}{'≈' if it.get('is_estimate') else ''}{it['cal']}kcal"
                f"[id={it.get('id')}]"
                for it in items
            )
            mark = "（估算）" if any(it.get("is_estimate") for it in items) else ""
            lines.append(
                f"  {mt} {m.get('total_cal', 0)} kcal（P{m.get('total_protein', 0)}g "
                f"C{m.get('total_carbs', 0)}g F{m.get('total_fat', 0)}g）{mark}"
            )
            if detail:
                lines.append(f"    {detail}")

        return "\n".join(lines)

    def delete_meal_item(args: dict) -> str:
        item_id = args.get("item_id")
        if not item_id:
            return "没给 item_id。先用 today_diet 看一眼，每条后面都有 [id=…]。"
        r = bridge.delete(f"/api/diet/items/{int(item_id)}")
        if not r.ok:
            raise RuntimeError(f"删除失败: {r.error}")
        return f"删掉了（id={item_id}）。要重记的话现在 add_meal 一条正确的。"

    def add_exercise(args: dict) -> str:
        date = args.get("date") or _today()
        r = bridge.post("/api/diet/exercise", {
            "date": date,
            "type": args.get("type", "其他"),
            "duration_min": args.get("duration_min", 0),
            "calories": args.get("calories", 0),
            "note": args.get("note", ""),
        })
        if not r.ok:
            raise RuntimeError(f"记运动失败: {r.error}")
        d = r.data or {}
        return f"记好了：{date} {args.get('type')} {'✅' if d.get('ok') else '❌'}"

    def log_weight(args: dict) -> str:
        date = args.get("date") or _today()
        r = bridge.post("/api/diet/weight", {
            "date": date,
            "weight_kg": args["weight_kg"],
            "note": args.get("note", ""),
        })
        if not r.ok:
            raise RuntimeError(f"记体重失败: {r.error}")
        return f"记好了：{date} {args['weight_kg']} kg"

    def add_food(args: dict) -> str:
        name = str(args.get("name", "")).strip()
        cal = args.get("cal_100g")
        if not name or cal is None:
            return "需要食物名和每100g热量（cal_100g）。"

        body = {
            "name": name,
            "cal_100g": cal,
            "protein_100g": args.get("protein_100g", 0),
            "carbs_100g": args.get("carbs_100g", 0),
            "fat_100g": args.get("fat_100g", 0),
            "category": args.get("category", ""),
            "aliases": args.get("aliases", ""),
            "is_cooked": args.get("is_cooked", 1),
        }
        r = bridge.post("/api/diet/foods", body)
        if not r.ok:
            raise RuntimeError(f"添加食材失败: {r.error}")

        d = r.data or {}
        food_id = d.get("id", "?")
        return (
            f"已添加「{name}」到食物库（id={food_id}），"
            f"{cal}kcal/100g，"
            f"P{args.get('protein_100g',0)}g C{args.get('carbs_100g',0)}g F{args.get('fat_100g',0)}g。"
            f"下次用 search_food 就能找到。"
        )

    return {
        "search_food": search_food,
        "add_meal": add_meal,
        "check_budget": check_budget,
        "today_diet": today_diet,
        "add_exercise": add_exercise,
        "log_weight": log_weight,
        "add_food": add_food,
        "delete_meal_item": delete_meal_item,
        # ToolSpec
        "_search_food": SEARCH_FOOD,
        "_add_meal": ADD_MEAL,
        "_check_budget": CHECK_BUDGET,
        "_today_diet": TODAY_DIET,
        "_add_exercise": ADD_EXERCISE,
        "_log_weight": LOG_WEIGHT,
        "_add_food": ADD_FOOD,
        "_delete_meal_item": DELETE_MEAL_ITEM,
    }


def register_all(loop, handlers: dict[str, object]) -> None:
    """注册八个饮食工具。顺序固定 —— 工具定义是缓存前缀的一部分。

    ⚠️ 新工具**加在末尾**，别插在中间：工具定义属于缓存前缀，
    动了顺序整段前缀作废，一轮 ¥0.00055 变 ¥0.011。
    """
    for spec in (
        SEARCH_FOOD, ADD_MEAL, CHECK_BUDGET, TODAY_DIET,
        ADD_EXERCISE, LOG_WEIGHT, ADD_FOOD, DELETE_MEAL_ITEM,
    ):
        loop.register(spec, handlers[spec.name])  # type: ignore[arg-type]
