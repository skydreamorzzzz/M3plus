# -*- coding: utf-8 -*-
"""
验证 qwen-image-edit 修复是否正确应用

此脚本检查：
1. 配置文件中的 base_url 是否正确
2. wanx_client.py 中的关键方法是否存在
3. 必要的导入是否正确
"""

import sys
import io
from pathlib import Path

# 设置 stdout 为 UTF-8 编码（Windows 兼容）
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def check_config():
    """检查配置文件"""
    print("=" * 60)
    print("1. 检查配置文件")
    print("=" * 60)
    
    try:
        from src.config.model_config import IMAGE_EDIT_LLM
        
        print(f"✓ 模型: {IMAGE_EDIT_LLM.model}")
        print(f"✓ Base URL: {IMAGE_EDIT_LLM.base_url}")
        
        # 检查 base_url 是否正确
        if "compatible-mode" in IMAGE_EDIT_LLM.base_url:
            print("❌ 错误：base_url 仍包含 'compatible-mode'")
            print(f"   当前值: {IMAGE_EDIT_LLM.base_url}")
            print(f"   应该是: https://dashscope.aliyuncs.com")
            return False
        
        if IMAGE_EDIT_LLM.base_url.rstrip("/") != "https://dashscope.aliyuncs.com":
            print("⚠️  警告：base_url 不是标准值")
            print(f"   当前值: {IMAGE_EDIT_LLM.base_url}")
            print(f"   建议值: https://dashscope.aliyuncs.com")
        
        print("✅ 配置文件检查通过")
        return True
        
    except Exception as e:
        print(f"❌ 配置文件检查失败: {e}")
        return False


def check_wanx_client():
    """检查 wanx_client.py 实现"""
    print()
    print("=" * 60)
    print("2. 检查 WanxImageEditClient 实现")
    print("=" * 60)
    
    try:
        from src.llm.wanx_client import WanxImageEditClient
        
        # 检查方法是否存在
        methods_to_check = [
            'edit',
            '_edit_via_compatible_mode',
            '_edit_via_legacy_wanx',
            '_image_to_data_url',
        ]
        
        for method_name in methods_to_check:
            if hasattr(WanxImageEditClient, method_name):
                print(f"✓ 方法存在: {method_name}")
            else:
                print(f"❌ 方法缺失: {method_name}")
                return False
        
        # 检查 _image_to_data_url 方法签名
        import inspect
        sig = inspect.signature(WanxImageEditClient._image_to_data_url)
        params = list(sig.parameters.keys())
        
        if 'self' in params and 'image_path' in params:
            print(f"✓ _image_to_data_url 签名正确")
        else:
            print(f"❌ _image_to_data_url 签名错误: {params}")
            return False
        
        # 检查方法实现（简单检查源码）
        source = inspect.getsource(WanxImageEditClient._edit_via_compatible_mode)
        
        checks = [
            ("multimodal-generation", "使用原生 API 端点"),
            ("_image_to_data_url", "调用图片转换方法"),
            ("choices", "解析响应格式"),
            ("image_url", "提取图片 URL"),
        ]
        
        for keyword, description in checks:
            if keyword in source:
                print(f"✓ 实现包含: {description}")
            else:
                print(f"❌ 实现缺失: {description}")
                return False
        
        print("✅ WanxImageEditClient 检查通过")
        return True
        
    except Exception as e:
        print(f"❌ WanxImageEditClient 检查失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_imports():
    """检查必要的导入"""
    print()
    print("=" * 60)
    print("3. 检查必要的导入")
    print("=" * 60)
    
    try:
        import base64
        print("✓ base64")
        
        import mimetypes
        print("✓ mimetypes")
        
        import requests
        print("✓ requests")
        
        from pathlib import Path
        print("✓ pathlib.Path")
        
        print("✅ 导入检查通过")
        return True
        
    except Exception as e:
        print(f"❌ 导入检查失败: {e}")
        return False


def check_test_files():
    """检查测试文件是否存在"""
    print()
    print("=" * 60)
    print("4. 检查测试文件")
    print("=" * 60)
    
    files_to_check = [
        "test_qwen_image_edit.py",
        "create_test_image.py",
        "QWEN_IMAGE_EDIT_FIX.md",
        "QUICK_START.md",
        "CHANGES_SUMMARY.md",
    ]
    
    all_exist = True
    for filename in files_to_check:
        filepath = Path(filename)
        if filepath.exists():
            print(f"✓ {filename}")
        else:
            print(f"❌ 缺失: {filename}")
            all_exist = False
    
    if all_exist:
        print("✅ 测试文件检查通过")
    else:
        print("⚠️  部分测试文件缺失")
    
    return all_exist


def main():
    print("Qwen Image Edit 修复验证")
    print()
    
    results = []
    
    # 运行所有检查
    results.append(("配置文件", check_config()))
    results.append(("WanxImageEditClient", check_wanx_client()))
    results.append(("导入", check_imports()))
    results.append(("测试文件", check_test_files()))
    
    # 总结
    print()
    print("=" * 60)
    print("验证总结")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print("🎉 所有检查通过！修复已正确应用。")
        print()
        print("下一步：")
        print("1. 设置 API 密钥: export DASHSCOPE_API_KEY=your_key")
        print("2. 创建测试图片: python create_test_image.py")
        print("3. 运行测试: python test_qwen_image_edit.py test_input.png \"将图片改为黑白风格\"")
        return 0
    else:
        print("❌ 部分检查失败，请检查修复是否完整应用。")
        return 1


if __name__ == "__main__":
    sys.exit(main())

