"""Hardware selection completeness; shared scenarios with the TypeScript client.

Only connected nodes gate completion; exported aliases preserve overrides. No CAD,
network or persistence dependencies; legacy records remain readable.
"""
import json
import math
from pathlib import Path
import re

RULES = json.loads((Path(__file__).resolve().parents[3] / "src/domain/hardwareSelectionRules.json").read_text(encoding="utf-8"))
INPUT_PRESETS = json.loads((Path(__file__).resolve().parents[3] / "src/domain/hardwareInputPresets.json").read_text(encoding="utf-8"))
PLACEHOLDER = re.compile(RULES["placeholderPattern"], re.I)
SCENARIOS = {scenario["id"]: scenario for scenario in RULES["scenarios"]}


def infer_category(name):
    for pattern, category in [
        (r"螺栓|bolt", "螺栓"), (r"螺钉|screw", "螺钉"), (r"螺柱|stud", "螺柱"),
        (r"螺母|nut", "螺母"), (r"垫圈|垫片|washer", "垫圈"), (r"销|pin", "销"),
        (r"铆钉|rivet", "铆钉"), (r"轴承|bearing", "轴承"), (r"轴套|bushing", "轴套"),
        (r"挡圈|retaining.?ring|circlip", "挡圈"), (r"卡扣|clip", "卡扣件"),
        (r"导轨限位", "导轨附件"), (r"^键$|平键|shaft.?key", "键"),
    ]:
        if re.search(pattern, name, re.I):
            return category
    return "其他"


def assess_interface_hardware(item):
    config = item.get("hardware")
    issues = []
    def issue(code, message):
        issues.append({"code": code, "message": message})
    if not config or config["mode"] == "auto":
        issue("AUTOMATIC_ONLY", "尚未完成选型：自动推荐仅供参考，请确认实际连接场景及五金清单。")
        return {"status": "pending", "issues": issues}
    scenario = SCENARIOS.get(config.get("scenarioId"))
    if not scenario:
        issue("SCENARIO_REQUIRED", "请选择实际连接场景；旧配置需复核，不能自动认定已完成。")
    else:
        if config["mode"] != scenario["mode"]:
            issue("SCENARIO_MODE_MISMATCH", "五金模式与选定连接场景不一致。")
        note = config.get("note") or ""
        if scenario["requiresNote"] and (not note.strip() or PLACEHOLDER.search(note)):
            issue("SELECTION_NOTE_REQUIRED", "请说明真实选配依据；无需额外五金时注明本体、已有成员或连接机制。")
    items = config.get("items") or []
    if config["mode"] == "none":
        if items:
            issue("NONE_WITH_ITEMS", "无需额外五金的配置不能同时保留采购清单。")
    elif config["mode"] == "manual":
        if not items or len(items) > 50:
            issue("HARDWARE_ITEMS_REQUIRED", "手动配置须包含 1–50 行真实五金。")
        for hardware in items:
            category = (hardware.get("category") or "").strip() or infer_category(hardware["name"])
            name, spec, quantity = hardware["name"], hardware["specification"], hardware["quantity"]
            if (not category or category in ("其他", "五金", "五金件") or PLACEHOLDER.search(category)
                    or not name.strip() or name.strip() == "五金件" or PLACEHOLDER.search(name)):
                issue("HARDWARE_TYPE_REQUIRED", "请明确每一行的实际五金类别和名称，不能使用泛称或占位内容。")
            if not spec.strip() or PLACEHOLDER.search(spec) or spec.strip() in ("配套", "按需"):
                issue("HARDWARE_SPECIFICATION_REQUIRED", f"{name or '五金件'}：请填写真实规格，待定或配套说明不算完成。")
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or quantity != int(quantity) or not 1 <= quantity <= 10000:
                issue("HARDWARE_QUANTITY_INVALID", "五金数量须为 1–10000 的整数。")
        categories = {(hardware.get("category") or "").strip() or infer_category(hardware["name"]) for hardware in items}
        for category in (scenario or {}).get("requiredCategories", []):
            if category not in categories:
                issue("REQUIRED_HARDWARE_MISSING", f"当前连接场景必需的五金类别尚未配置：{category}。")
    else:
        issue("HARDWARE_MODE_INVALID", "五金模式无效。")
    return {"status": "pending" if issues else "not-required" if config["mode"] == "none" else "configured", "issues": issues}


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _kit_signature(items):
    grouped = {}
    for item in items:
        key = _json([(item.get("category") or "").strip() or infer_category(item["name"]), item["name"].strip(), item["specification"].strip()])
        grouped[key] = grouped.get(key, 0) + item["quantity"]
    return _json(sorted(grouped.items()))


def audit_hardware_selection(document):
    parents, definitions, invalid = {}, {}, {}
    connected = set()
    def root(key):
        if parents[key] != key:
            parents[key] = root(parents[key])
        return parents[key]
    def problem(key, label, code="INVALID_ENDPOINT", is_connected=True):
        invalid[key] = {"id": key, "label": label, "code": code, "connected": is_connected}
    def resolve(scope, endpoint, prefix, depth=0):
        if depth > 40 or not endpoint["path"] or endpoint["interfaceId"].startswith("@"):
            return None
        member = next((member for member in scope if member["id"] == endpoint["path"][0]), None)
        if not member:
            return None
        path = [*prefix, member["id"]]
        rest = endpoint["path"][1:]
        nested = member.get("instanceDocument")
        if rest:
            return resolve(nested["assembly"]["members"], {**endpoint, "path": rest}, path, depth + 1) if nested else None
        item = next((item for item in member["interfaces"] if item["id"] == endpoint["interfaceId"]), None)
        if item is None:
            return None
        port = next((port for port in nested.get("ports", []) if port["id"] == endpoint["interfaceId"]), None) if nested else None
        inner = resolve(nested["assembly"]["members"], {"path": [port["source"]["memberId"]], "interfaceId": port["source"]["interfaceId"]}, path, depth + 1) if nested and port else None
        if nested and port and not inner:
            return None
        return {"key": inner["key"] if inner else _json([path, endpoint["interfaceId"]]), "path": path,
                "interfaceId": item["id"], "label": f"{' / '.join(path)} · {member['name']} · {item['name']}",
                "item": item, "depth": len(path)}
    def ensure(endpoint):
        key = endpoint["key"]
        parents.setdefault(key, key)
        definitions.setdefault(key, {})[id(endpoint["item"])] = endpoint
    def visit(doc, prefix, ancestors):
        scope = doc["assembly"]["members"]
        if len(prefix) > 40 or id(scope) in ancestors:
            problem(_json(prefix), " / ".join(prefix), "NESTING_INVALID")
            return
        next_ancestors = {*ancestors, id(scope)}
        def connect(a, b):
            if a["interfaceId"].startswith("@") or b["interfaceId"].startswith("@"):
                return
            left, right = resolve(scope, a, prefix), resolve(scope, b, prefix)
            if not left or not right:
                problem(_json([prefix, a, b]), f"{' / '.join([*prefix, *a['path']])}:{a['interfaceId']} ↔ {' / '.join([*prefix, *b['path']])}:{b['interfaceId']}")
                return
            if left["key"] == right["key"]:
                return
            ensure(left)
            ensure(right)
            connected.update((left["key"], right["key"]))
            parents[root(right["key"])] = root(left["key"])
        for mate in doc["assembly"].get("mates", []):
            connect({"path": [mate["from"]["memberId"]], "interfaceId": mate["from"]["interfaceId"]},
                    {"path": [mate["to"]["memberId"]], "interfaceId": mate["to"]["interfaceId"]})
        for relation in (doc.get("geometryDesign") or {}).get("relations", []):
            connect(relation["a"], relation["b"])
        required = [{"path": [port["source"]["memberId"]], "interfaceId": port["source"]["interfaceId"]} for port in doc.get("ports", [])]
        required += [{"path": [member["id"]], "interfaceId": item["id"]} for member in scope for item in member["interfaces"] if item.get("required")]
        for source in required:
            if source["interfaceId"].startswith("@"):
                continue
            endpoint = resolve(scope, source, prefix)
            if endpoint:
                ensure(endpoint)
            else:
                problem(_json([prefix, source]), f"{' / '.join([*prefix, *source['path']])}:{source['interfaceId']}", "INVALID_ENDPOINT", False)
        for member in scope:
            if member.get("instanceDocument"):
                visit(member["instanceDocument"], [*prefix, member["id"]], next_ancestors)
    visit(document, [], set())
    groups = {}
    for key, items in definitions.items():
        node = groups.setdefault(root(key), {"id": root(key), "endpoints": [], "declarations": [], "connected": False})
        all_items = list(items.values())
        configured = [entry for entry in all_items if entry["item"].get("hardware") and entry["item"]["hardware"]["mode"] != "auto"]
        depth = min((entry["depth"] for entry in configured), default=math.inf)
        node["endpoints"].extend(all_items)
        node["declarations"].extend([entry for entry in configured if entry["depth"] == depth] if configured else all_items)
        node["connected"] = node["connected"] or key in connected
    entries = []
    for node in groups.values():
        if not node["connected"]:
            continue
        explicit = [entry for entry in node["declarations"] if entry["item"].get("hardware") and entry["item"]["hardware"]["mode"] != "auto"]
        chosen = explicit or node["declarations"]
        issues = [issue for entry in chosen for issue in assess_interface_hardware(entry["item"])["issues"]]
        modes = {(entry["item"].get("hardware") or {}).get("mode", "auto") for entry in chosen}
        if "manual" in modes and "none" in modes:
            issues.append({"code": "SHARED_SELECTION_CONFLICT", "message": "同一共用节点同时声明需要五金与无需五金，请统一节点配置。"})
        kits = {_kit_signature(entry["item"]["hardware"].get("items") or []) for entry in chosen if (entry["item"].get("hardware") or {}).get("mode") == "manual"}
        if len(kits) > 1:
            issues.append({"code": "SHARED_KIT_CONFLICT", "message": "同一共用节点各端的五金类别、规格或数量不一致；不能用去重统计替代选型确认。"})
        unique = {issue["code"] + issue["message"]: issue for issue in issues}
        entries.append({"id": node["id"], "label": " ↔ ".join(dict.fromkeys(entry["label"] for entry in node["endpoints"])),
                        "status": "pending" if issues else "configured" if "manual" in modes else "not-required",
                        "connected": node["connected"], "endpoints": [{key: entry[key] for key in ("path", "interfaceId", "label")} for entry in node["endpoints"]],
                        "issues": list(unique.values())})
    for entry in invalid.values():
        if not entry["connected"]:
            continue
        entries.append({"id": entry["id"], "label": entry["label"], "status": "pending", "connected": True, "endpoints": [],
                        "issues": [{"code": entry["code"], "message": "子组件嵌套无效或超过安全深度，五金检查未完成。" if entry["code"] == "NESTING_INVALID" else "已连接接口的来源已失效，无法确认五金。"}]})
    pending = sum(entry["status"] == "pending" for entry in entries)
    return {"ruleVersion": RULES["version"], "scope": "connected", "total": len(entries),
            "configured": sum(entry["status"] == "configured" for entry in entries),
            "notRequired": sum(entry["status"] == "not-required" for entry in entries),
            "pending": pending, "complete": pending == 0, "entries": entries}

