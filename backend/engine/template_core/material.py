from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

from .metamodel import MaterialRequirement


MATERIAL_COLUMNS = (
    "id", "code", "name", "type", "category_group", "data_kind", "length", "width",
    "height", "thickness", "grade", "color", "drawing_color", "price", "surface",
    "standard", "stock_qty", "available_qty", "supplier", "status", "updated_at", "remark",
)

SUPPLY_FORM_ALIASES = {
    "coil": ("卷", "带钢", "coil", "strip steel"), "sheet": ("板", "sheet", "plate"),
    "openProfile": ("开口", "型材", "profile", "c型钢", "ω钢", "角钢", "槽钢", "工字钢", "扁钢"),
    "closedProfile": ("闭口", "closed profile"),
    "tube": ("管", "tube", "pipe"), "bar": ("棒", "bar"), "wire": ("线", "wire"),
    "engineeringPlastic": ("塑料", "塑胶", "plastic"),
    "standardPart": ("标准件", "五金", "紧固件", "standard", "hardware"),
}

SUPPLY_FORM_LABELS = {
    "coil": "卷材", "sheet": "平板", "openProfile": "开口型材", "closedProfile": "闭口型材",
    "tube": "管材", "bar": "棒材", "wire": "线材", "engineeringPlastic": "工程塑料",
    "externalModel": "外部模型", "standardPart": "标准件", "other": "其他",
}

SEARCHABLE_COLUMNS = (
    "code",
    "name",
    "type",
    "category_group",
    "data_kind",
    "grade",
    "color",
    "drawing_color",
    "surface",
    "standard",
    "supplier",
    "status",
    "remark",
    "thickness",
    "length",
    "width",
    "height",
    "price",
)


def effective_thickness_domain(requirement: MaterialRequirement) -> dict[str, Any]:
    """Return the single thickness domain used by filtering, parameters and admission."""
    constraint = requirement.thickness
    declared_values = sorted(set(float(value) for value in constraint.allowedValues))
    values = [
        value for value in declared_values
        if (constraint.minimum is None or value >= constraint.minimum)
        and (constraint.maximum is None or value <= constraint.maximum)
    ]
    empty = bool(declared_values) and not values
    lower = values[0] if values else constraint.minimum
    upper = values[-1] if values else constraint.maximum
    return {
        "kind": "discrete" if declared_values else ("range" if lower is not None or upper is not None else "unbounded"),
        "declaredValues": declared_values,
        "values": values,
        "minimum": lower,
        "maximum": upper,
        "empty": empty,
    }


def infer_supply_forms(material: dict[str, Any]) -> list[str]:
    """Map RuiWare's business material types into the template contract's supply forms."""
    explicit = material.get("supplyForms")
    if isinstance(explicit, list) and explicit:
        return [str(item) for item in explicit]
    haystack = " ".join(str(material.get(key) or "") for key in ("type", "name", "categoryGroup", "dataKind")).lower()
    return [form for form, aliases in SUPPLY_FORM_ALIASES.items() if any(alias.lower() in haystack for alias in aliases)]


def material_requirement_mismatches(requirement: MaterialRequirement, material: dict[str, Any]) -> list[str]:
    """Return human-readable reasons why a material cannot satisfy a template requirement."""
    reasons: list[str] = []
    supply_forms = infer_supply_forms(material)
    if requirement.supplyForm not in supply_forms:
        actual = "、".join(SUPPLY_FORM_LABELS.get(item, item) for item in supply_forms) or "未归类"
        expected = SUPPLY_FORM_LABELS.get(requirement.supplyForm, requirement.supplyForm)
        reasons.append(f"材料库供应形态为{actual}，模板要求为{expected}")
    thickness = material.get("thickness")
    constraint = requirement.thickness
    domain = effective_thickness_domain(requirement)
    if domain["empty"]:
        reasons.append("允许厚度值与厚度范围没有交集")
    elif domain["values"] and (thickness is None or not any(math.isclose(float(thickness), value, abs_tol=1e-9) for value in domain["values"])):
        reasons.append("厚度不在允许值内")
    if constraint.minimum is not None and (thickness is None or thickness < constraint.minimum):
        reasons.append("厚度小于允许下限")
    if constraint.maximum is not None and (thickness is None or thickness > constraint.maximum):
        reasons.append("厚度大于允许上限")
    if requirement.selectionMode in {"family", "specificRecord"}:
        if requirement.allowedGrades and material.get("grade") not in requirement.allowedGrades:
            reasons.append("牌号不在材料族范围内")
        if requirement.standards and material.get("standard") not in requirement.standards:
            reasons.append("标准不在材料族范围内")
        if requirement.surfaces and material.get("surface") not in requirement.surfaces:
            reasons.append("表面状态不在材料族范围内")
        if requirement.familyTags:
            searchable = " ".join(str(material.get(key) or "") for key in material).lower()
            if not all(tag.lower() in searchable for tag in requirement.familyTags):
                reasons.append("材料族标签未全部匹配")
    return reasons


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def checksum(record: dict[str, Any]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RuiWareMaterialLibrary:
    """Read-only adapter for the existing RuiWare material database."""

    source_id = "ruiware-materials"

    def __init__(self, database_path: Path):
        self.database_path = database_path.resolve()

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.database_path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def list(self, search: str = "", limit: int = 100) -> list[dict[str, Any]]:
        where = ""
        parameters: list[Any] = []
        terms = [term for term in re.split(r"[\s,，;；/|]+", search.strip()) if term]
        if terms:
            column_clause = " OR ".join(f"COALESCE(CAST({column} AS TEXT), '') LIKE ?" for column in SEARCHABLE_COLUMNS)
            where = "WHERE " + " AND ".join(f"({column_clause})" for _ in terms)
            for term in terms:
                parameters.extend([f"%{term}%"] * len(SEARCHABLE_COLUMNS))
        parameters.append(max(1, min(limit, 500)))
        query = f"SELECT {','.join(MATERIAL_COLUMNS)} FROM materials {where} ORDER BY type, code LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._normalize(dict(row)) for row in rows]

    def get(self, record_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {','.join(MATERIAL_COLUMNS)} FROM materials WHERE id = ?", (record_id,)
            ).fetchone()
        if row is None:
            raise KeyError(record_id)
        return self._normalize(dict(row))

    def _normalize(self, row: dict[str, Any]) -> dict[str, Any]:
        material = {
            "id": str(row["id"]),
            "code": row["code"],
            "name": row["name"],
            "type": row["type"],
            "categoryGroup": row["category_group"] or "",
            "dataKind": row["data_kind"],
            "length": _number(row["length"]),
            "width": _number(row["width"]),
            "height": _number(row["height"]),
            "thickness": _number(row["thickness"]),
            "grade": row["grade"] or None,
            "color": row["color"] or None,
            "surface": row["surface"] or None,
            "standard": row["standard"] or None,
            "stockQty": row["stock_qty"],
            "availableQty": row["available_qty"],
            "supplier": row["supplier"] or None,
            "status": row["status"],
            "updatedAt": row["updated_at"],
            "remark": row["remark"] or None,
        }
        material["supplyForms"] = infer_supply_forms(material)
        material["supplyFormSource"] = "由材料库类型映射"
        return material
