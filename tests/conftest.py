import os
import sys

# 让测试无需安装即可导入：src 下的 word2jats、项目根下的 scripts.eval_v1 / scripts.eval_v2。
# 两套评测器现在都以 `scripts.<包名>` 的形式导入，调用约定统一（见 scripts/__init__.py）。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

SAMPLES = os.path.join(ROOT, "样例数据")
