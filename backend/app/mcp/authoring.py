"""Creation-time guidance for agents; never invent geometry or change a draft."""
from ..components.resize_solver import fingerprint
from ..components.brace_policy import brace_rule_conflicts, built_in_brace_issues


POLICY_VERSION = "2026-11-20.3"
AUTHORING_RULES = (
    ("column-frame", "每个柱片模板必须在制作时自带可执行的横撑、斜撑自动补齐规则，不需要用户额外提出。读取真实立柱孔列及撑杆端口，用component_set_brace_generation_rule保存规则，绑定两根立柱的高度驱动，覆盖全部立柱与撑杆。撑杆保持原规格、长度，增高新增实例，缩短移除多余实例。制作时完成开启自动补齐的原尺寸、增高、缩短验收；缺规则、漏成员或缺高度绑定的模板不得交付或发布。用户使用时只需选择自动补齐和目标高度，不得要求其临时找AI配置。旧模板也在制作阶段升级，无法依据真实接口确定时保留为未完成草稿，不能虚构规则或擅自删除用户成员。"),
    ("selection", "选型时读取固定发布版本的真实接口、几何来源和参数定义，确认预期调整方向有可用的驱动参数。缺少接口或可调参数时先补齐来源模板或选择合适模板，不得虚构接口、参数或修改发布版本的限制。"),
    ("assembly", "摆放成员时同步建立真实的贴合、同轴、重合或有依据的保留偏移关系；逐一处理未连接零件。多层组件必须检查内部叶子零件和对外端口映射，不能只摆放外观或填写文字连接说明。"),
    ("parameters", "制作时检查实际尺寸与历史参数、mm单位、rad旋转、参数上下限、整数和离散规格；仅使用模板公开的可调输入。固定尺寸及锁定必须来自设计要求，不得自动锁定报错零件或随意配对接口来绕过检查。"),
    ("verification", "装配过程中读取实际CAD尺寸，尽早进行原尺寸完整联动试算，提前发现接口无法定位、孤立成员、关系歧义和约束冲突。inspectOnly只读取尺寸，不算联动验收。对用户要求支持的每个调整方向，在合法范围内分别试算增大和缩小，并验证预期的多轴组合调整；仅测试增大不能代表缩小可用。孔列或阵列数量会变化时，制作中确认已连接、对外及槽位引用的接口仍能生成且来源不变，必要时联动合法的孔距或端距，不能删除连接来绕过失败。柱片要求按新增孔位补横撑、斜撑时，读取真实孔位和撑杆端口后调用component_set_brace_generation_rule保存可执行规则；横撑与斜撑分别读取端口跨度对应的孔列模式、孔距、编号起点和跨孔步长，使用diagonalLeftColumnInterfacePattern、diagonalRightColumnInterfacePattern、diagonalPitchParameterId、diagonalColumnInterfaceIndexStart和diagonalColumnInterfaceIndexStep表达不同节点网格，明确余量策略、固定撑杆规格，并验证增高与缩小会按规则增删实例且撑杆几何尺寸不变。"),
    ("repair", "收到authoring.issues或试算失败时，能依据真实模板和明确设计意图修复的立即在制作流程内修复并复验；无法唯一确定的连接或设计取舍集中询问用户。不得把可预防的问题留给用户日后调整时处理。"),
    ("handoff", "交付前调用component_validate_draft，检查基础数据及真实几何结果。仅在用户授权的目标和范围内应用预览；试验尺寸不得自动覆盖交付尺寸。未完成验证须明确未完成项，不能把保存成功、导出成功或自行填写verified视作联动通过，也不能将有限试算承诺为任意尺寸都可调整。"),
)
AUTHORING_INSTRUCTIONS = (
    "AI制作组件默认遵循以下制作要求，必须在选型、装配和参数配置时提前解决可预防的问题，而不是只在制作后报告：\n"
    + "\n".join(f"{index}. {text}" for index, (_, text) in enumerate(AUTHORING_RULES, 1))
    + "\n柱片撑杆规则必须明确连接用途：仅按真实孔位定位时设置connectionMode=geometry；要建立正式装配连接时设置connectionMode=mates，并先检查来源配合契约、关系和接口容量。缺少契约不得虚构补齐，几何通过不代表工程配合已验收。"
    + "\n柱片规则同样受锁定约束：整件锁定保留原位置、角度和尺寸，尺寸锁定只保留指定参数。目标高度与锁定立柱长度冲突、或缩短将移除已锁定撑杆时必须报出冲突，不能重设锁定基线、自动解除锁定或删除用户约束。应用前检查changes中的已有零件调整及structureChanges中的新增/移除清单。"
    + "\n多条撑杆规则可共用立柱，但每根撑杆原型只能由一条规则使用；需要同规格撑杆时新增独立成员实例，继续引用原发布模板。不得用生成实例作为原型、让同一成员兼任立柱和撑杆、或在同一对真实孔位重复生成撑杆。孔列模式必须含一个{index}或{index:03d}编号占位符；横撑和斜撑的孔距均需引用正数mm参数。"
    + "\n斜撑起点上移后，可用高度须扣除真实起点偏移，不能沿用横撑分格数；不同孔列依据真实CAD坐标确定偏移，逐个核对交替斜撑的实际两端孔位及顶部余量。分步恢复仅使用所有撑杆规则共同允许的中间高度，最终目标和使用范围保持用户指定值；相邻合法高度之间没有中间分格时，不得强行试算非法高度或改写目标。"
    + "\n几何联动中的柱片调整窗口提供自动补齐选择与高度增减。规则enabled默认true；用户明确关闭后使用普通几何联动，保留现有撑杆数量和规则配置。resize请求autoFillBraces可在预览中切换全部根规则，应用后保存选择；不得为绕过失败而自行关闭自动补齐。"
    + "\n调用component_get_authoring_requirements可重新读取完整要求。创建、读取、编辑和验证草稿返回authoring制作检查。"
    "选型优先使用component_get_template_authoring_info读取公开参数范围及当前展开接口，无需包内STL；"
    "默认草稿尺寸只是示例，须按用户要求与真实来源配置参数、范围和绑定。"
    "编辑后平台自动排队执行制作验收；制作完成必须调用component_finish_authoring并轮询component_authoring_status，"
    "只有当前修订deliveryReady=true才可标记完成。component_configure_authoring用于声明用户要求的使用范围，不能删除失败测试或缩小需求来绕过验收。"
    "几何验证使用component_resize_preview（expectedRevision；inspectOnly=true读取实际尺寸，"
    "inspectOnly=false且省略dimensions检查原尺寸的完整联动；dimensions=[X,Y,Z]试调），"
    "随后轮询component_resize_status直到结束，检查结果后按授权调用component_resize_apply。"
    "应用后再次读取修订并验证；取消试验预览保留原组件。组件文字工程规则仍不执行，几何通过不代表承载或焊缝检查通过。"
)


def requirements():
    return {"version": POLICY_VERSION, "appliesTo": "all-ai-component-authoring",
            "rules": [{"stage": stage, "requirement": text} for stage, text in AUTHORING_RULES],
            "braceConnectionModes": {
                "geometry": "真实孔位的几何定位关系；CAD验证位置和方向，不声明工程配合契约通过。",
                "mates": "正式装配连接；须检查来源配合契约、所选连接关系和接口容量。旧规则默认使用此模式。",
            },
            "verificationTools": ["component_finish_authoring", "component_authoring_status", "component_configure_authoring", "component_resize_preview", "component_resize_status", "component_validate_draft"],
            "draftsMayBeIncomplete": True,
            "workflow": [
                {"stage": "selection", "tools": ["component_search_templates", "component_get_template_authoring_info"]},
                {"stage": "definition", "tools": ["component_create_draft", "component_update_definition", "component_apply_actions"],
                 "requirement": "新草稿的默认尺寸为示例；根据真实零件公开参数定义建立组件参数与绑定，不能把示例范围当作用户设计要求。"},
                {"stage": "assembly", "tools": ["component_add_template_member", "component_connect_interfaces", "component_inherit_interface", "component_set_binding", "component_set_brace_generation_rule"]},
                {"stage": "qualification", "tools": ["component_configure_authoring", "component_finish_authoring", "component_authoring_status", "component_cancel_authoring"]},
                {"stage": "delivery", "tools": ["component_publish_draft", "component_get_publication", "component_add_template_member"]},
            ],
            "completionCriteria": {"draft": "当前修订authoring.deliveryReady=true，且声明的用户使用范围已验收。",
                "publication": "readiness.templateStatus=qualified，attachmentsComplete=true，可下载并重新作为子组件引用。",
                "engineering": "规则说明不执行；承载、焊缝、紧固件强度等未实现工程验算须另行验证。"}}


def authoring_report(draft, history=None):
    """Conservative early findings from snapshots, not a replacement for CAD checks.

    Missing declarations are pending: real CAD can infer undeclared contacts.
    A server-applied, checksum-bound result can resolve those snapshot findings.
    Imported `verified` flags alone are never trusted as evidence.
    """
    document = draft["document"]
    issues = []

    def issue(code, message, path=(), status="pending"):
        issues.append({"code": code, "status": status, "path": list(path), "message": message})

    for check in draft.get("checks", []):
        if check["status"] == "fail":
            issue("AUTHORING_DATA_INVALID", check["message"], [check["id"]], "fail")

    # Check nested copies as well as top-level members. Endpoint aliases resolve
    # component ports to leaves, so a connected parent cannot hide an orphan child.
    leaves, endpoints, relations, locks = {}, {}, [], []

    def visit(doc, prefix=()):
        if len(prefix) > 16 or len(leaves) > 150:
            issue("AUTHORING_RESOURCE_LIMIT", "嵌套或零件数量超过联动限制，请先调整组件结构。", prefix, "fail")
            return
        for finding in built_in_brace_issues(doc):
            issue(finding['code'], finding['message'], (*prefix, *([finding['memberId']] if finding.get('memberId') else [])), 'fail')
        design = doc.get("geometryDesign") or {}
        locks.extend((*prefix, *lock["path"]) for lock in design.get("locks", []) if lock.get("parameterId") is None)
        for member in doc["assembly"]["members"]:
            path = (*prefix, member["id"])
            nested = member.get("instanceDocument")
            if member["template"].get("templateKind") == "component":
                if nested:
                    visit(nested, path)
                    for port in nested["ports"]:
                        source = port["source"]
                        key = ((*path, source["memberId"]), source["interfaceId"])
                        if key in endpoints:
                            endpoints[(path, port["id"])] = endpoints[key]
                else:
                    issue("AUTHORING_NESTED_SOURCE_UNCHECKED", f"{member['name']}：需通过真实CAD检查固定发布子组件的内部接口和联动关系。", path)
            else:
                leaves[path] = member
                for interface in member["interfaces"]:
                    endpoints[(path, interface["id"])] = path
        for mate in doc["assembly"]["mates"]:
            relations.append(tuple(((*prefix, mate[side]["memberId"]), mate[side]["interfaceId"]) for side in ("from", "to")))
        for slot in doc["slots"]:
            base = slot.get("baseInterface")
            if base and slot.get("memberId") and slot.get("interfaceId"):
                relations.append((((*prefix, base["memberId"]), base["interfaceId"]), ((*prefix, slot["memberId"]), slot["interfaceId"])))
        for relation in design.get("relations", []):
            relations.append(tuple(((*prefix, *relation[side]["path"]), relation[side]["interfaceId"]) for side in ("a", "b")))

    visit(document)
    for conflict in brace_rule_conflicts(document):
        issue(conflict['code'], conflict['message'], [conflict.get('memberId') or conflict.get('ruleId')], 'fail')
    root_members = {member['id']: member for member in document['assembly']['members']}
    for rule in document.get('braceGenerationRules', []):
        if rule.get('connectionMode', 'mates') != 'mates':
            continue
        source_ports = (
            (rule.get('horizontalMemberId'), rule.get('horizontalLeftInterfaceId')),
            (rule.get('horizontalMemberId'), rule.get('horizontalRightInterfaceId')),
            (rule.get('diagonalMemberId'), rule.get('diagonalLeftInterfaceId')),
            (rule.get('diagonalMemberId'), rule.get('diagonalRightInterfaceId')),
        )
        for member_id, interface_id in source_ports:
            member = root_members.get(member_id)
            interface = next((item for item in (member or {}).get('interfaces', []) if item['id'] == interface_id), None)
            if interface is not None and not interface.get('mating'):
                issue('AUTHORING_BRACE_CONTRACT_UNCONFIRMED',
                      f"{member['name']}：撑杆规则要求正式装配连接，但接口 {interface_id} 缺少配合契约。"
                      "仅需孔位几何定位时明确设置connectionMode=geometry；工程配合需先完善来源模板契约并复验。",
                      (member_id, interface_id))
    if len(leaves) > 150:
        issue("AUTHORING_RESOURCE_LIMIT", "超过150个叶子零件，请在制作时拆分组件。", status="fail")
    connected = set()
    for a, b in relations:
        if a in endpoints and b in endpoints and endpoints[a] != endpoints[b]:
            connected.update((endpoints[a], endpoints[b]))
    for path, member in leaves.items():
        locked = any(path[:len(lock)] == lock for lock in locks)
        if not locked and len(leaves) > 1 and path not in connected:
            issue("AUTHORING_RELATION_UNCONFIRMED", f"{member['name']}：制作时补充实际连接关系，或用完整CAD试算确认可自动识别的接触关系；不要通过锁定来消除提示。", path)
        if not locked and not member["interfaces"]:
            issue("AUTHORING_INTERFACE_UNCONFIRMED", f"{member['name']}：尚无接口声明，选型时确认来源模板能提供真实几何接口。", path)
        definitions = member.get("parameterDefinitions", [])
        if not locked and not definitions:
            issue("AUTHORING_PARAMETERS_UNCHECKED", f"{member['name']}：先读取来源模板参数，确认可调尺寸及范围；刚性零件可仅随连接移动。", path)
        for definition in definitions:
            value = member["parameterValues"].get(definition["id"], definition["default"])
            if definition["valueType"] not in ("number", "integer"):
                continue
            minimum, maximum = definition["minimum"], definition["maximum"]
            invalid = (isinstance(value, bool) or not isinstance(value, (float, int))
                       or minimum is not None and value < minimum or maximum is not None and value > maximum
                       or definition["valueType"] == "integer" and value != int(value)
                       or bool(definition.get("allowedValues")) and value not in definition["allowedValues"])
            if invalid:
                issue("AUTHORING_PARAMETER_INVALID", f"{member['name']}：{definition['label']}不符合模板范围、整数或离散规格要求，请在制作时修正。", (*path, definition["id"]), "fail")

    verified = False
    design = document.get("geometryDesign") or {}
    if history and history.get("appliedRevision") == document["revision"] and design.get("verified"):
        candidate = {**document, "revision": history["expectedRevision"]}
        verified = fingerprint(candidate) == history.get("result", {}).get("checksum")
    if verified:
        issues = [item for item in issues if item["status"] == "fail"]
    else:
        issue("AUTHORING_GEOMETRY_UNVERIFIED", "制作中先读取实际尺寸并完成原尺寸联动试算；交付前还需试调要求支持的方向，保存授权的最终尺寸并复验。")
    return {"policyVersion": POLICY_VERSION, "revision": document["revision"],
            "status": "needs_attention" if any(item["status"] == "fail" for item in issues) else "pending" if issues else "geometry_verified",
            "geometryVerified": verified, "issues": issues,
            "requiredNextStep": "修复上述制作检查项，继续实际CAD试算；遵循component_get_authoring_requirements。" if issues else "当前保存尺寸的几何已验证；仍须针对用户要求支持的调整方向试算，不能承诺任意尺寸或工程承载通过。",
            "verificationScope": "当前保存尺寸；参数范围内的其他尺寸及工程规则不由此检查证明。"}
