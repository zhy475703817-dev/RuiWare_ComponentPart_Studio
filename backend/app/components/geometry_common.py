"""Common values for pinned CAD inputs and resize diagnostics."""
import hashlib
import json


class ResizeFailure(ValueError):
    def __init__(self, message, code="RESIZE_UNRESOLVED", details=None):
        super().__init__(message)
        self.code, self.details = code, details or []


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def template_key(reference):
    return reference["publicationId"], reference["sha256"].lower()
