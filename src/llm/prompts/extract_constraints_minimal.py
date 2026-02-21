# -*- coding: utf-8 -*-
"""
src/llm/prompts/extract_constraints_minimal.py

极简约束提取（减少LLM输出长度）

策略：
- LLM只输出核心信息（一行一个约束）
- 本地解析并组装完整Constraint对象
- 减少80%的输出token

格式：
    type: object [| field: value]

示例：
    object: red cube
    count: 3 | object: apples
    spatial: on | object: cube | reference: table
"""

from __future__ import annotations

from typing import List, Optional
import re

from src.io.schemas import Constraint, ConstraintType
from src.llm.client import LLMClient, LLMParams


# ============================================================
# Prompt Template
# ============================================================

MINIMAL_SYSTEM_PROMPT = """
You are a visual constraint extractor.

Your task: Extract atomic visual constraints in minimal format.

Output format (one constraint per line):
    type: object [| field: value]

Types (MUST use these exact names):
- object: object existence
- count: quantity of objects
- spatial: spatial relationship
- attribute: visual attribute (color, size, shape, etc.)
- relation: relationship between objects
- text: text content

Rules:
1. One constraint per line
2. Use | to separate additional fields
3. Only include non-empty fields
4. Keep object names simple and clear
5. NO explanations, NO markdown, NO extra text
"""

MINIMAL_USER_TEMPLATE = """
Extract constraints from this prompt:

"{prompt}"

Examples of correct format:
object: red cube
count: 3 | object: apples
spatial: on | object: cube | reference: table
attribute: color | object: cube | value: red
text: HELLO | object: sign

Now extract (one per line):
"""


# ============================================================
# Main Extraction Function
# ============================================================

def extract_constraints_minimal(
    client: LLMClient,
    prompt_text: str,
    temperature: float = 0.0,
    max_tokens: int = 800,
) -> List[Constraint]:
    """
    使用极简格式提取约束
    
    Args:
        client: LLM客户端
        prompt_text: 用户prompt
        temperature: LLM温度
        max_tokens: 最大token数（极简格式下800足够）
    
    Returns:
        List[Constraint]
    """
    messages = [
        {"role": "system", "content": MINIMAL_SYSTEM_PROMPT.strip()},
        {"role": "user", "content": MINIMAL_USER_TEMPLATE.format(prompt=prompt_text).strip()},
    ]
    
    raw = client.chat(
        task="extract_constraints_minimal",
        messages=messages,
        params=LLMParams(temperature=temperature, max_tokens=max_tokens),
    )
    
    print(f"[EXTRACT_MINIMAL] Raw output length: {len(raw)} chars")
    
    # 解析输出
    constraints = parse_minimal_output(raw, prompt_text)
    
    print(f"[EXTRACT_MINIMAL] Extracted {len(constraints)} constraints")
    
    return constraints


# ============================================================
# Parsing Functions
# ============================================================

def parse_minimal_output(raw: str, prompt_head: str = "") -> List[Constraint]:
    """
    解析极简格式输出
    
    Args:
        raw: LLM原始输出
        prompt_head: prompt前缀（用于错误日志）
    
    Returns:
        List[Constraint]
    """
    # 清理输出
    raw = raw.strip()
    
    # 移除markdown代码块（如果有）
    raw = re.sub(r"```.*?\n", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```", "", raw)
    
    # 分割成行
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    
    constraints = []
    for i, line in enumerate(lines, 1):
        # 跳过注释或说明性文字
        if line.startswith("#") or line.startswith("//"):
            continue
        
        # 跳过不包含冒号的行（可能是说明文字）
        if ":" not in line:
            continue
        
        try:
            c = parse_minimal_line(line, i)
            constraints.append(c)
        except Exception as e:
            print(f"[EXTRACT_MINIMAL] Warning: Failed to parse line {i}: '{line}', error: {e}")
            continue
    
    # 确保ID唯一
    constraints = _ensure_unique_ids(constraints)
    
    return constraints


def parse_minimal_line(line: str, index: int) -> Constraint:
    """
    解析单行约束
    
    格式：type: object [| field: value]
    
    示例：
        object: red cube
        count: 3 | object: apples
        spatial: on | object: cube | reference: table
        attribute: color | object: cube | value: red
    
    Args:
        line: 单行文本
        index: 行号（用于生成ID）
    
    Returns:
        Constraint
    """
    # 分割主体和额外字段
    parts = [p.strip() for p in line.split("|")]
    
    if len(parts) == 0:
        raise ValueError(f"Empty line")
    
    # 解析主体（type: object）
    main = parts[0]
    if ":" not in main:
        raise ValueError(f"Missing colon in main part: {main}")
    
    type_str, obj_str = main.split(":", 1)
    type_str = type_str.strip().lower()
    obj_str = obj_str.strip()
    
    # 验证类型
    try:
        constraint_type = ConstraintType(type_str)
    except ValueError:
        raise ValueError(f"Unknown constraint type: {type_str}")
    
    # 解析额外字段
    extras = {}
    for part in parts[1:]:
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        extras[k.strip()] = v.strip()
    
    # 如果extras中有object，覆盖主体的object
    if "object" in extras:
        obj_str = extras["object"]
    
    # 构建Constraint
    return Constraint(
        id=f"C{index}",
        type=constraint_type,
        object=obj_str if obj_str else "unknown",
        value=extras.get("value"),
        relation=extras.get("relation"),
        reference=extras.get("reference"),
        confidence=1.0,
    )


def _ensure_unique_ids(constraints: List[Constraint]) -> List[Constraint]:
    """确保约束ID唯一"""
    seen = set()
    out: List[Constraint] = []
    
    for i, c in enumerate(constraints, start=1):
        cid = c.id
        if cid in seen or not cid:
            cid = f"C{i}"
            c = Constraint(
                id=cid,
                type=c.type,
                object=c.object,
                value=c.value,
                relation=c.relation,
                reference=c.reference,
                confidence=c.confidence,
            )
        seen.add(cid)
        out.append(c)
    
    return out


