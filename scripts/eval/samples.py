"""唯一样例登记表:评测/再生所需的全部路径与元数据,只此一处(设计 §8.2)。

14 例统一结构(样例数据/<key>/):
  初始文件.docx   源文件(内容唯一权威)
  结构参考.xml    金标准的 XML 部分(A+B、无 C;人工核定冻结)
  figures.zip     金标准的图片部分(逐字节取自 docx 内嵌媒体)
  上线版本.xml    出版方发表版(仅 01-05;house-style/抽全核对/B 旁证,非评测目标)

三组:
  main 01-05    委员会主样例,有上线版本
  supp S01-S05  委员会补充样例,仅 docx(held-out)
  ext  X01-X04  外部投稿件,本队自取,无 DOI(投稿件尚未发表)

**SAMPLES 是全集(14 例),EVAL_SET 是成绩口径(10 例)。** 评测器自身的回归测试跑全集——
X 组的形态(无编号参考文献、图文摘要、公式退化为图片、Article 号)是评测盲区的探针,
2026-08 实测正是它们暴露了参考文献空键塌缩与三个未覆盖元素;成绩仍按 10 例报,口径不变。
刊号/DOI 均经官网核实,见 样例数据/说明.md。
"""
from dataclasses import dataclass
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(ROOT, "样例数据")


@dataclass(frozen=True)
class Sample:
    key: str
    group: str      # main = 01-05(有上线版本) / supp = S01-05(仅 docx) / ext = X01-04(外部投稿件)
    journal: str
    doi: str        # ext 组为空串:投稿件尚未发表,无 DOI

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


SAMPLES = [
    Sample("01", "main", "RCM", "10.31083/RCM46777"),
    # 02 上线版本 article-id 误带 zr-sly 草稿后缀,登记干净值(见 样例数据/说明.md)
    Sample("02", "main", "RCM", "10.31083/RCM46175"),
    Sample("03", "main", "JIN", "10.31083/JIN49347"),
    Sample("04", "main", "JIN", "10.31083/JIN52316"),
    Sample("05", "main", "HSF", "10.31083/HSF49106"),
    Sample("S01", "supp", "CEOG", "10.31083/CEOG48513"),
    Sample("S02", "supp", "CEOG", "10.31083/CEOG51386"),
    Sample("S03", "supp", "CEOG", "10.31083/CEOG50327"),
    Sample("S04", "supp", "RCM", "10.31083/RCM49717"),
    Sample("S05", "supp", "RCM", "10.31083/RCM49651"),
    Sample("X01", "ext", "RN", ""),
    Sample("X02", "ext", "DJNB", ""),
    Sample("X03", "ext", "BP", ""),
    Sample("X04", "ext", "DJNB", ""),
]
BY_KEY = {s.key: s for s in SAMPLES}

# 成绩口径:委员会给的 10 例。ext 组不入成绩(本队自取、金标准也由本队建,与转换器同源,
# 拿来报成绩会自证;它只用于评测器自身的回归与盲区探测)。
EVAL_SET = [s for s in SAMPLES if s.group in ("main", "supp")]


def get(key: str) -> Sample:
    return BY_KEY[key]
