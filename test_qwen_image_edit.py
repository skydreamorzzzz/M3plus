# -*- coding: utf-8 -*-
"""
测试脚本：验证 qwen-image-edit 修复

使用方法：
    python test_qwen_image_edit.py <input_image_path> <instruction>

示例：
    python test_qwen_image_edit.py test_input.png "将图片改为黑白风格"
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.config.model_config import get_image_edit_client


def test_image_edit(input_image: str, instruction: str):
    """
    测试图像编辑功能
    
    Args:
        input_image: 输入图片路径
        instruction: 编辑指令
    """
    print("=" * 60)
    print("Qwen Image Edit 测试")
    print("=" * 60)
    
    # 检查输入文件
    input_path = Path(input_image)
    if not input_path.exists():
        print(f"❌ 错误：输入图片不存在: {input_path}")
        return False
    
    print(f"📥 输入图片: {input_path}")
    print(f"📝 编辑指令: {instruction}")
    print()
    
    # 创建输出目录
    output_dir = Path("test_output")
    output_dir.mkdir(exist_ok=True)
    
    # 生成输出文件名
    output_path = output_dir / f"edited_{input_path.name}"
    
    # 如果输出文件已存在，删除它以测试实际请求
    if output_path.exists():
        print(f"🗑️  删除已存在的输出文件: {output_path}")
        output_path.unlink()
    
    try:
        # 获取客户端
        print("🔧 初始化 Image Edit 客户端...")
        client = get_image_edit_client()
        print(f"   模型: {client.model}")
        print(f"   Base URL: {client.base_url}")
        print()
        
        # 执行编辑
        print("🚀 开始图像编辑...")
        result_path = client.edit(
            image_path=input_path,
            instruction=instruction,
            out_path=output_path
        )
        
        print()
        print("=" * 60)
        print("✅ 测试成功！")
        print("=" * 60)
        print(f"📤 输出图片: {result_path}")
        print(f"📊 文件大小: {result_path.stat().st_size / 1024:.2f} KB")
        print()
        
        return True
        
    except Exception as e:
        print()
        print("=" * 60)
        print("❌ 测试失败！")
        print("=" * 60)
        print(f"错误类型: {type(e).__name__}")
        print(f"错误信息: {e}")
        print()
        
        import traceback
        print("详细错误堆栈：")
        traceback.print_exc()
        
        return False


def main():
    if len(sys.argv) < 3:
        print("使用方法：")
        print(f"  python {sys.argv[0]} <input_image_path> <instruction>")
        print()
        print("示例：")
        print(f"  python {sys.argv[0]} test_input.png \"将图片改为黑白风格\"")
        print(f"  python {sys.argv[0]} photo.jpg \"添加一只猫\"")
        sys.exit(1)
    
    input_image = sys.argv[1]
    instruction = " ".join(sys.argv[2:])
    
    # 检查 API 密钥
    if not os.getenv("DASHSCOPE_API_KEY"):
        print("❌ 错误：未设置 DASHSCOPE_API_KEY 环境变量")
        print("请先设置：")
        print("  export DASHSCOPE_API_KEY=your_api_key")
        sys.exit(1)
    
    success = test_image_edit(input_image, instruction)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

