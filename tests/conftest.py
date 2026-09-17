import os
import sys

# 让测试无需安装即可导入源码和独立评测工具。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

SAMPLES = os.path.join(ROOT, "样例数据")
