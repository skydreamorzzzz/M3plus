# -*- coding: utf-8 -*-
"""
src/refine/loop_core.py

Refinement loop (global-check_all version).

Design:
- check_all(): authoritative global evaluation
- check_one(): only generates edit instruction for selected constraint
- conflict statistics updated from global status delta
- Early stopping on consecutive failures or no progress
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple
import copy

from src.io.schemas import (
    PromptItem, Constraint, TraceStep, RunSummary, ConstraintGraph,
    SchedulingDecision, ConflictEvolution
)
from src.refine.checker import Checker
from src.refine.editor import Editor, ArtifactHandle
from src.refine.verifier import Verifier, Decision
from src.scheduler.conflict_matrix import ConflictMatrix
from src.eval.oscillation import detect_oscillation
from src.eval.protection import constraint_protection_rate
from src.eval.stability import compute_stability_indices


# ============================================================
# Image similarity check (optional, for detecting no-change edits)
# ============================================================

def _images_identical(path1: str, path2: str) -> bool:
    """
    Check if two images are identical (pixel-wise).
    Returns True if identical, False otherwise.
    Handles errors gracefully (returns False if can't compare).
    """
    try:
        from pathlib import Path
        import hashlib
        
        p1 = Path(path1)
        p2 = Path(path2)
        
        # Quick check: if paths are the same, they're identical
        if p1 == p2:
            return True
        
        # Check if both files exist
        if not p1.exists() or not p2.exists():
            return False
        
        # Quick check: if file sizes differ, they're different
        if p1.stat().st_size != p2.stat().st_size:
            return False
        
        # Compare file hashes (fast and reliable)
        def file_hash(path: Path) -> str:
            h = hashlib.md5()
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    h.update(chunk)
            return h.hexdigest()
        
        return file_hash(p1) == file_hash(p2)
    
    except Exception as e:
        # If comparison fails, assume they're different (conservative)
        print(f"[LOOP] Warning: Could not compare images: {e}")
        return False


# ============================================================
# Params
# ============================================================

@dataclass(frozen=True)
class LoopParams:
    max_rounds: int = 8
    early_stop: bool = True
    accept_on_same: bool = False
    max_conflict_count: int = 5
    quality_threshold: float = 0.8  # Minimum quality score to stop (0-1)
    max_consecutive_failures: int = 3  # Stop after N consecutive edit failures
    max_no_progress_rounds: int = 3  # Stop if no improvement for N rounds


# ============================================================
# Utilities
# ============================================================

def _constraint_map(constraints: List[Constraint]) -> Dict[str, Constraint]:
    return {c.id: c for c in constraints}


def _all_pass(status: Dict[str, bool]) -> bool:
    return all(bool(v) for v in status.values()) if status else True


def _diff_status(before: Dict[str, bool], after: Dict[str, bool]) -> Tuple[List[str], List[str]]:
    degraded: List[str] = []
    improved: List[str] = []
    for cid, ok_before in before.items():
        ok_after = bool(after.get(cid, False))
        if ok_before and not ok_after:
            degraded.append(cid)
        if not ok_before and ok_after:
            improved.append(cid)
    return degraded, improved


# ============================================================
# Main loop
# ============================================================

def run_refine_loop(
    item: PromptItem,
    scheduler: Any,
    checker: Checker,
    editor: Editor,
    verifier: Verifier,
    params: Optional[LoopParams] = None,
    initial_artifact: Optional[ArtifactHandle] = None,
    conflict_matrix: Optional[ConflictMatrix] = None,
    out_dir: Optional[Any] = None,
    strategy_name: str = "unknown",  # 新增：策略名称
) -> Tuple[ArtifactHandle, List[TraceStep], RunSummary, List[SchedulingDecision], List[ConflictEvolution]]:

    params = params or LoopParams()

    # Set default out_dir
    if out_dir is None:
        from pathlib import Path
        out_dir = Path("runs") / item.prompt_id

    constraints = item.constraints or []
    cmap = _constraint_map(constraints)

    graph: ConstraintGraph
    if item.graph is not None:
        graph = item.graph
    else:
        graph = ConstraintGraph(nodes=constraints, edges=[])

    best = initial_artifact or ArtifactHandle(
        payload=f"artifact://{item.prompt_id}/init",
        meta={"prompt_id": item.prompt_id},
    )

    cm = conflict_matrix or ConflictMatrix()
    traces: List[TraceStep] = []
    scheduling_decisions: List[SchedulingDecision] = []  # 新增：调度决策记录
    conflict_evolutions: List[ConflictEvolution] = []    # 新增：冲突演化记录
    conflict_count = 0
    consecutive_failures = 0  # Track consecutive edit failures
    no_progress_rounds = 0  # Track rounds with no improvement

    # ============================================================
    # Initial global evaluation (constraints + quality)
    # ============================================================

    status_best, feedback_best = checker.check_all_with_feedback(
        prompt_text=item.text,
        artifact=best,
        constraints=constraints,
    )
    
    # Score initial quality
    quality_best = 0.0
    if hasattr(checker.backend, "score_quality"):
        try:
            quality_result = checker.backend.score_quality(
                prompt_text=item.text,
                artifact=best,
            )
            quality_best = float(quality_result.get("quality_score", 0.0))
        except Exception:
            quality_best = 0.0  # Fallback if scoring fails

    # ============================================================
    # Iterative refinement (quality-aware)
    # ============================================================

    for t in range(params.max_rounds):

        # Check stopping conditions
        all_pass = _all_pass(status_best)
        
        # Stop if: all constraints pass AND quality is good
        if params.early_stop and all_pass and quality_best >= params.quality_threshold:
            print(f"[LOOP] Early stop: all constraints pass and quality sufficient")
            break

        if conflict_count >= params.max_conflict_count:
            print(f"[LOOP] Stop: max conflict count reached ({conflict_count})")
            break
        
        # NEW: Stop if too many consecutive edit failures
        if consecutive_failures >= params.max_consecutive_failures:
            print(f"[LOOP] Stop: {consecutive_failures} consecutive edit failures")
            break
        
        # NEW: Stop if no progress for too long
        if no_progress_rounds >= params.max_no_progress_rounds:
            print(f"[LOOP] Stop: no progress for {no_progress_rounds} rounds")
            break

        status_before = copy.deepcopy(status_best)
        quality_before = quality_best
        
        # ============================================================
        # Decide what to edit
        # ============================================================
        
        is_quality_round = False
        edit_instruction = ""
        selected = ""
        
        if not all_pass:
            # Case A: Some constraints failed → fix failed constraints
            conflict_risk = cm.export_dict(mode="normalized")  # 归一化冲突风险
            
            # 收集调度决策信息
            if hasattr(scheduler, 'get_scheduling_info'):
                sched_info = scheduler.get_scheduling_info(graph, status_best, conflict_risk)
            else:
                # 兼容旧调度器
                sched_info = {
                    "candidates": [],
                    "scores": {},
                    "selected": None,
                    "reason": "unknown",
                }
            
            order = scheduler.schedule(
                graph=graph,
                status=status_best,
                conflict_risk=conflict_risk,
            )
            
            if not order:
                break
            
            selected = order[0]
            if selected not in cmap:
                continue
            
            # 记录调度决策
            scheduling_decisions.append(SchedulingDecision(
                prompt_id=item.prompt_id,
                round_id=t,
                strategy=strategy_name,
                candidate_constraints=sched_info.get("candidates", []),
                candidate_scores=sched_info.get("scores", {}),
                selected_constraint=selected,
                selection_reason=sched_info.get("reason", "unknown"),
                base_priorities=sched_info.get("base_priorities"),
                conflict_risks=sched_info.get("conflict_risks"),
            ))
            
            # 记录冲突演化
            for cid in cmap.keys():
                conflict_evolutions.append(ConflictEvolution(
                    prompt_id=item.prompt_id,
                    round_id=t,
                    constraint_id=cid,
                    conflict_count=int(cm.risk_score(cid)),
                    conflict_risk=float(conflict_risk.get(cid, 0.0)),
                    conflicts_with=cm.get_conflict_details(cid),
                ))
            
            constraint = cmap[selected]
            
            # Generate constraint-fixing instruction
            from src.refine.checker import _mk_instruction
            base_instruction = _mk_instruction(constraint)
            judge_feedback = (feedback_best.get(selected, "") or "").strip()

            if judge_feedback:
                edit_instruction = (
                    f"{base_instruction} "
                    f"Address this judge feedback specifically: {judge_feedback}"
                )
            else:
                edit_instruction = base_instruction
            is_quality_round = False
        
        else:
            # Case B: All constraints pass, but quality is low → improve quality
            if quality_best < params.quality_threshold:
                selected = "__quality__"  # Special marker for quality improvement
                
                # Generate quality improvement instruction
                edit_instruction = (
                    "Improve the overall quality of this image: "
                    "enhance composition, refine details, improve lighting and colors. "
                    "Keep all existing content intact, only improve visual quality."
                )
                is_quality_round = True
            else:
                # Should not reach here (early_stop would have triggered)
                break

        # ============================================================
        # Apply edit
        # ============================================================

        candidate = editor.edit(
            artifact=best,
            instruction=edit_instruction,
            round_id=t,
            prompt_id=item.prompt_id,
            out_dir=out_dir,
        )
        
        # ============================================================
        # Check if edit actually happened
        # ============================================================
        
        edit_failed = False
        if hasattr(candidate, "meta") and isinstance(candidate.meta, dict):
            # Check if editor returned fallback (edit failed)
            if candidate.meta.get("fallback", False):
                edit_failed = True
                consecutive_failures += 1
                print(f"[LOOP] Round {t}: Edit failed (fallback), consecutive failures: {consecutive_failures}")
            
            # Check if in dry_run mode (no actual edit)
            elif candidate.meta.get("dry_run", False):
                # Dry run is expected, not a failure
                consecutive_failures = 0
            
            # Check if payload unchanged (edit had no effect)
            elif candidate.payload == best.payload:
                edit_failed = True
                consecutive_failures += 1
                print(f"[LOOP] Round {t}: Edit had no effect (payload unchanged), consecutive failures: {consecutive_failures}")
            
            # Check if images are identical (pixel-wise comparison)
            elif not candidate.payload.startswith("mock://") and not best.payload.startswith("mock://"):
                if _images_identical(candidate.payload, best.payload):
                    edit_failed = True
                    consecutive_failures += 1
                    print(f"[LOOP] Round {t}: Edit produced identical image, consecutive failures: {consecutive_failures}")
                else:
                    # Edit succeeded and produced different image
                    consecutive_failures = 0
            
            else:
                # Edit succeeded
                consecutive_failures = 0
        
        # If edit failed, skip evaluation and record failure
        if edit_failed:
            error_type = candidate.meta.get("error_type", "unknown") if hasattr(candidate, "meta") else "unknown"
            
            traces.append(
                TraceStep(
                    round_id=t,
                    selected_constraint=selected,
                    status_before=status_before,
                    status_after=copy.deepcopy(status_best),  # No change
                    degraded_constraints=[],
                    improved_constraints=[],
                    edit_instruction=edit_instruction,
                    accepted=False,
                    error_type=error_type,
                    edit_fallback=True,
                    quality_score_before=quality_before,
                    quality_score_after=quality_before,  # No change
                    quality_improvement=is_quality_round,
                )
            )
            
            conflict_count += 1
            no_progress_rounds += 1
            continue  # Skip to next round

        # ============================================================
        # Global evaluation of candidate (constraints + quality)
        # ============================================================

        status_candidate, feedback_candidate = checker.check_all_with_feedback(
            prompt_text=item.text,
            artifact=candidate,
            constraints=constraints,
        )
        
        # Score candidate quality
        quality_candidate = 0.0
        if hasattr(checker.backend, "score_quality"):
            try:
                quality_result = checker.backend.score_quality(
                    prompt_text=item.text,
                    artifact=candidate,
                )
                quality_candidate = float(quality_result.get("quality_score", 0.0))
            except Exception:
                quality_candidate = quality_best  # Fallback to previous score

        # ============================================================
        # Verifier decision (enhanced with quality awareness)
        # ============================================================

        # Simple quality-aware decision (bypass verifier if quality clearly improves)
        n_pass_before = sum(1 for v in status_before.values() if v)
        n_pass_after = sum(1 for v in status_candidate.values() if v)
        
        accepted = False
        made_progress = False  # Track if this round made any progress
        
        # Accept if: more constraints pass OR (same constraints + better quality)
        if n_pass_after > n_pass_before:
            accepted = True
            made_progress = True
        elif n_pass_after == n_pass_before and quality_candidate > quality_before:
            accepted = True
            made_progress = True
        elif n_pass_after == n_pass_before and quality_candidate == quality_before:
            # Use verifier for tie-breaking
            decision: Decision = verifier.verify(
                prompt_text=item.text,
                best_artifact=best,
                candidate_artifact=candidate,
                status_best=status_best,
                status_candidate=status_candidate,
                extra={"selected": selected, "round": t, "quality_before": quality_before, "quality_after": quality_candidate},
            )
            if decision == "better" or (decision == "same" and params.accept_on_same):
                accepted = True
                made_progress = (decision == "better")
        
        # Update progress counter
        if made_progress:
            no_progress_rounds = 0
        else:
            no_progress_rounds += 1
        
        if accepted:
            best = candidate
            status_best = status_candidate
            feedback_best = feedback_candidate
            quality_best = quality_candidate
            print(f"[LOOP] Round {t}: Accepted (n_pass: {n_pass_before} → {n_pass_after}, quality: {quality_before:.3f} → {quality_candidate:.3f})")
        else:
            conflict_count += 1
            print(f"[LOOP] Round {t}: Rejected (n_pass: {n_pass_before} → {n_pass_after}, quality: {quality_before:.3f} → {quality_candidate:.3f})")
            
            # Check if candidate is significantly worse (regression)
            if n_pass_after < n_pass_before:
                print(f"[LOOP] Round {t}: Regression detected ({n_pass_before - n_pass_after} constraints degraded)")

        # ============================================================
        # Conflict statistics (based on global delta)
        # ============================================================

        degraded, improved = _diff_status(status_before, status_best)

        for cid in degraded:
            if cid != selected:
                cm.record_conflict(selected, cid)

        # Extract error info from candidate artifact (if editor fallback occurred)
        error_type = None
        edit_fallback = False
        if hasattr(candidate, "meta") and isinstance(candidate.meta, dict):
            error_type = candidate.meta.get("error_type")
            edit_fallback = candidate.meta.get("fallback", False)

        traces.append(
            TraceStep(
                round_id=t,
                selected_constraint=selected,
                status_before=status_before,
                status_after=copy.deepcopy(status_best),
                degraded_constraints=degraded,
                improved_constraints=improved,
                edit_instruction=edit_instruction,
                accepted=accepted,
                error_type=error_type,
                edit_fallback=edit_fallback,
                quality_score_before=quality_before,
                quality_score_after=quality_best if accepted else quality_before,
                quality_improvement=is_quality_round,
            )
        )

    # ============================================================
    # Summary
    # ============================================================

    final_pass = _all_pass(status_best)
    quality_improved = any(t.quality_improvement for t in traces)

    oscillation = detect_oscillation(traces)
    protection_rate = constraint_protection_rate(traces)
    stability = compute_stability_indices(traces)

    summary = RunSummary(
        prompt_id=item.prompt_id,
        total_rounds=len(traces),
        final_pass=bool(final_pass),
        conflict_count=int(conflict_count),
        oscillation_detected=oscillation.has_oscillation,
        protection_rate=protection_rate,
        global_score_oscillation=stability.global_score_oscillation,
        constraint_flip_count=stability.constraint_flip_count,
        stable_convergence_rounds=stability.stable_convergence_rounds,
        final_quality_score=quality_best,
        quality_improved=quality_improved,
    )

    return best, traces, summary, scheduling_decisions, conflict_evolutions
