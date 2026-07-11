"""唯一样例登记表:评测/再生所需的全部路径与元数据,只此一处(设计 §8.2)。

10 例统一结构(样例数据/<key>/):
  初始文件.docx   源文件(内容唯一权威)
  结构参考.xml    评测对照物(A+B、无 C;人工核定冻结)
  上线版本.xml    出版方发表版(仅 01-05;house-style/抽全核对/B 旁证,非评测目标)
  scope.json      已知编辑加工(C 档)目录等审计信息
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
    group: str      # main = 01-05(委员会主样例,有上线版本);supp = S01-05(补充,仅 docx)
    journal: str
    doi: str

    @property
    def article_id(self) -> str:
        return self.doi.split("/")[-1]

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
    def scope_json(self):
        p = os.path.join(self.dir, "scope.json")
        return p if os.path.exists(p) else None


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
]
BY_KEY = {s.key: s for s in SAMPLES}


def get(key: str) -> Sample:
    return BY_KEY[key]
