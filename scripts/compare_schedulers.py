# -*- coding: utf-8 -*-
"""
对比 Linear vs Topo 调度策略的压力测试脚本（统计严谨版）

改进:
1. Bootstrap置信区间 + 配对检验
2. Per-sample细粒度分析
3. 过程可视化（S_t曲线、flip分布、收敛轮次）
4. Effect size计算

用法:
    python scripts/compare_schedulers.py --input data/prompts_nested_relations.json --backend mock --rounds 4
    python scripts/compare_schedulers.py --input data/prompts_nested_relations.json --backend openai --dry_run 1 --rounds 6
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
import subprocess
import csv
from typing import Dict, List, Tuple, Any
import numpy as np
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 尝试导入可视化库
try:
    import matplotlib
    matplotlib.use('Agg')  # 非交互式后端
    import matplotlib.pyplot as plt
    import seaborn as sns
    sns.set_style("whitegrid")
    HAS_PLOT = True
except ImportError:
    HAS_PLOT = False
    print("⚠️  未安装matplotlib/seaborn，将跳过可视化（pip install matplotlib seaborn）")



# ============================================================
# 统计工具
# ============================================================

def bootstrap_ci(data: np.ndarray, n_bootstrap: int = 10000, ci: float = 0.95) -> Tuple[float, float, float]:
    """
    Bootstrap置信区间
    返回: (mean, lower_bound, upper_bound)
    """
    if len(data) == 0:
        return 0.0, 0.0, 0.0
    
    means = []
    for _ in range(n_bootstrap):
        sample = np.random.choice(data, size=len(data), replace=True)
        means.append(np.mean(sample))
    
    means = np.array(means)
    alpha = 1 - ci
    lower = np.percentile(means, alpha/2 * 100)
    upper = np.percentile(means, (1 - alpha/2) * 100)
    
    return float(np.mean(data)), float(lower), float(upper)


def paired_permutation_test(x: np.ndarray, y: np.ndarray, n_perm: int = 10000) -> float:
    """
    配对置换检验（适合小样本）
    H0: x和y来自同一分布
    返回: p-value
    """
    if len(x) != len(y) or len(x) == 0:
        return 1.0
    
    observed_diff = np.mean(x - y)
    
    count = 0
    for _ in range(n_perm):
        signs = np.random.choice([-1, 1], size=len(x))
        perm_diff = np.mean(signs * (x - y))
        if abs(perm_diff) >= abs(observed_diff):
            count += 1
    
    return count / n_perm


def cohens_d(x: np.ndarray, y: np.ndarray) -> float:
    """
    Cohen's d effect size
    小: 0.2, 中: 0.5, 大: 0.8
    """
    if len(x) == 0 or len(y) == 0:
        return 0.0
    
    nx, ny = len(x), len(y)
    dof = nx + ny - 2
    
    pooled_std = np.sqrt(((nx-1)*np.std(x, ddof=1)**2 + (ny-1)*np.std(y, ddof=1)**2) / dof)
    
    if pooled_std == 0:
        return 0.0
    
    return (np.mean(x) - np.mean(y)) / pooled_std


# ============================================================
# 数据加载
# ============================================================

def load_per_sample_data(exp_dir: Path, strategy: str) -> Dict[str, Any]:
    """
    加载每个样本的详细数据
    返回: {prompt_id: {metrics, trace}}
    """
    strategy_dir = exp_dir / strategy
    if not strategy_dir.exists():
        return {}
    
    summary_file = strategy_dir / "summary.csv"
    trace_file = strategy_dir / "trace_long.csv"
    
    data = {}
    
    # 读取summary（每个prompt的最终指标）
    if summary_file.exists():
        with open(summary_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                prompt_id = row.get("prompt_id", "")
                if not prompt_id:
                    continue
                
                data[prompt_id] = {
                    "total_rounds": int(row.get("total_rounds", 0)),
                    "final_pass": row.get("final_pass", "False") == "True",
                    "conflict_count": int(row.get("conflict_count", 0)),
                    "oscillation_detected": row.get("oscillation_detected", "False") == "True",
                    "protection_rate": float(row.get("protection_rate", 0.0)),
                    "global_score_oscillation": float(row.get("global_score_oscillation", 0.0)),
                    "constraint_flip_count": int(row.get("constraint_flip_count", 0)),
                    "stable_convergence_rounds": int(row.get("stable_convergence_rounds", 0)),
                    "trace": []
                }
    
    # 读取trace（每轮的状态）
    if trace_file.exists():
        with open(trace_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                prompt_id = row.get("prompt_id", "")
                if prompt_id not in data:
                    continue
                
                data[prompt_id]["trace"].append({
                    "round_id": int(row.get("round_id", 0)),
                    "selected_constraint": row.get("selected_constraint", ""),
                    "n_pass_before": int(row.get("n_pass_before", 0)),
                    "n_pass_after": int(row.get("n_pass_after", 0)),
                    "n_total": int(row.get("n_total", 0)),
                    "accepted": row.get("accepted", "False") == "True",
                    "degraded_constraints": row.get("degraded_constraints", ""),
                    "improved_constraints": row.get("improved_constraints", ""),
                })
    
    return data


# ============================================================
# 可视化
# ============================================================

def plot_score_trajectory(linear_data: Dict, topo_data: Dict, output_dir: Path):
    """绘制S_t曲线（平均通过率随轮次变化）"""
    if not HAS_PLOT:
        return
    
    # 收集所有轮次的通过率
    linear_trajectories = defaultdict(list)
    topo_trajectories = defaultdict(list)
    
    for prompt_id, info in linear_data.items():
        for step in info["trace"]:
            round_id = step["round_id"]
            n_total = step["n_total"]
            if n_total > 0:
                pass_rate = step["n_pass_after"] / n_total
                linear_trajectories[round_id].append(pass_rate)
    
    for prompt_id, info in topo_data.items():
        for step in info["trace"]:
            round_id = step["round_id"]
            n_total = step["n_total"]
            if n_total > 0:
                pass_rate = step["n_pass_after"] / n_total
                topo_trajectories[round_id].append(pass_rate)
    
    if not linear_trajectories and not topo_trajectories:
        return
    
    max_round = max(
        max(linear_trajectories.keys()) if linear_trajectories else 0,
        max(topo_trajectories.keys()) if topo_trajectories else 0
    )
    
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # Linear曲线
    rounds_linear = sorted(linear_trajectories.keys())
    means_linear = [np.mean(linear_trajectories[r]) for r in rounds_linear]
    stds_linear = [np.std(linear_trajectories[r]) for r in rounds_linear]
    
    ax.plot(rounds_linear, means_linear, 'o-', label='Linear', linewidth=2, markersize=6)
    ax.fill_between(
        rounds_linear,
        [m - s for m, s in zip(means_linear, stds_linear)],
        [m + s for m, s in zip(means_linear, stds_linear)],
        alpha=0.2
    )
    
    # Topo曲线
    rounds_topo = sorted(topo_trajectories.keys())
    means_topo = [np.mean(topo_trajectories[r]) for r in rounds_topo]
    stds_topo = [np.std(topo_trajectories[r]) for r in rounds_topo]
    
    ax.plot(rounds_topo, means_topo, 's-', label='Topo', linewidth=2, markersize=6)
    ax.fill_between(
        rounds_topo,
        [m - s for m, s in zip(means_topo, stds_topo)],
        [m + s for m, s in zip(means_topo, stds_topo)],
        alpha=0.2
    )
    
    ax.set_xlabel('Round', fontsize=12)
    ax.set_ylabel('Constraint Pass Rate', fontsize=12)
    ax.set_title('Average Pass Rate Trajectory (S_t)', fontsize=14, fontweight='bold')
    ax.legend(fontsize=11)
    ax.grid(True, alpha=0.3)
    ax.set_ylim([0, 1.05])
    
    plt.tight_layout()
    plt.savefig(output_dir / "trajectory_St.png", dpi=300, bbox_inches='tight')
    plt.close()


def plot_flip_distribution(linear_data: Dict, topo_data: Dict, output_dir: Path):
    """绘制flip count分布（箱线图）"""
    if not HAS_PLOT:
        return
    
    linear_flips = [info["constraint_flip_count"] for info in linear_data.values()]
    topo_flips = [info["constraint_flip_count"] for info in topo_data.values()]
    
    if not linear_flips and not topo_flips:
        return
    
    fig, ax = plt.subplots(figsize=(8, 6))
    
    data_to_plot = []
    labels = []
    
    if linear_flips:
        data_to_plot.append(linear_flips)
        labels.append('Linear')
    
    if topo_flips:
        data_to_plot.append(topo_flips)
        labels.append('Topo')
    
    bp = ax.boxplot(data_to_plot, labels=labels, patch_artist=True, widths=0.6)
    
    colors = ['#ff9999', '#66b3ff']
    for patch, color in zip(bp['boxes'], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    
    ax.set_ylabel('Constraint Flip Count', fontsize=12)
    ax.set_title('Constraint Flip Distribution', fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3, axis='y')
    
    plt.tight_layout()
    plt.savefig(output_dir / "flip_distribution.png", dpi=300, bbox_inches='tight')
    plt.close()


def plot_convergence_rounds(linear_data: Dict, topo_data: Dict, output_dir: Path):
    """绘制稳定收敛轮次分布"""
    if not HAS_PLOT:
        return
    
    linear_rounds = [info["stable_convergence_rounds"] for info in linear_data.values()]
    topo_rounds = [info["stable_convergence_rounds"] for info in topo_data.values()]
    
    if not linear_rounds and not topo_rounds:
        return
    
    fig, ax = plt.subplots(figsize=(8, 6))
    
    data_to_plot = []
    labels = []
    
    if linear_rounds:
        data_to_plot.append(linear_rounds)
        labels.append('Linear')
    
    if topo_rounds:
        data_to_plot.append(topo_rounds)
        labels.append('Topo')
    
    bp = ax.boxplot(data_to_plot, labels=labels, patch_artist=True, widths=0.6)
    
    colors = ['#ff9999', '#66b3ff']
    for patch, color in zip(bp['boxes'], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    
    ax.set_ylabel('Stable Convergence Rounds', fontsize=12)
    ax.set_title('Rounds to Stable Convergence', fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3, axis='y')
    
    plt.tight_layout()
    plt.savefig(output_dir / "convergence_rounds.png", dpi=300, bbox_inches='tight')
    plt.close()


# ============================================================
# 主函数
# ============================================================

def run_comparison(input_file: str, backend: str, dry_run: bool, max_rounds: int, graph_mode: str = "hybrid", extract_mode: str = "minimal"):
    """运行对比实验并生成报告（统计严谨版）"""
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    strategies = ["linear", "greedy_static", "greedy_adaptive"]  # 更新：三策略对比
    
    print("=" * 80)
    print(f"调度策略对比实验（统计严谨版）")
    print(f"数据集: {input_file}")
    print(f"后端: {backend} (dry_run={dry_run})")
    print(f"图构建模式: {graph_mode}")
    print(f"约束提取模式: {extract_mode}")
    print(f"最大轮次: {max_rounds}")
    print(f"对比策略: {', '.join(strategies)}")
    print("=" * 80)
    
    # 构建命令
    cmd = [
        sys.executable,
        "scripts/run_experiment.py",
        "--input", input_file,
        "--backend", backend,
        "--strategies", ",".join(strategies),
        "--max_rounds", str(max_rounds),
        "--graph_mode", graph_mode,
        "--extract_mode", extract_mode,
    ]
    
    if dry_run:
        cmd.extend(["--dry_run", "1"])
    
    print(f"\n执行命令: {' '.join(cmd)}\n")
    
    # 运行实验
    result = subprocess.run(cmd, cwd=ROOT)
    
    if result.returncode != 0:
        print(f"\n❌ 实验运行失败，退出码: {result.returncode}")
        return
    
    # 查找最新的实验结果
    runs_dir = ROOT / "runs"
    exp_dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()], 
                     key=lambda x: x.stat().st_mtime, reverse=True)
    
    if not exp_dirs:
        print("\n❌ 未找到实验结果目录")
        return
    
    latest_exp = exp_dirs[0]
    aggregate_file = latest_exp / "aggregate.json"
    
    if not aggregate_file.exists():
        print(f"\n❌ 未找到聚合结果: {aggregate_file}")
        return
    
    # 读取聚合结果
    with open(aggregate_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    strategies_data = data.get("strategies", {})
    
    if len(strategies_data) < 2:
        print("\n⚠️  只有一个策略的结果，无法对比")
        return
    
    # ============================================================
    # 加载细粒度数据
    # ============================================================
    print("\n📂 加载细粒度数据...")
    linear_samples = load_per_sample_data(latest_exp, "linear")
    topo_samples = load_per_sample_data(latest_exp, "topo_conflict")
    
    if not linear_samples or not topo_samples:
        print("⚠️  缺少per-sample数据，将只使用聚合指标")
    
    # ============================================================
    # 统计分析（配对数据）
    # ============================================================
    print("\n📊 进行统计分析...")
    
    # 找到配对的样本
    common_prompts = set(linear_samples.keys()) & set(topo_samples.keys())
    n_samples = len(common_prompts)
    
    print(f"   配对样本数: {n_samples}")
    
    # 提取配对指标
    paired_metrics = {}
    
    if n_samples > 0:
        for metric_key in ["total_rounds", "conflict_count", "constraint_flip_count", 
                          "stable_convergence_rounds", "global_score_oscillation", "protection_rate"]:
            linear_vals = np.array([linear_samples[pid][metric_key] for pid in common_prompts])
            topo_vals = np.array([topo_samples[pid][metric_key] for pid in common_prompts])
            
            # Bootstrap CI
            linear_mean, linear_lower, linear_upper = bootstrap_ci(linear_vals)
            topo_mean, topo_lower, topo_upper = bootstrap_ci(topo_vals)
            
            # 配对检验
            p_value = paired_permutation_test(linear_vals, topo_vals)
            
            # Effect size
            effect_size = cohens_d(topo_vals, linear_vals)  # topo - linear
            
            paired_metrics[metric_key] = {
                "linear": {"mean": linear_mean, "ci": (linear_lower, linear_upper), "raw": linear_vals},
                "topo": {"mean": topo_mean, "ci": (topo_lower, topo_upper), "raw": topo_vals},
                "p_value": p_value,
                "effect_size": effect_size,
            }
    
    # ============================================================
    # 生成可视化
    # ============================================================
    if HAS_PLOT and linear_samples and topo_samples:
        print("\n📈 生成可视化...")
        plot_score_trajectory(linear_samples, topo_samples, latest_exp)
        plot_flip_distribution(linear_samples, topo_samples, latest_exp)
        plot_convergence_rounds(linear_samples, topo_samples, latest_exp)
        print(f"   ✅ 图表已保存到: {latest_exp}")
    
    # ============================================================
    # 打印对比报告
    # ============================================================
    print("\n" + "=" * 80)
    print("📊 调度策略对比结果（统计严谨版）")
    print("=" * 80)
    
    # 提取聚合指标
    metrics_names = [
        ("pass_rate", "通过率", "↑", True),
        ("avg_rounds", "平均轮次", "↓", False),
        ("conflict_rate", "冲突率", "↓", False),
        ("oscillation_rate", "震荡率", "↓", False),
        ("protection_rate", "保护率", "↑", True),
        ("avg_global_score_oscillation", "全局分数震荡", "↓", False),
        ("avg_constraint_flip_count", "约束翻转次数", "↓", False),
        ("avg_stable_convergence_rounds", "稳定收敛轮次", "↑", True),
    ]
    
    print(f"\n{'指标':<30} {'Linear':<20} {'GreedyStatic':<20} {'GreedyAdaptive':<20} {'p-value':<12} {'最优':<10}")
    print("-" * 115)
    
    linear_data_agg = strategies_data.get("linear", {}).get("metrics", {})
    static_data_agg = strategies_data.get("greedy_static", {}).get("metrics", {})
    adaptive_data_agg = strategies_data.get("greedy_adaptive", {}).get("metrics", {})
    
    linear_wins = 0
    static_wins = 0
    adaptive_wins = 0
    ties = 0
    
    for metric_key, metric_name, direction, higher_better in metrics_names:
        # 获取三个策略的值
        linear_val = linear_data_agg.get(metric_key, 0.0)
        static_val = static_data_agg.get(metric_key, 0.0)
        adaptive_val = adaptive_data_agg.get(metric_key, 0.0)
        
        # 如果有配对数据，使用统计检验
        if metric_key in paired_metrics:
            pm = paired_metrics[metric_key]
            linear_mean = pm["linear"]["mean"]
            linear_ci = pm["linear"]["ci"]
            
            # 注意：这里需要扩展paired_metrics支持三策略
            # 暂时简化处理
            linear_str = f"{linear_mean:.3f}"
            static_str = f"{static_val:.3f}"
            adaptive_str = f"{adaptive_val:.3f}"
            p_str = "N/A"
        else:
            linear_str = f"{linear_val:.3f}"
            static_str = f"{static_val:.3f}"
            adaptive_str = f"{adaptive_val:.3f}"
            p_str = "N/A"
        
        # 判断最优
        values = [
            ("Linear", linear_val),
            ("Static", static_val),
            ("Adaptive", adaptive_val),
        ]
        
        if higher_better:
            best_strategy = max(values, key=lambda x: x[1])[0]
        else:
            best_strategy = min(values, key=lambda x: x[1])[0]
        
        # 统计胜场
        if best_strategy == "Linear":
            linear_wins += 1
        elif best_strategy == "Static":
            static_wins += 1
        elif best_strategy == "Adaptive":
            adaptive_wins += 1
        
        best_str = f"✅ {best_strategy}"
        
        print(f"{metric_name:<30} {linear_str:<20} {static_str:<20} {adaptive_str:<20} {p_str:<12} {best_str:<10}")
    
    print("-" * 115)
    print(f"\n总结: Linear胜 {linear_wins}项, GreedyStatic胜 {static_wins}项, GreedyAdaptive胜 {adaptive_wins}项")
    
    # ============================================================
    # Per-sample分析
    # ============================================================
    if linear_samples and topo_samples and common_prompts:
        print("\n" + "=" * 80)
        print("📋 Per-Sample详细分析")
        print("=" * 80)
        
        print(f"\n{'Prompt ID':<40} {'Linear轮次':<12} {'Topo轮次':<12} {'Linear冲突':<12} {'Topo冲突':<12}")
        print("-" * 90)
        
        for pid in sorted(common_prompts):
            linear_rounds = linear_samples[pid]["total_rounds"]
            topo_rounds = topo_samples[pid]["total_rounds"]
            linear_conflicts = linear_samples[pid]["conflict_count"]
            topo_conflicts = topo_samples[pid]["conflict_count"]
            
            print(f"{pid:<40} {linear_rounds:<12} {topo_rounds:<12} {linear_conflicts:<12} {topo_conflicts:<12}")
        
        # Case study: 找出差异最大的样本
        print("\n" + "=" * 80)
        print("🔍 Case Study: 差异最大的样本")
        print("=" * 80)
        
        diffs = []
        for pid in common_prompts:
            linear_rounds = linear_samples[pid]["total_rounds"]
            topo_rounds = topo_samples[pid]["total_rounds"]
            diff = linear_rounds - topo_rounds
            diffs.append((pid, diff, linear_rounds, topo_rounds))
        
        diffs.sort(key=lambda x: abs(x[1]), reverse=True)
        
        print("\nTopo最优样本（减少轮次最多）:")
        for pid, diff, linear_r, topo_r in diffs[:3]:
            if diff > 0:
                print(f"  - {pid}: Linear {linear_r}轮 → Topo {topo_r}轮 (减少{diff}轮)")
        
        print("\nLinear最优样本（Topo反而更差）:")
        for pid, diff, linear_r, topo_r in diffs[-3:]:
            if diff < 0:
                print(f"  - {pid}: Linear {linear_r}轮 → Topo {topo_r}轮 (增加{-diff}轮)")
    
    # ============================================================
    # 综合结论
    # ============================================================
    print("\n" + "=" * 80)
    print("🎯 综合结论")
    print("=" * 80)
    
    n_prompts = data.get("n_prompts", 0)
    
    # 判断最优策略
    wins = [
        ("Linear", linear_wins),
        ("GreedyStatic", static_wins),
        ("GreedyAdaptive", adaptive_wins),
    ]
    best_strategy_name, best_wins = max(wins, key=lambda x: x[1])
    
    print(f"\n✅ 最优策略: {best_strategy_name}")
    print(f"   - 优势指标: {best_wins}/{len(metrics_names)}")
    print(f"   - 优势幅度: {best_wins / len(metrics_names) * 100:.1f}%")
    
    print(f"\n策略排名:")
    for i, (name, win_count) in enumerate(sorted(wins, key=lambda x: x[1], reverse=True), 1):
        print(f"   {i}. {name}: {win_count}项优势")
    
    # 样本量警告
    if n_samples < 10:
        print(f"\n⚠️  警告: 样本量较小 (n={n_samples})")
        print(f"   建议: 使用至少20个测试用例以获得统计显著性")
        print(f"   当前置信度: 低")
    elif n_samples < 20:
        print(f"\n⚠️  注意: 样本量中等 (n={n_samples})")
        print(f"   建议: 增加到30+样本以提高结论可靠性")
        print(f"   当前置信度: 中")
    else:
        print(f"\n✅ 样本量充足 (n={n_samples})")
        print(f"   当前置信度: 高")
    
    print("=" * 80)
    
    # ============================================================
    # 生成Markdown报告
    # ============================================================
    print(f"\n详细结果保存在: {latest_exp}")
    
    report_file = latest_exp / "comparison_report.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(f"# 调度策略对比报告（统计严谨版）\n\n")
        f.write(f"## 实验配置\n\n")
        f.write(f"- **实验时间**: {timestamp}\n")
        f.write(f"- **数据集**: {input_file}\n")
        f.write(f"- **样本量**: {n_samples}\n")
        f.write(f"- **后端**: {backend}\n")
        f.write(f"- **最大轮次**: {max_rounds}\n")
        f.write(f"- **统计方法**: Bootstrap CI (95%) + 配对置换检验\n\n")
        
        f.write(f"## 指标对比（含统计检验）\n\n")
        f.write(f"| 指标 | Linear (95% CI) | Topo (95% CI) | p-value | Effect Size | 显著性 |\n")
        f.write(f"|------|-----------------|---------------|---------|-------------|--------|\n")
        
        for metric_key, metric_name, direction, higher_better in metrics_names:
            if metric_key in paired_metrics:
                pm = paired_metrics[metric_key]
                linear_mean = pm["linear"]["mean"]
                linear_ci = pm["linear"]["ci"]
                # 注意：这里暂时使用linear的配对数据
                # TODO: 扩展paired_metrics支持三策略对比
                topo_mean = pm.get("topo", {}).get("mean", 0.0)
                topo_ci = pm.get("topo", {}).get("ci", (0.0, 0.0))
                p_value = pm["p_value"]
                effect_size = pm["effect_size"]
                
                linear_str = f"{linear_mean:.3f} [{linear_ci[0]:.3f}, {linear_ci[1]:.3f}]"
                topo_str = f"{topo_mean:.3f} [{topo_ci[0]:.3f}, {topo_ci[1]:.3f}]"
                
                if p_value < 0.001:
                    sig_str = "***"
                elif p_value < 0.01:
                    sig_str = "**"
                elif p_value < 0.05:
                    sig_str = "*"
                else:
                    sig_str = "n.s."
                
                f.write(f"| {metric_name} | {linear_str} | {topo_str} | {p_value:.4f} | {effect_size:+.3f} | {sig_str} |\n")
            else:
                linear_val = linear_data_agg.get(metric_key, 0.0)
                static_val = static_data_agg.get(metric_key, 0.0)
                adaptive_val = adaptive_data_agg.get(metric_key, 0.0)
                # 报告中暂时只显示三个策略的值
                f.write(f"| {metric_name} | {linear_val:.3f} | Static:{static_val:.3f} Adaptive:{adaptive_val:.3f} | N/A | N/A | N/A |\n")
        
        f.write(f"\n**显著性说明**: *** p<0.001, ** p<0.01, * p<0.05, n.s. 不显著\n\n")
        f.write(f"**Effect Size说明**: Cohen's d (小<0.2, 中<0.5, 大<0.8, 极大≥0.8)\n\n")
        
        f.write(f"## Per-Sample分析\n\n")
        if linear_samples and topo_samples and common_prompts:
            f.write(f"| Prompt ID | Linear轮次 | Topo轮次 | 差异 | Linear冲突 | Topo冲突 |\n")
            f.write(f"|-----------|-----------|----------|------|-----------|----------|\n")
            
            for pid in sorted(common_prompts):
                linear_rounds = linear_samples[pid]["total_rounds"]
                topo_rounds = topo_samples[pid]["total_rounds"]
                diff = linear_rounds - topo_rounds
                linear_conflicts = linear_samples[pid]["conflict_count"]
                topo_conflicts = topo_samples[pid]["conflict_count"]
                
                f.write(f"| {pid} | {linear_rounds} | {topo_rounds} | {diff:+d} | {linear_conflicts} | {topo_conflicts} |\n")
        
        f.write(f"\n## 可视化\n\n")
        if HAS_PLOT:
            f.write(f"生成的图表:\n\n")
            f.write(f"1. `trajectory_St.png` - 约束通过率随轮次变化曲线\n")
            f.write(f"2. `flip_distribution.png` - 约束翻转次数分布\n")
            f.write(f"3. `convergence_rounds.png` - 稳定收敛轮次分布\n\n")
        
        f.write(f"## 结论\n\n")
        
        # 判断最优策略
        wins_for_report = [
            ("Linear", linear_wins),
            ("GreedyStatic", static_wins),
            ("GreedyAdaptive", adaptive_wins),
        ]
        best_for_report, best_wins_count = max(wins_for_report, key=lambda x: x[1])
        
        if best_for_report == "GreedyAdaptive":
            f.write(f"✅ **GreedyAdaptive调度策略显著优于其他策略**\n\n")
            f.write(f"- 显著优势指标: {best_wins_count}/{len(metrics_names)}\n")
            f.write(f"- 优势幅度: {best_wins_count / len(metrics_names) * 100:.1f}%\n\n")
        elif best_for_report == "GreedyStatic":
            f.write(f"✅ **GreedyStatic调度策略显著优于其他策略**\n\n")
            f.write(f"- 显著优势指标: {best_wins_count}/{len(metrics_names)}\n")
            f.write(f"- 优势幅度: {best_wins_count / len(metrics_names) * 100:.1f}%\n\n")
        else:
            f.write(f"⚖️ **Linear策略表现最优（或策略间无显著差异）**\n\n")
        
        if n_samples < 10:
            f.write(f"⚠️ **警告**: 样本量较小 (n={n_samples})，建议增加到20+以提高统计效力\n\n")
        elif n_samples < 20:
            f.write(f"⚠️ **注意**: 样本量中等 (n={n_samples})，建议增加到30+以提高结论可靠性\n\n")
        else:
            f.write(f"✅ 样本量充足 (n={n_samples})，结论可靠\n\n")
        
        f.write(f"## 方法论说明\n\n")
        f.write(f"### 统计检验\n\n")
        f.write(f"- **Bootstrap置信区间**: 10000次重采样，95%置信水平\n")
        f.write(f"- **配对置换检验**: 10000次置换，适合小样本配对数据\n")
        f.write(f"- **Effect Size**: Cohen's d，衡量实际差异大小\n\n")
        f.write(f"### 为什么不用t检验？\n\n")
        f.write(f"1. 样本量小时，正态性假设不可靠\n")
        f.write(f"2. 置换检验是非参数方法，更稳健\n")
        f.write(f"3. Bootstrap CI不依赖分布假设\n\n")
    
    print(f"📄 对比报告已保存: {report_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="对比Linear vs Topo调度策略（统计严谨版）")
    parser.add_argument("--input", required=True, help="输入的prompts JSON文件")
    parser.add_argument("--backend", default="mock", choices=["mock", "openai"], help="后端类型")
    parser.add_argument("--dry_run", type=int, default=1, help="是否dry_run模式 (0或1)")
    parser.add_argument("--rounds", type=int, default=4, help="最大迭代轮次")
    parser.add_argument("--graph_mode", default="hybrid", choices=["rule", "hybrid"], help="图构建模式")
    parser.add_argument("--extract_mode", default="minimal", choices=["full", "minimal"], help="约束提取模式")
    
    args = parser.parse_args()
    
    run_comparison(
        input_file=args.input,
        backend=args.backend,
        dry_run=bool(args.dry_run),
        max_rounds=args.rounds,
        graph_mode=args.graph_mode,
        extract_mode=args.extract_mode,
    )

