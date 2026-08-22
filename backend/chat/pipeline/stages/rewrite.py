"""Compatibility wrapper for the renamed turn-resolution stage."""

from backend.chat.pipeline.stages.turn_resolution import (
    rewrite_query,
    run_turn_resolution_stage,
)


run_rewrite_stage = run_turn_resolution_stage

__all__ = ["rewrite_query", "run_rewrite_stage"]
