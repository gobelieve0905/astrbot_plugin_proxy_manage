#!/usr/bin/env python3
"""重建index.html：保留完整HTML结构，内联CSS文件"""

# 读取旧版本HTML作为模板
with open('/tmp/old_index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 读取三个CSS文件
with open('style.css', 'r', encoding='utf-8') as f:
    style_css = f.read()

with open('health.css', 'r', encoding='utf-8') as f:
    health_css = f.read()

with open('download.css', 'r', encoding='utf-8') as f:
    download_css = f.read()

# 替换CSS链接为内联样式
# 找到三个link标签并替换为一个style标签
import re

# 移除所有CSS link标签
html = re.sub(r'  <link rel="stylesheet" href="\./(style|health|download)\.css">\n', '', html)

# 在</head>之前插入内联CSS
inline_style = f'''  <style>
{style_css}

{health_css}

{download_css}
  </style>
</head>'''

html = html.replace('</head>', inline_style)

# 更新script标签，移除type="module"
html = html.replace('<script type="module" src="./app.js"></script>', '<script src="app.js"></script>')

# 写入新的index.html
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("✓ index.html已重建，保留完整HTML结构并内联所有CSS")
print(f"  总大小: {len(html)} 字节")
