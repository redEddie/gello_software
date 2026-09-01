"""Standalone dialogs used by the workspace collection GUI."""

from apps.dialogs.grid_editor_dialog import GridEditorDialog
from apps.dialogs.hdf5_tree_dialog import Hdf5TreeDialog
from apps.dialogs.new_scene_dialog import NewSceneDialog
from apps.dialogs.pipeline_dialog import PipelineDialog
from apps.dialogs.plan_edit_dialog import PlanEditDialog
from apps.dialogs.plan_json_dialog import PlanJsonDialog
from apps.dialogs.recommend_dialog import RecommendDialog

__all__ = [
    "GridEditorDialog",
    "Hdf5TreeDialog",
    "NewSceneDialog",
    "PipelineDialog",
    "PlanEditDialog",
    "PlanJsonDialog",
    "RecommendDialog",
]
