"""Source-only checks shared by brace authoring and CAD expansion."""
import re


INDEX_TOKEN = re.compile(r"\{index(?::0?([0-9]+)d)?\}")


def column_frame_members(document):
    """Recognize column frames from their declared type or structural members."""
    members = document.get('assembly', {}).get('members', [])
    groups = {key: [] for key in ('columns', 'horizontal', 'diagonal')}
    for member in members:
        if member.get('template', {}).get('templateKind') == 'component':
            continue  # A nested frame carries its own contract.
        text = (member.get('role', '') + ' ' + member.get('name', '')).lower()
        interfaces = [i.get('id', '').lower() for i in member.get('interfaces', [])]
        if '立柱' in text or 'upright' in text or any(i.startswith('upright.') for i in interfaces):
            groups['columns'].append(member['id'])
        elif '横撑' in text or 'crossbrace' in text or any(i.startswith('crossbrace.') for i in interfaces):
            groups['horizontal'].append(member['id'])
        elif '斜撑' in text or 'diagonal' in text or any(i.startswith('diagonalbrace.') for i in interfaces):
            groups['diagonal'].append(member['id'])
    definition = document.get('definition', {})
    declared = '柱片' in (definition.get('name', '') + ' ' + definition.get('category', ''))
    structural = len(groups['columns']) >= 2 and bool(groups['horizontal']) and bool(groups['diagonal'])
    return groups if declared or structural else None


def built_in_brace_issues(document):
    """A frame cannot be delivered with use-time configuration left unfinished.

    Disabled rules still count: the switch is a usage preference, not permission
    to omit the capability. Never infer ownership of legacy rods from names.
    """
    groups = column_frame_members(document)
    if groups is None:
        return []
    rules = document.get('braceGenerationRules', [])
    if not rules:
        return [{'code': 'AUTHORING_BRACE_RULE_REQUIRED',
                 'message': '柱片模板制作未完成：须在制作时内置横撑、斜撑补齐规则，并通过增高、缩短验收；使用时无需另行配置。'}]
    issues = []
    covered_columns = {r.get(key) for r in rules for key in ('leftColumnMemberId', 'rightColumnMemberId')}
    prototypes = {r.get(key) for r in rules for key in ('horizontalMemberId', 'diagonalMemberId')}
    rule_ids = {r.get('id') for r in rules}
    owned = {m['id'] for m in document.get('assembly', {}).get('members', []) if m.get('generatedBy') in rule_ids}
    uncovered = [m for m in groups['columns'] if m not in covered_columns]
    uncovered += [m for kind in ('horizontal', 'diagonal') for m in groups[kind] if m not in prototypes | owned]
    for identifier in uncovered:
        issues.append({'code': 'AUTHORING_BRACE_MEMBER_UNCOVERED', 'memberId': identifier,
                       'message': f'柱片成员 {identifier} 尚未纳入内置补齐规则；制作时按真实接口确认归属，不能留下不会随高度补齐的撑杆。'})
    bindings = document.get('bindings', [])
    for rule in rules:
        for key in ('leftColumnMemberId', 'rightColumnMemberId'):
            identifier = rule.get(key)
            if identifier and not any(b.get('memberId') == identifier and b.get('parameterId') == rule.get('heightParameterId') for b in bindings):
                issues.append({'code': 'AUTHORING_BRACE_HEIGHT_BINDING_REQUIRED', 'memberId': identifier,
                               'message': f'柱片立柱 {identifier} 未绑定规则的高度参数；请在制作时绑定实际长度驱动并完成CAD验收。'})
    return issues


def active_brace_rules(document):
    return [rule for rule in document.get('braceGenerationRules', []) if rule.get('enabled', True)]


def brace_relation_owner(identifier):
    """Parse the full reserved ID, so rule 'a' cannot delete rule 'a__b'."""
    if not isinstance(identifier, str) or not identifier.startswith('__brace__'):
        return None
    if identifier.endswith('__node'):
        identifier = identifier[:-6]
    parts = identifier[len('__brace__'):].rsplit('__', 3)
    if (len(parts) != 4 or not parts[0] or parts[1] not in ('horizontal', 'diagonal')
            or not parts[2].isascii() or not parts[2].isdecimal() or parts[3] not in ('left', 'right')):
        return None
    return parts[0]


def valid_interface_pattern(pattern):
    if not isinstance(pattern, str):
        return False
    matches = list(INDEX_TOKEN.finditer(pattern))
    if len(matches) != 1:
        return False
    width = matches[0].group(1)
    if width is not None and (len(width) > 2 or not 1 <= int(width) <= 10):
        return False
    remainder = INDEX_TOKEN.sub('', pattern)
    return '{' not in remainder and '}' not in remainder


def brace_rule_conflicts(document):
    """Columns may be shared; each physical brace prototype has one owner."""
    rules = document.get('braceGenerationRules', [])
    members = {m['id']: m for m in document.get('assembly', {}).get('members', [])}
    rule_ids = {r.get('id') for r in rules}
    columns = {r.get(key) for r in rules for key in ('leftColumnMemberId', 'rightColumnMemberId')}
    owners, seen, issues = {}, set(), []
    for rule in rules:
        identifier = rule.get('id')
        if identifier in seen:
            issues.append({'code': 'BRACE_RULE_ID_CONFLICT', 'ruleId': identifier,
                           'message': f'撑杆规则标识 {identifier} 重复，请使用不同标识'})
        seen.add(identifier)
        for key in ('horizontalMemberId', 'diagonalMemberId'):
            member_id = rule.get(key)
            if not member_id:
                continue  # Incomplete drafts have their own reference checks.
            member = members.get(member_id, {})
            label = member.get('name', member_id)
            if member_id in owners:
                issues.append({'code': 'BRACE_RULE_PROTOTYPE_CONFLICT', 'ruleId': identifier,
                               'otherRuleId': owners[member_id], 'memberId': member_id,
                               'message': f'撑杆 {label} 已由规则 {owners[member_id]} 使用，不能再次作为 {identifier} 的原型；请新增独立成员实例，可继续引用同一发布模板'})
            else:
                owners[member_id] = identifier
            if member_id in columns:
                issues.append({'code': 'BRACE_RULE_MEMBER_ROLE_CONFLICT', 'ruleId': identifier, 'memberId': member_id,
                               'message': f'{label} 同时被用作立柱和撑杆，请为不同角色使用独立成员'})
            if member.get('generatedBy') is not None and member['generatedBy'] in rule_ids:
                issues.append({'code': 'BRACE_RULE_GENERATED_PROTOTYPE', 'ruleId': identifier, 'memberId': member_id,
                               'message': f'{label} 是规则生成的撑杆，重建时会被清理；请使用独立的固定规格原型成员'})
    return issues
