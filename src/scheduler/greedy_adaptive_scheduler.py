# -*- coding: utf-8 -*-
"""
src/scheduler/greedy_adaptive_scheduler.py

自适应贪心调度器

策略：
- 类型优先级 + 动态冲突惩罚
- 根据历史冲突调整权重
- 自适应避免高冲突约束

得分公式：
    score(i) = type_priority(i) - α × conflict_risk(i)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.io.schemas import ConstraintType


@dataclass(frozen=True)
class GreedyAdaptiveParams:
    """自适应贪心调度器参数"""
    
    # 类型优先级映射（同静态版本）
    type_priority: Dict[str, float] = field(default_factory=lambda: {
        "object": 10.0,
        "count": 8.0,
        "attribute": 5.0,
        "spatial": 5.0,
        "relation": 3.0,
        "text": 3.0,
    })
    
    # 冲突惩罚权重（核心参数）
    conflict_penalty: float = 5.0
    
    # 是否只调度未通过的约束
    failed_only: bool = True


class GreedyAdaptiveScheduler:
    """
    自适应贪心调度器
    
    Contract (compatible with loop_core.py):
        schedule(graph, status, conflict_risk=None) -> List[str]
    """
    
    def __init__(self, params: Optional[GreedyAdaptiveParams] = None) -> None:
        self.params = params or GreedyAdaptiveParams()
    
    def schedule(
        self,
        graph: Any,
        status: Dict[str, bool],
        conflict_risk: Optional[Dict[str, float]] = None,
    ) -> List[str]:
        """
        返回按自适应得分排序的约束ID列表
        
        Args:
            graph: 约束图（使用 graph.nodes）
            status: 约束状态 {constraint_id: passed}
            conflict_risk: 冲突风险 {constraint_id: risk_value (0-1)}
        
        Returns:
            排序后的约束ID列表
        """
        nodes = self._get_nodes(graph)
        
        if not nodes:
            return []
        
        # 筛选未通过的约束
        if self.params.failed_only:
            candidates = [n for n in nodes if not bool(status.get(self._get_id(n), False))]
        else:
            candidates = list(nodes)
        
        if not candidates:
            return []
        
        # 计算得分
        risk = conflict_risk or {}
        scores = {}
        
        for n in candidates:
            node_id = self._get_id(n)
            node_type = self._get_type(n)
            
            # 基础优先级
            base_priority = self.params.type_priority.get(node_type, 0.0)
            
            # 冲突风险
            risk_value = float(risk.get(node_id, 0.0))
            
            # 最终得分
            score = base_priority - self.params.conflict_penalty * risk_value
            scores[node_id] = score
        
        # 按得分降序排序
        sorted_ids = sorted(scores.keys(), key=lambda x: (scores[x], x), reverse=True)
        
        return sorted_ids
    
    def _get_nodes(self, graph: Any) -> List[Any]:
        """提取图中的节点"""
        nodes = getattr(graph, "nodes", None)
        if not nodes:
            return []
        return list(nodes)
    
    def _get_id(self, node: Any) -> str:
        """提取节点ID"""
        if isinstance(node, str):
            return node
        if hasattr(node, "id"):
            return str(node.id)
        return str(node)
    
    def _get_type(self, node: Any) -> str:
        """提取节点类型"""
        if hasattr(node, "type"):
            t = node.type
            if isinstance(t, ConstraintType):
                return t.value
            return str(t)
        return "unknown"
    
    def get_scheduling_info(
        self,
        graph: Any,
        status: Dict[str, bool],
        conflict_risk: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        返回调度决策的详细信息（用于数据收集）
        
        Returns:
            {
                "candidates": [constraint_id, ...],
                "scores": {constraint_id: score, ...},
                "base_priorities": {constraint_id: priority, ...},
                "conflict_risks": {constraint_id: risk, ...},
                "selected": constraint_id,
                "reason": "adaptive_score",
            }
        """
        nodes = self._get_nodes(graph)
        
        if not nodes:
            return {
                "candidates": [],
                "scores": {},
                "base_priorities": {},
                "conflict_risks": {},
                "selected": None,
                "reason": "no_nodes",
            }
        
        # 筛选候选
        if self.params.failed_only:
            candidates = [n for n in nodes if not bool(status.get(self._get_id(n), False))]
        else:
            candidates = list(nodes)
        
        if not candidates:
            return {
                "candidates": [],
                "scores": {},
                "base_priorities": {},
                "conflict_risks": {},
                "selected": None,
                "reason": "all_passed",
            }
        
        # 计算详细信息
        risk = conflict_risk or {}
        scores = {}
        base_priorities = {}
        conflict_risks = {}
        
        for n in candidates:
            node_id = self._get_id(n)
            node_type = self._get_type(n)
            
            base_priority = self.params.type_priority.get(node_type, 0.0)
            risk_value = float(risk.get(node_id, 0.0))
            score = base_priority - self.params.conflict_penalty * risk_value
            
            base_priorities[node_id] = base_priority
            conflict_risks[node_id] = risk_value
            scores[node_id] = score
        
        # 排序
        sorted_ids = self.schedule(graph, status, conflict_risk)
        selected = sorted_ids[0] if sorted_ids else None
        
        return {
            "candidates": [self._get_id(n) for n in candidates],
            "scores": scores,
            "base_priorities": base_priorities,
            "conflict_risks": conflict_risks,
            "selected": selected,
            "reason": "adaptive_score",
        }

