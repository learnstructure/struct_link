"""
Material and concrete-section helpers shared by the CSI (SAP2000 / ETABS) writers.
"""

from __future__ import annotations

from typing import Any, List, Tuple

from struct_core import classify_frame_members

# struct_core material type -> CSI material type
_CSI_MATERIAL_TYPES = {"concrete": "Concrete", "rebar": "Rebar", "elastic": "Other", "bilinear": "Other"}


def _concrete_sections(model: Any) -> List[Tuple[Any, str]]:
    """Reinforced-concrete sections of ``model`` (those naming longitudinal rebar), each with its role: 'column' if
    any member using it is a column (``struct_core.classify_frame_members``), else 'beam'."""
    roles = classify_frame_members(model)
    columns = {e.section_id for e in model.elements if roles.get(e.id) == "column"}
    return [(sec, "column" if sec.id in columns else "beam")
            for sec in model.sections if getattr(sec, "rebar_material_id", None) is not None]
