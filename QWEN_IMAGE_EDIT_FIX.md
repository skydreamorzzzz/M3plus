# Qwen Image Edit 修复说明

## 问题
- 仅走单一路由时，在不同环境会出现 404/路由不匹配，导致图片编辑 agent 调用失败。

## 修复
- `qwen-image-edit*` 先走 OpenAI 兼容接口：`/compatible-mode/v1/images/edits`。
- 若兼容接口失败，自动回退到原生接口：`/api/v1/services/aigc/multimodal-generation/generation`。
- 保留 legacy wanx 的历史逻辑，避免影响其他模型。

## 影响文件
- `src/llm/wanx_client.py`

## 验证
- 运行 `python verify_fix.py`（结构检查）
- 配置 `DASHSCOPE_API_KEY` 后运行真实调用测试。
