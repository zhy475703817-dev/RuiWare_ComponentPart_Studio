"""Deterministic semantic comparison for published part packages."""
import hashlib
import io
import json
import stat
import unicodedata
from pathlib import PurePosixPath
import zipfile


SIMILARITY_VERSION = 1
MAX_ENTRIES = 256
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024

_FILES = {
    "G": ("geometry-recipe.json", "constraints.json", "sweep-path.json", "sketch-solver.json"),
    "P": ("parameters.json", "parameter-dependencies.json"),
    "F": ("feature-rules.json", "variants.json", "interfaces.json"),
    "M": ("classification.json", "material-requirements.json", "material-validation.json"),
}
_WEIGHTS = {"G": 0.55, "P": 0.25, "F": 0.12, "M": 0.08}
_IDENTITY_KEYS = {
    "author", "authorId", "createdAt", "updatedAt", "publicationId", "templateId",
    "sourceRevision", "evidence", "aiProposals", "buildId", "buildHash", "outputHash",
    "sha256", "url", "downloadUrl", "artifactUrl", "packagePath",
}
_UNORDERED_ARRAYS = {"entities", "interfaces", "rules", "variants", "definitions", "samples"}
_ENTITY_ARRAYS = {"entities", "geometry", "constraints", "operations", "interfaces", "rules", "variants", "semanticFaces", "samples", "requirements"}


class PackageFormatError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _read_documents(content: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                raise PackageFormatError("PACKAGE_TOO_LARGE")
            names = set()
            for entry in entries:
                name = entry.orig_filename
                path = PurePosixPath(name)
                if (name in names or name.startswith("/") or "\\" in name or ":" in name or
                        ".." in path.parts or "\0" in name or stat.S_ISLNK(entry.external_attr >> 16) or
                        entry.flag_bits & 1 or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise PackageFormatError("PACKAGE_INVALID")
                names.add(name)
            documents = {}
            for category_files in (("manifest.json",), *_FILES.values()):
                for name in category_files:
                    if name not in names:
                        continue
                    info = archive.getinfo(name)
                    if info.file_size > MAX_JSON_BYTES:
                        raise PackageFormatError("PACKAGE_TOO_LARGE")
                    value = json.loads(archive.read(info), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON number")))
                    if not isinstance(value, (dict, list)):
                        raise PackageFormatError("PACKAGE_INVALID")
                    documents[name] = value
            manifest = documents.get('manifest.json')
            if (not isinstance(manifest, dict) or manifest.get('schemaVersion') != '3.0'
                    or manifest.get('unitSystem') != 'mm-kg-s'
                    or manifest.get('coordinateSystem') != 'right-handed-z-up'
                    or not isinstance(manifest.get('id'), str) or not manifest['id']
                    or not isinstance(manifest.get('code'), str) or not manifest['code']):
                raise PackageFormatError('PACKAGE_UNSUPPORTED')
            if "geometry-recipe.json" not in documents:
                raise PackageFormatError("CRITICAL_GEOMETRY_MISSING")
            if "parameters.json" not in documents:
                raise PackageFormatError("CRITICAL_PARAMETERS_MISSING")
            recipe = documents['geometry-recipe.json']
            parameters = documents['parameters.json']
            if not isinstance(recipe, dict) or not isinstance(recipe.get('operations'), list) or not recipe['operations']:
                raise PackageFormatError('CRITICAL_GEOMETRY_MISSING')
            if not isinstance(parameters, dict) or not isinstance(parameters.get('definitions'), list):
                raise PackageFormatError('CRITICAL_PARAMETERS_MISSING')
            return documents
    except PackageFormatError:
        raise
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, json.JSONDecodeError):
        raise PackageFormatError("PACKAGE_INVALID") from None


def _entity_maps(documents: dict) -> dict[str, str]:
    identifiers = {}
    counts = {}

    def visit(value, array_name=""):
        if isinstance(value, dict):
            identifier = value.get("id")
            if array_name in _ENTITY_ARRAYS and isinstance(identifier, str):
                if identifier not in identifiers:
                    index = counts.get(array_name, 0)
                    identifiers[identifier] = f"{array_name}-{index}"
                    counts[array_name] = index + 1
            for key, item in value.items():
                visit(item, key if isinstance(item, list) else array_name)
        elif isinstance(value, list):
            for item in value:
                visit(item, array_name)

    for name in sorted(documents):
        value = documents[name]
        visit(value)
    return identifiers


def _canonical(value, id_map=None, key=""):
    id_map = id_map or {}
    if isinstance(value, dict):
        result = {}
        for raw_key in sorted(value, key=lambda item: unicodedata.normalize("NFC", str(item))):
            normalized_key = unicodedata.normalize("NFC", str(raw_key))
            if normalized_key in _IDENTITY_KEYS:
                continue
            item = value[raw_key]
            if normalized_key == "id" and key in _ENTITY_ARRAYS and isinstance(item, str):
                item = id_map.get(item, item)
            elif (normalized_key.endswith("Id") or normalized_key.endswith("Ids")):
                if isinstance(item, str):
                    item = id_map.get(item, item)
                elif isinstance(item, list):
                    item = [id_map.get(entry, entry) if isinstance(entry, str) else entry for entry in item]
            result[normalized_key] = _canonical(item, id_map, normalized_key)
        return result
    if isinstance(value, list):
        result = [_canonical(item, id_map, key) for item in value]
        if key in _UNORDERED_ARRAYS:
            result.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return result
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    return value


def _fields(value, prefix="") -> set[str]:
    if isinstance(value, dict):
        if not value:
            return {prefix + "={}"}
        fields = set()
        for key, item in value.items():
            pointer = f"{prefix}/{key.replace('~', '~0').replace('/', '~1')}"
            fields.update(_fields(item, pointer))
        return fields
    if isinstance(value, list):
        if not value:
            return {prefix + "=[]"}
        fields = set()
        for index, item in enumerate(value):
            fields.update(_fields(item, f"{prefix}/{index}"))
        return fields
    return {f"{prefix}={json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"}


def analyze_package(content: bytes) -> dict:
    try:
        documents = _read_documents(content)
    except PackageFormatError as error:
        return {"similarityVersion": SIMILARITY_VERSION, "candidateClass": "unclassified", "reason": error.code}
    id_map = _entity_maps(documents)
    categories = {}
    for category, filenames in _FILES.items():
        categories[category] = set()
        for filename in filenames:
            if filename in documents:
                categories[category].update(_fields(_canonical(documents[filename], id_map), '/' + filename))
    return {"similarityVersion": SIMILARITY_VERSION, "candidateClass": "analyzed", "categories": categories}


def compare_packages(left: bytes, right: bytes) -> dict:
    left_hash = hashlib.sha256(left).hexdigest()
    right_hash = hashlib.sha256(right).hexdigest()
    left_result = analyze_package(left)
    right_result = analyze_package(right)
    return compare_analyses(left_result, right_result, exact_duplicate=left_hash == right_hash)


def compare_analyses(left_result: dict, right_result: dict, *, exact_duplicate=False) -> dict:
    if left_result["candidateClass"] == "unclassified" or right_result["candidateClass"] == "unclassified":
        reason = left_result.get("reason") or right_result.get("reason")
        return {"similarityVersion": SIMILARITY_VERSION, "candidateClass": "unclassified", "reason": reason,
                "exactDuplicate": exact_duplicate}
    scores = {}
    differences = {}
    for category in _FILES:
        left_fields = left_result["categories"][category]
        right_fields = right_result["categories"][category]
        union = left_fields | right_fields
        scores[category] = round(100 * len(left_fields & right_fields) / len(union), 2) if union else 0.0
        differences[category] = {"leftOnly": sorted(left_fields - right_fields)[:20],
                                 "rightOnly": sorted(right_fields - left_fields)[:20]}
    scores["S"] = round(sum(scores[key] * weight for key, weight in _WEIGHTS.items()), 2)
    exact = exact_duplicate
    if exact:
        candidate = "exact-duplicate"
    elif scores["S"] >= 90 and scores["G"] >= 95 and scores["P"] >= 90:
        candidate = "high-similarity"
    elif scores["S"] >= 80 or (scores["G"] >= 95 and scores["P"] >= 85):
        candidate = "suspected-similarity"
    else:
        candidate = "none"
    return {"similarityVersion": SIMILARITY_VERSION, "candidateClass": candidate,
            "exactDuplicate": exact, "scores": scores, "differences": differences}
