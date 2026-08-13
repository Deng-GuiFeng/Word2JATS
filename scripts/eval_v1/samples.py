"""V1 的样例登记:从 `样例数据/样例登记.json` 读取唯一名单,构建 V1 自己的数据类。

**名单是数据、不是代码**,故只存一份(那个 JSON),两套评测器各自读、各自建模型。
V1 与 V2 的判分逻辑保持完全独立、互不引用——同时用两把尺子的全部价值就在于此;
但样例名单再各存一份就纯属隐患:增删样例改漏一边,两器就在评不是同一批东西。

每个样例目录 `样例数据/<key>/` 的布局:
  初始文件.docx   源文件(内容唯一权威)
  结构参考.xml    金标准的 XML 部分(A+B、无 C;人工核定冻结)
  figures.zip     金标准的图片部分(字节逐字取自 docx 内嵌媒体)
  上线版本.xml    出版方发表版(仅 01-05;house-style 旁证,非评测目标)
"""
from dataclasses import dataclass
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(ROOT, "样例数据")
REGISTRY = os.path.join(DATA, "样例登记.json")


@dataclass(frozen=True)
class Sample:
    key: str
    group: str      # main=有上线版本 / supp=仅 docx(held-out) / external=外部投稿件
    journal: str
    doi: str        # external 组为空串:投稿件尚未发表,无 DOI

    @property
    def article_id(self) -> str:
        """外部化图片目录名。无 DOI 时用 刊号-序号(如 RN-X01),仍唯一且可读。"""
        if self.doi:
            return self.doi.split("/")[-1]
        return "%s-%s" % (self.journal, self.key)

    @property
    def dir(self) -> str:
        return os.path.join(DATA, self.key)

    @property
    def docx(self) -> str:
        return os.path.join(self.dir, "初始文件.docx")

    @property
    def ref_xml(self) -> str:
        return os.path.join(self.dir, "结构参考.xml")

    @property
    def figures_zip(self) -> str:
        """金标准的图片包:参考里每个 graphic 指向的文件,字节取自 docx 内嵌媒体。"""
        return os.path.join(self.dir, "figures.zip")


def _load():
    with open(REGISTRY, encoding="utf-8") as f:
        raw = json.load(f)
    return [Sample(s["key"], s["group"], s["journal"], s["doi"]) for s in raw["样例"]]


SAMPLES = _load()
BY_KEY = {s.key: s for s in SAMPLES}

# 成绩口径:委员会给的 10 例。external 组不入成绩(本队自取、金标准也由本队建,与转换器
# 同源,拿来报成绩会自证);它只用于评测器自身的回归与盲区探测。理由见登记表里的说明。
EVAL_SET = [s for s in SAMPLES if s.group in ("main", "supp")]


def get(key: str) -> Sample:
    return BY_KEY[key]
