# -*- coding: utf-8 -*-
"""
src/graph/build_drg_hybrid.py

混合模式图构建：规则推断 + LLM补充

策略：
1. 用确定性规则推断明显的依赖（OBJECT → 其他，COUNT → SPATIAL等）
2. 识别不确定的约束对（可能有隐式依赖）
3. 只让LLM判断这些不确定的对，减少LLM负担
4. 合并规则依赖和LLM补充的依赖

优势：
- 减少LLM调用次数和token消耗
- 保证基础依赖的正确性（规则）
- 捕获复杂隐式依赖（LLM）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
import json

from src.io.schemas import Constraint, ConstraintGraph, GraphEdge, ConstraintType
from src.llm.client import LLMClient, LLMParams


@dataclass(frozen=True)
class HybridDRGParams:
    # 规则部分
    add_object_dependencies: bool = True
    add_reference_dependencies: bool = True
    relation_depends_on_count: bool = True
    spatial_depends_on_count: bool = True
    
    # LLM补充部分
    use_llm_for_uncertain: bool = True
    llm_temperature: float = 0.0
    llm_max_tokens: int = 800
    
    # 不确定对的阈值（如果规则推断的依赖数量 < 这个比例，才调用LLM）
    uncertain_threshold: float = 0.3  # 如果少于30%的约束对有依赖，认为可能遗漏
    
    # 权重
    w_dependency: float = 1.0
    w_coupling: float = 0.3


def _norm_obj(s: Optional[str]) -> Optional[str]:
    """轻量级对象名归一化"""
    if s is None:
        return None
    s2 = s.strip().lower()
    if s2.endswith("s") and len(s2) > 3 and not s2.endswith("ss"):
        s2 = s2[:-1]
    return s2


def _index_object_constraints(constraints: List[Constraint]) -> Dict[str, str]:
    """返回 object_name -> constraint_id 映射"""
    obj2cid: Dict[str, str] = {}
    for c in constraints:
        if c.type == ConstraintType.OBJECT:
            obj = _norm_obj(c.object)
            if obj and obj not in obj2cid:
                obj2cid[obj] = c.id
    return obj2cid


def _add_edge(
    edges: List[GraphEdge],
    seen: Set[Tuple[str, str, str]],
    src: str,
    dst: str,
    edge_type: str,
    weight: float,
) -> None:
    """添加边（去重）"""
    if src == dst:
        return
    key = (src, dst, edge_type)
    if key in seen:
        return
    seen.add(key)
    edges.append(GraphEdge(source=src, target=dst, edge_type=edge_type, weight=weight))


# ============================================================
# 第一阶段：规则推断（确定性依赖）
# ============================================================

def _build_rule_based_deps(
    constraints: List[Constraint],
    params: HybridDRGParams,
) -> Tuple[List[GraphEdge], Set[Tuple[str, str, str]]]:
    """
    使用确定性规则推断明显的依赖关系
    返回：(edges, seen_keys)
    """
    obj2cid = _index_object_constraints(constraints)
    edges: List[GraphEdge] = []
    seen: Set[Tuple[str, str, str]] = set()
    
    # 规则1：OBJECT → 其他约束（基础依赖）
    if params.add_object_dependencies:
        for c in constraints:
            if c.type == ConstraintType.OBJECT:
                continue
            subj = _norm_obj(c.object)
            if subj and subj in obj2cid:
                _add_edge(
                    edges, seen,
                    src=obj2cid[subj],
                    dst=c.id,
                    edge_type="dependency",
                    weight=params.w_dependency,
                )
    
    # 规则2：reference依赖
    if params.add_reference_dependencies:
        for c in constraints:
            ref = _norm_obj(c.reference)
            if ref and ref in obj2cid:
                _add_edge(
                    edges, seen,
                    src=obj2cid[ref],
                    dst=c.id,
                    edge_type="dependency",
                    weight=params.w_dependency,
                )
    
    # 规则3：COUNT → SPATIAL/RELATION（数量确定后再定位）
    count_by_obj: Dict[str, List[str]] = {}
    for c in constraints:
        if c.type == ConstraintType.COUNT:
            obj = _norm_obj(c.object)
            if obj:
                count_by_obj.setdefault(obj, []).append(c.id)
    
    for c in constraints:
        obj = _norm_obj(c.object)
        if not obj or obj not in count_by_obj:
            continue
        
        if params.relation_depends_on_count and c.type == ConstraintType.RELATION:
            for count_id in count_by_obj[obj]:
                _add_edge(edges, seen, count_id, c.id, "dependency", params.w_dependency)
        
        if params.spatial_depends_on_count and c.type == ConstraintType.SPATIAL:
            for count_id in count_by_obj[obj]:
                _add_edge(edges, seen, count_id, c.id, "dependency", params.w_dependency)
    
    return edges, seen


# ============================================================
# 第二阶段：识别不确定的约束对
# ============================================================

def _find_uncertain_pairs(
    constraints: List[Constraint],
    rule_edges: List[GraphEdge],
) -> List[Tuple[Constraint, Constraint]]:
    """
    找出可能有依赖但规则未覆盖的约束对
    
    启发式：
    1. 两个约束提到相同或相关的物体
    2. 一个约束的reference是另一个的object
    3. 空间关系约束之间（如 A on B, B on C）
    """
    # 已有依赖的约束对
    existing_deps = {(e.source, e.target) for e in rule_edges}
    
    uncertain: List[Tuple[Constraint, Constraint]] = []
    
    for i, c1 in enumerate(constraints):
        for j, c2 in enumerate(constraints):
            if i >= j:
                continue
            
            # 跳过已有依赖的对
            if (c1.id, c2.id) in existing_deps or (c2.id, c1.id) in existing_deps:
                continue
            
            # 跳过两个OBJECT约束（它们之间通常无依赖）
            if c1.type == ConstraintType.OBJECT and c2.type == ConstraintType.OBJECT:
                continue
            
            # 启发式1：提到相同物体
            obj1 = _norm_obj(c1.object)
            obj2 = _norm_obj(c2.object)
            if obj1 and obj2 and obj1 == obj2:
                uncertain.append((c1, c2))
                continue
            
            # 启发式2：reference关系
            ref1 = _norm_obj(c1.reference)
            ref2 = _norm_obj(c2.reference)
            if (ref1 and obj2 and ref1 == obj2) or (ref2 and obj1 and ref2 == obj1):
                uncertain.append((c1, c2))
                continue
            
            # 启发式3：空间关系链（A on B, B on C）
            if c1.type == ConstraintType.SPATIAL and c2.type == ConstraintType.SPATIAL:
                if (ref1 and obj2 and ref1 == obj2) or (ref2 and obj1 and ref2 == obj1):
                    uncertain.append((c1, c2))
                    continue
            
            # 启发式4：属性依赖关系（如颜色依赖物体存在）
            if c1.type == ConstraintType.ATTRIBUTE and c2.type == ConstraintType.ATTRIBUTE:
                if obj1 and obj2 and obj1 == obj2:
                    uncertain.append((c1, c2))
                    continue
    
    return uncertain


# ============================================================
# 第三阶段：LLM判断不确定的依赖
# ============================================================

LLM_DEPENDENCY_PROMPT = """
你是一个依赖关系分析专家。

任务：判断两个约束之间是否存在依赖关系。

定义：如果约束B必须在约束A满足之后才能检查/修改，则B依赖A。

示例：
- "红色的球" 依赖 "球存在" ✓
- "球在桌子上" 依赖 "球存在" 和 "桌子存在" ✓
- "3个苹果" 和 "苹果是红色的" 无依赖（可以同时检查）✗

现在判断以下约束对：

{pairs_description}

输出严格JSON格式（无其他文字）：
{{
  "dependencies": [
    {{"from": "C1", "to": "C2", "reason": "C2依赖C1的原因"}},
    ...
  ]
}}

规则：
- 只输出确实存在依赖的对
- from是被依赖的约束，to是依赖者
- 如果不确定，不要输出
"""


def _query_llm_for_dependencies(
    client: LLMClient,
    uncertain_pairs: List[Tuple[Constraint, Constraint]],
    params: HybridDRGParams,
) -> List[Tuple[str, str]]:
    """
    让LLM判断不确定的约束对
    返回：[(from_id, to_id), ...]
    """
    if not uncertain_pairs:
        return []
    
    # 构建描述
    pairs_desc = []
    for i, (c1, c2) in enumerate(uncertain_pairs, 1):
        desc = f"""
对 {i}:
  C1 (id={c1.id}): type={c1.type.value}, object="{c1.object}", value="{c1.value or 'N/A'}", reference="{c1.reference or 'N/A'}"
  C2 (id={c2.id}): type={c2.type.value}, object="{c2.object}", value="{c2.value or 'N/A'}", reference="{c2.reference or 'N/A'}"
"""
        pairs_desc.append(desc.strip())
    
    pairs_description = "\n\n".join(pairs_desc)
    
    prompt = LLM_DEPENDENCY_PROMPT.format(pairs_description=pairs_description)
    
    messages = [
        {"role": "system", "content": "你是依赖关系分析专家。只输出JSON，无其他文字。"},
        {"role": "user", "content": prompt.strip()},
    ]
    
    try:
        raw = client.chat(
            task="extract_dependencies",
            messages=messages,
            params=LLMParams(
                temperature=params.llm_temperature,
                max_tokens=params.llm_max_tokens,
            ),
        )
        
        # 解析JSON
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        
        data = json.loads(raw)
        
        if not isinstance(data, dict) or "dependencies" not in data:
            print(f"[HYBRID_DRG] LLM返回格式错误，跳过LLM补充")
            return []
        
        deps = []
        for dep in data["dependencies"]:
            if isinstance(dep, dict) and "from" in dep and "to" in dep:
                deps.append((str(dep["from"]), str(dep["to"])))
        
        print(f"[HYBRID_DRG] LLM补充了 {len(deps)} 条依赖")
        return deps
    
    except Exception as e:
        print(f"[HYBRID_DRG] LLM调用失败: {e}，跳过LLM补充")
        return []


# ============================================================
# 主函数：混合模式图构建
# ============================================================

def build_drg_hybrid(
    constraints: List[Constraint],
    client: Optional[LLMClient] = None,
    params: Optional[HybridDRGParams] = None,
) -> ConstraintGraph:
    """
    混合模式构建DRG图
    
    流程：
    1. 规则推断确定性依赖
    2. 识别不确定的约束对
    3. LLM判断不确定的对（可选）
    4. 合并所有依赖
    """
    params = params or HybridDRGParams()
    
    print(f"[HYBRID_DRG] 开始构建图，约束数量: {len(constraints)}")
    
    # 阶段1：规则推断
    rule_edges, seen = _build_rule_based_deps(constraints, params)
    print(f"[HYBRID_DRG] 规则推断得到 {len(rule_edges)} 条依赖边")
    
    # 阶段2：识别不确定的对
    uncertain_pairs = _find_uncertain_pairs(constraints, rule_edges)
    print(f"[HYBRID_DRG] 识别到 {len(uncertain_pairs)} 对不确定的约束")
    
    # 阶段3：LLM补充（可选）
    llm_deps: List[Tuple[str, str]] = []
    if params.use_llm_for_uncertain and client is not None and uncertain_pairs:
        # 限制LLM调用：如果不确定对太多，只采样一部分
        max_pairs_for_llm = 20
        if len(uncertain_pairs) > max_pairs_for_llm:
            print(f"[HYBRID_DRG] 不确定对过多，采样 {max_pairs_for_llm} 对给LLM判断")
            import random
            uncertain_pairs = random.sample(uncertain_pairs, max_pairs_for_llm)
        
        llm_deps = _query_llm_for_dependencies(client, uncertain_pairs, params)
    
    # 阶段4：合并LLM补充的依赖
    edges = list(rule_edges)
    for from_id, to_id in llm_deps:
        _add_edge(
            edges, seen,
            src=from_id,
            dst=to_id,
            edge_type="dependency",
            weight=params.w_dependency,
        )
    
    print(f"[HYBRID_DRG] 最终图包含 {len(edges)} 条边")
    
    return ConstraintGraph(nodes=constraints, edges=edges)


