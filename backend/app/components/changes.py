"""Field differences for collaboration notices; IDs keep list edits stable."""

LABELS = {
    "definition": "组件定义", "name": "名称", "code": "编码", "category": "类型", "description": "说明",
    "projectCode": "项目编码", "owner": "项目负责人", "organization": "所属组织", "searchTags": "检索标签",
    "partConnectionType": "零部件连接形式", "partConnectionCustom": "零部件自定义连接形式",
    "componentConnectionType": "组件间连接形式", "componentConnectionCustom": "组件间自定义连接形式",
    "designConstraints": "设计约束与可变范围", "evidenceFiles": "证据与参考",
    "fileName": "文件名", "mediaType": "文件类型", "size": "文件大小", "sha256": "文件校验值",
    "assembly": "装配", "members": "成员", "mates": "内部连接", "role": "角色", "position": "位置",
    "rotation": "旋转", "parameterValues": "参数覆盖", "parameters": "组件参数", "value": "数值",
    "rules": "规则", "expression": "表达式", "ports": "对外接口", "slots": "槽位", "bindings": "参数绑定",
    "source": "来源", "from": "起点", "to": "终点", "relation": "连接关系", "offset": "偏移",
    "memberId": "成员引用", "interfaceId": "接口引用", "required": "必填", "tags": "标签",
    "template": "模板引用", "interfaces": "接口", "unit": "单位", "min": "下限", "max": "上限",
}


def document_changes(before: dict, after: dict) -> list[dict]:
    items = []

    def visit(left, right, path, labels, left_exists=True, right_exists=True):
        if left_exists and right_exists and left == right:
            return
        if left_exists and right_exists and isinstance(left, dict) and isinstance(right, dict):
            for key in dict.fromkeys([*left, *right]):
                if not path and key == "revision":
                    continue
                visit(left.get(key), right.get(key), [*path, key], [*labels, LABELS.get(key, key)], key in left, key in right)
            return
        if left_exists and right_exists and isinstance(left, list) and isinstance(right, list):
            if all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in [*left, *right]):
                old, new = {item["id"]: item for item in left}, {item["id"]: item for item in right}
                for key in dict.fromkeys([*old, *new]):
                    item = new.get(key, old.get(key))
                    label = item.get("label") or item.get("name") or item.get("role") or key
                    visit(old.get(key), new.get(key), [*path, key], [*labels, label], key in old, key in new)
                return
        items.append({"path": path, "label": " / ".join(labels), "before": left, "after": right,
                      "kind": "added" if not left_exists else "removed" if not right_exists else "updated"})

    visit(before, after, [], [])
    return items
