from __future__ import annotations

import re

from .models import StageCheck, StageValidation, TemplateDraft
from .registries import registry_option_exists


CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_-]{2,39}$")


def validate_template_info(draft: TemplateDraft, *, code_unique: bool = True) -> StageValidation:
    checks = [
        StageCheck(id="code-format", label="模板编码有效", passed=bool(CODE_PATTERN.fullmatch(draft.code)), severity="error", path="code", message="编码须以字母开头，仅包含大写字母、数字、下划线或短横线，长度 3–40。"),
        StageCheck(id="code-unique", label="模板编码唯一", passed=code_unique, severity="error", path="code", message="该编码已被其他未归档草稿使用。"),
        StageCheck(id="name", label="模板名称完整", passed=len(draft.name.strip()) >= 3, severity="error", path="name", message="模板名称至少需要 3 个字符。"),
        StageCheck(id="manufacturing-origin", label="零部件来源已选择", passed=registry_option_exists("origins", draft.manufacturingClassification.originId), severity="error", path="manufacturingClassification.originId", message="请选择注册表中的零部件来源。"),
        StageCheck(id="primary-process", label="主成形工艺已选择", passed=registry_option_exists("primaryProcesses", draft.manufacturingClassification.primaryProcessId), severity="error", path="manufacturingClassification.primaryProcessId", message="请选择注册表中的主成形工艺。"),
        StageCheck(id="template-kind", label="模板范围为单体零部件", passed=draft.templateKind == "monolithicPart", severity="error", path="templateKind", message="当前生成器只接受单体零部件模板。"),
        StageCheck(id="geometry-prototype", label="初始几何原型有效", passed=registry_option_exists("geometryPrototypes", draft.geometryPrototypeId), severity="error", path="geometryPrototypeId", message="请选择有效的初始几何原型或自定义几何配方。"),
        StageCheck(id="classification-review", label="制造分类已确认", passed=draft.manufacturingClassification.reviewed, severity="error", path="manufacturingClassification.reviewed", message="请确认来源、主工艺和后续工序。"),
        StageCheck(id="description", label="用途说明清楚", passed=len(draft.description.strip()) >= 10, severity="error", path="description", message="请说明模板用途和适用范围。"),
        StageCheck(id="design-intent", label="设计意图可执行", passed=len(draft.designIntent.strip()) >= 20, severity="error", path="designIntent", message="请说明制造方式、主要形态和预期变化。"),
        StageCheck(id="owner", label="负责人已指定", passed=len(draft.owner.strip()) >= 2, severity="error", path="owner", message="请填写模板负责人。"),
        StageCheck(id="tags", label="检索标签已设置", passed=len(draft.tags) > 0, severity="warning", path="tags", message="建议至少设置一个检索标签。"),
        StageCheck(id="reference", label="参考资料已附加", passed=len(draft.attachments) > 0, severity="warning", path="attachments", message="建议上传实例图片、图纸或规格资料。"),
    ]
    blocking = [check for check in checks if check.severity == "error"]
    progress = round(sum(check.passed for check in blocking) / len(blocking) * 100)
    return StageValidation(stage="templateInfo", complete=all(check.passed for check in blocking), progress=progress, checks=checks)


def template_info_fingerprint(draft: TemplateDraft) -> tuple:
    return (
        draft.templateKind, draft.code, draft.name, draft.description, draft.designIntent,
        draft.manufacturingClassification.model_dump_json(), draft.geometryPrototypeId,
        tuple(draft.tags), draft.owner, draft.organization,
        draft.unitSystem, draft.coordinateSystem,
        tuple((item.id, item.sha256, item.kind) for item in draft.attachments),
    )
