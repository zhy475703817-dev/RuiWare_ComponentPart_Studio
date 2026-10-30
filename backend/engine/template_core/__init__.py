"""Pure domain model and deterministic lowering for part templates."""

from .models import CompileRequest, CompileResult, Diagnostic, SweepPathSketch, TemplateDraft
from .metamodel import SemanticFaceLocator
from .lowering import lower_to_plan
from .sweep_path_sampling import (
    map_point_to_3d,
    sample_arc_points,
    sample_ordered_path_points,
    sample_ordered_path_points_3d,
)

__all__ = [
    "CompileRequest",
    "CompileResult",
    "Diagnostic",
    "TemplateDraft",
    "SemanticFaceLocator",
    "SweepPathSketch",
    "lower_to_plan",
    "map_point_to_3d",
    "sample_arc_points",
    "sample_ordered_path_points",
    "sample_ordered_path_points_3d",
]
