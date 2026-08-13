"""word2jats 第二代补充评测器。

V2 不调用转换器，也不依赖 V1。它只读取：

* 初始文件.docx；
* 金标准结构参考.xml + figures.zip；
* 待评候选输出目录。

公开入口是 :func:`eval_v2.engine.evaluate_sample`。
"""

from .engine import evaluate_sample
from .samples import ALL_SAMPLES, Sample, get_sample

__all__ = ["ALL_SAMPLES", "Sample", "evaluate_sample", "get_sample"]
__version__ = "2.0.0"
