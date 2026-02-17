# Qwen Image Edit 快速开始指南

## 修复已完成 ✅

qwen-image-edit 的 404 问题已修复！现在可以正常使用图像编辑功能。

## 快速测试步骤

### 1. 设置 API 密钥

```bash
# Windows PowerShell
$env:DASHSCOPE_API_KEY="your_api_key_here"

# Windows CMD
set DASHSCOPE_API_KEY=your_api_key_here

# Linux/Mac
export DASHSCOPE_API_KEY=your_api_key_here
```

### 2. 创建测试图片（可选）

如果没有测试图片，可以运行：

```bash
python create_test_image.py
```

这会创建一个 `test_input.png` 文件，包含简单的风景图（太阳、云、树）。

### 3. 运行测试

```bash
python test_qwen_image_edit.py test_input.png "将图片改为黑白风格"
```

### 4. 查看结果

编辑后的图片会保存在 `test_output/edited_test_input.png`

## 测试示例

### 示例 1：黑白风格
```bash
python test_qwen_image_edit.py test_input.png "将图片改为黑白风格"
```

### 示例 2：卡通风格
```bash
python test_qwen_image_edit.py test_input.png "将图片转换为卡通风格"
```

### 示例 3：添加元素
```bash
python test_qwen_image_edit.py test_input.png "在天空中添加一只飞鸟"
```

### 示例 4：修改颜色
```bash
python test_qwen_image_edit.py test_input.png "将天空改为夕阳的橙红色"
```

### 示例 5：艺术风格
```bash
python test_qwen_image_edit.py test_input.png "将图片改为梵高星空的风格"
```

## 预期输出

成功运行时，你会看到类似以下的输出：

```
============================================================
Qwen Image Edit 测试
============================================================
📥 输入图片: test_input.png
📝 编辑指令: 将图片改为黑白风格

🔧 初始化 Image Edit 客户端...
   模型: qwen-image-edit
   Base URL: https://dashscope.aliyuncs.com

🚀 开始图像编辑...
[QWEN-IMAGE-EDIT] Preparing image for editing...
[QWEN-IMAGE-EDIT] Input: test_input.png
[QWEN-IMAGE-EDIT] Requesting edit via native API...
[QWEN-IMAGE-EDIT] Downloading edited image from: https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/...
[QWEN-IMAGE-EDIT] Edit completed: test_output\edited_test_input.png

============================================================
✅ 测试成功！
============================================================
📤 输出图片: test_output\edited_test_input.png
📊 文件大小: 234.56 KB
```

## 在项目中使用

修复后，你可以在项目中正常使用图像编辑功能：

```python
from pathlib import Path
from src.config.model_config import get_image_edit_client

# 获取客户端
client = get_image_edit_client()

# 编辑图片
result = client.edit(
    image_path=Path("input.png"),
    instruction="将图片改为黑白风格",
    out_path=Path("output/edited.png")
)

print(f"编辑完成: {result}")
```

## 支持的模型

- `qwen-image-edit` （默认）
- `qwen-image-edit-plus`
- `qwen-image-edit-max`

可以在 `src/config/model_config.py` 中修改模型：

```python
IMAGE_EDIT_LLM = ModelConfig(
    provider="qwen",
    model="qwen-image-edit-plus",  # 修改这里
    base_url="https://dashscope.aliyuncs.com",
    ...
)
```

## 常见问题

### Q: 出现 401 错误
A: 检查 DASHSCOPE_API_KEY 是否正确设置

### Q: 出现 404 错误
A: 确保已应用本次修复，检查 `src/config/model_config.py` 中的 `base_url` 是否为 `https://dashscope.aliyuncs.com`（不包含 `/compatible-mode/v1`）

### Q: 图片太大
A: base64 编码会增加约 33% 的数据量，建议输入图片不超过 4MB

### Q: 编辑效果不理想
A: 尝试：
- 使用更详细的指令
- 切换到 `qwen-image-edit-plus` 或 `qwen-image-edit-max` 模型
- 调整图片尺寸和质量

## 技术支持

如有问题，请查看：
- `QWEN_IMAGE_EDIT_FIX.md` - 详细修复说明
- `src/llm/wanx_client.py` - 核心实现代码
- DashScope 官方文档：https://help.aliyun.com/zh/dashscope/

## 修复内容摘要

✅ 移除了 compatible-mode 路径  
✅ 使用 DashScope 原生 multimodal-generation 接口  
✅ 实现图片 base64 编码  
✅ 正确解析响应格式  
✅ 保持向后兼容  
✅ 创建测试脚本  

---

**修复完成时间**: 2026-02-17  
**修复版本**: v1.0

