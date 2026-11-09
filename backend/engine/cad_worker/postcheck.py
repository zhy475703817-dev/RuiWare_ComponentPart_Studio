from __future__ import annotations

from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer


def solid_count(shape) -> int:
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def check_brep(shape) -> tuple[bool, GProp_GProps, int]:
    valid = BRepCheck_Analyzer(shape).IsValid()
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    solids = solid_count(shape)
    return valid, properties, solids
