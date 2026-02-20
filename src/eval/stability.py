# -*- coding: utf-8 -*-
"""
src/eval/stability.py

Stability indices computed from refinement traces.

Indices:
1) global_score_oscillation:
   Mean absolute delta of global pass score between consecutive status snapshots.
2) constraint_flip_count:
   Total number of pass/fail flips across all constraints between consecutive snapshots.
3) stable_convergence_rounds:
   Number of trailing rounds where global status remains unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from src.io.schemas import TraceStep


@dataclass(frozen=True)
class StabilityIndices:
    global_score_oscillation: float
    constraint_flip_count: int
    stable_convergence_rounds: int


def _status_snapshots(trace: List[TraceStep]) -> List[Dict[str, bool]]:
    if not trace:
        return []
    snaps: List[Dict[str, bool]] = [dict(trace[0].status_before)]
    snaps.extend(dict(step.status_after) for step in trace)
    return snaps


def _global_score(status: Dict[str, bool]) -> float:
    if not status:
        return 1.0
    return sum(1 for ok in status.values() if bool(ok)) / len(status)


def compute_stability_indices(trace: List[TraceStep]) -> StabilityIndices:
    snaps = _status_snapshots(trace)
    if len(snaps) < 2:
        return StabilityIndices(
            global_score_oscillation=0.0,
            constraint_flip_count=0,
            stable_convergence_rounds=0,
        )

    total_abs_delta = 0.0
    total_flips = 0

    for i in range(1, len(snaps)):
        before = snaps[i - 1]
        after = snaps[i]

        total_abs_delta += abs(_global_score(after) - _global_score(before))

        keys = set(before.keys()) | set(after.keys())
        for cid in keys:
            if bool(before.get(cid, False)) != bool(after.get(cid, False)):
                total_flips += 1

    transitions = len(snaps) - 1
    mean_abs_delta = total_abs_delta / transitions if transitions else 0.0

    stable_tail = 0
    for i in range(len(snaps) - 1, 0, -1):
        if snaps[i] == snaps[i - 1]:
            stable_tail += 1
        else:
            break

    return StabilityIndices(
        global_score_oscillation=float(mean_abs_delta),
        constraint_flip_count=int(total_flips),
        stable_convergence_rounds=int(stable_tail),
    )
