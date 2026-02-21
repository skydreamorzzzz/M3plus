# -*- coding: utf-8 -*-
"""
src/scheduler/greedy_static_scheduler.py

静态贪心调度器

策略：
- 基于约束类型的固定优先级
- 不考虑历史冲突
- 完全确定性

优先级规则：
- OBJECT（物体存在）优先级最高
- COUNT（数量）次之
- 其他类型较低
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.io.schemas import ConstraintType


@dataclass(frozen=True)
class GreedyStaticParams:
    """静态贪心调度器参数"""
    
    # 类型优先级映射
    type_priority: Dict[str, float] = field(default_factory=lambda: {
        "object": 10.0,
        "count": 8.0,
        "attribute": 5.0,
        "spatial": 5.0,
        "relation": 3.0,
        "text": 3.0,
    })
    
    # 是否只调度未通过的约束
    failed_only: bool = True


class GreedyStaticScheduler:
    """
    静态贪心调度器
    
    Contract (compatible with loop_core.py):
        schedule(graph, status, conflict_risk=None) -> List[str]
    """
    
    def __init__(self, params: Optional[GreedyStaticParams] = None) -> None:
        self.params = params or GreedyStaticParams()
    
    def schedule(
        self,
        graph: Any,
        status: Dict[str, bool],
        conflict_risk: Optional[Dict[str, float]] = None,
    ) -> List[str]:
        """
        返回按静态优先级排序的约束ID列表
        
        Args:
            graph: 约束图（使用 graph.nodes）
            status: 约束状态 {constraint_id: passed}
            conflict_risk: 冲突风险（忽略，静态版本不使用）
        
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
        
        # 按优先级排序
        sorted_nodes = sorted(
            candidates,
            key=lambda n: self._get_sort_key(n),
            reverse=True  # 降序：优先级高的在前
        )
        
        return [self._get_id(n) for n in sorted_nodes]
    
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
    
    def _get_sort_key(self, node: Any) -> tuple:
        """
        生成排序键
        
        返回：(priority, -id)
        - priority: 类型优先级（高的在前）
        - -id: 负的ID（字典序，确保确定性）
        """
        node_type = self._get_type(node)
        priority = self.params.type_priority.get(node_type, 0.0)
        node_id = self._get_id(node)
        
        # 返回 (priority, -id)，这样排序时：
        # 1. 优先级高的在前
        # 2. 同优先级按ID升序
        return (priority, node_id)
    
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
                "selected": constraint_id,
                "reason": "type_priority",
            }
        """
        nodes = self._get_nodes(graph)
        
        if not nodes:
            return {
                "candidates": [],
                "scores": {},
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
                "selected": None,
                "reason": "all_passed",
            }
        
        # 计算得分（静态版本：得分=优先级）
        scores = {}
        for n in candidates:
            node_id = self._get_id(n)
            node_type = self._get_type(n)
            priority = self.params.type_priority.get(node_type, 0.0)
            scores[node_id] = priority
        
        # 排序
        sorted_ids = self.schedule(graph, status, conflict_risk)
        selected = sorted_ids[0] if sorted_ids else None
        
        return {
            "candidates": [self._get_id(n) for n in candidates],
            "scores": scores,
            "selected": selected,
            "reason": "type_priority",
        }

