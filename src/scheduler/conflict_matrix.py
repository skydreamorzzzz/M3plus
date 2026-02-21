# -*- coding: utf-8 -*-

"""
Track conflict statistics between constraints.
"""

from typing import Dict, List, Tuple
import numpy as np


class ConflictMatrix:

    def __init__(self):
        self.index: Dict[str, int] = {}
        self.reverse: List[str] = []
        self.matrix = np.zeros((0, 0), dtype=float)

    def _ensure(self, cid: str):
        if cid in self.index:
            return
        idx = len(self.reverse)
        self.index[cid] = idx
        self.reverse.append(cid)

        if self.matrix.shape[0] == 0:
            self.matrix = np.zeros((1, 1), dtype=float)
        else:
            n = self.matrix.shape[0]
            new = np.zeros((n + 1, n + 1), dtype=float)
            new[:n, :n] = self.matrix
            self.matrix = new

    def record_conflict(self, a: str, b: str):
        self._ensure(a)
        self._ensure(b)
        i = self.index[a]
        j = self.index[b]
        self.matrix[i, j] += 1
        self.matrix[j, i] += 1

    def risk_score(self, cid: str) -> float:
        if cid not in self.index:
            return 0.0
        i = self.index[cid]
        return float(self.matrix[i].sum())

    def export_dict(self, mode: str = "normalized") -> Dict[str, float]:
        """
        导出冲突风险字典
        
        Args:
            mode: 计算模式
                - "normalized": 归一化到 0-1 (推荐)
                - "raw": 原始计数
        
        Returns:
            {constraint_id: risk_value}
        """
        if not self.reverse:
            return {}
        
        if mode == "raw":
            return {cid: self.risk_score(cid) for cid in self.reverse}
        
        elif mode == "normalized":
            # 归一化到 0-1
            scores = {cid: self.risk_score(cid) for cid in self.reverse}
            max_score = max(scores.values()) if scores else 0.0
            
            if max_score == 0.0:
                return {cid: 0.0 for cid in self.reverse}
            
            return {cid: score / max_score for cid, score in scores.items()}
        
        else:
            # 默认归一化
            return self.export_dict(mode="normalized")
    
    def get_conflict_details(self, cid: str) -> Dict[str, float]:
        """
        获取某个约束与其他约束的冲突详情
        
        Returns:
            {other_constraint_id: conflict_count}
        """
        if cid not in self.index:
            return {}
        
        i = self.index[cid]
        details = {}
        
        for j, other_cid in enumerate(self.reverse):
            if i != j and self.matrix[i, j] > 0:
                details[other_cid] = float(self.matrix[i, j])
        
        return details
    
    def get_all_conflicts(self) -> List[Tuple[str, str, float]]:
        """
        获取所有冲突对
        
        Returns:
            [(constraint_a, constraint_b, conflict_count), ...]
        """
        conflicts = []
        n = len(self.reverse)
        
        for i in range(n):
            for j in range(i + 1, n):  # 只取上三角，避免重复
                if self.matrix[i, j] > 0:
                    conflicts.append((
                        self.reverse[i],
                        self.reverse[j],
                        float(self.matrix[i, j])
                    ))
        
        return conflicts
