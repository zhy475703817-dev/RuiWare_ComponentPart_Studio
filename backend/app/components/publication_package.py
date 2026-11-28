from io import BytesIO
import json
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def build_component_package(
    manifest: dict, document: dict, checks: list[dict], qualification: dict, evidence: list[dict] | None = None,
) -> bytes:
    entries = [
        ("manifest.json", manifest),
        ("component.json", document),
        ("checks.json", checks),
        ("qualification.json", qualification),
    ]
    if evidence is not None:
        entries.append(("evidence.json", evidence))
    output = BytesIO()
    with ZipFile(output, "w") as package:
        for name, value in entries:
            info = ZipInfo(name)
            info.compress_type = ZIP_DEFLATED
            package.writestr(info, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return output.getvalue()
