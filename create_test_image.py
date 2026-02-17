# -*- coding: utf-8 -*-
"""
创建一个简单的测试图片

使用方法：
    python create_test_image.py [output_path]

默认输出：test_input.png
"""

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("❌ 需要安装 Pillow 库")
    print("运行：pip install Pillow")
    sys.exit(1)


def create_test_image(output_path: str = "test_input.png"):
    """创建一个简单的测试图片"""
    
    # 创建一个 512x512 的图片，背景为浅蓝色
    width, height = 512, 512
    img = Image.new('RGB', (width, height), color=(135, 206, 235))  # 天蓝色
    
    draw = ImageDraw.Draw(img)
    
    # 画一个太阳
    sun_center = (128, 128)
    sun_radius = 50
    draw.ellipse(
        [sun_center[0] - sun_radius, sun_center[1] - sun_radius,
         sun_center[0] + sun_radius, sun_center[1] + sun_radius],
        fill=(255, 255, 0),  # 黄色
        outline=(255, 200, 0),
        width=3
    )
    
    # 画一些云
    cloud_positions = [(300, 100), (400, 150), (200, 180)]
    for cx, cy in cloud_positions:
        for dx, dy, r in [(0, 0, 25), (-20, 5, 20), (20, 5, 20)]:
            draw.ellipse(
                [cx + dx - r, cy + dy - r, cx + dx + r, cy + dy + r],
                fill=(255, 255, 255),  # 白色
            )
    
    # 画地面（绿色）
    draw.rectangle(
        [0, height - 150, width, height],
        fill=(34, 139, 34)  # 森林绿
    )
    
    # 画一棵树
    tree_x, tree_base = 256, height - 150
    # 树干
    draw.rectangle(
        [tree_x - 15, tree_base - 80, tree_x + 15, tree_base],
        fill=(139, 69, 19)  # 棕色
    )
    # 树冠
    for dy, r in [(0, 50), (-30, 45), (-60, 40)]:
        draw.ellipse(
            [tree_x - r, tree_base - 100 + dy - r,
             tree_x + r, tree_base - 100 + dy + r],
            fill=(0, 128, 0)  # 绿色
        )
    
    # 添加文字
    try:
        # 尝试使用默认字体
        font = ImageFont.truetype("arial.ttf", 36)
    except:
        # 如果找不到，使用默认字体
        font = ImageFont.load_default()
    
    text = "Test Image"
    # 获取文字边界框
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    text_x = (width - text_width) // 2
    text_y = height - 50
    
    # 绘制文字阴影
    draw.text((text_x + 2, text_y + 2), text, fill=(0, 0, 0), font=font)
    # 绘制文字
    draw.text((text_x, text_y), text, fill=(255, 255, 255), font=font)
    
    # 保存图片
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    img.save(output)
    
    print(f"✅ 测试图片已创建: {output}")
    print(f"📊 尺寸: {width}x{height}")
    print(f"📁 大小: {output.stat().st_size / 1024:.2f} KB")
    print()
    print("现在可以运行测试：")
    print(f"  python test_qwen_image_edit.py {output} \"将图片改为黑白风格\"")


def main():
    output_path = sys.argv[1] if len(sys.argv) > 1 else "test_input.png"
    create_test_image(output_path)


if __name__ == "__main__":
    main()

