"""最小复现：独立公式组装是否保留同段解释文字。退出码 1 表示缺陷仍存在。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent.parent/'tmp/web-release-r02/src'))

from word2jats.model.source import SourceDocument, SourceNode, ObjectOccurrence, ObjectAnchor
from word2jats.understand.assemble import _Assembler
from word2jats.understand.merge import Assignment, DocumentAssignment
from word2jats.semantic import model as sm


def main():
    source = SourceDocument(
        nodes=[SourceNode('doc/p1','document','para',None,0,
                          text='Before \ufffc after.',objects=[ObjectAnchor(7,'o1')])],
        occurrences=[ObjectOccurrence('o1','image','doc/p1',7)],
    )
    assignment = DocumentAssignment((
        Assignment('node','doc/p1','body-paragraph',()),
        Assignment('object','o1','display-formula',()),
    ),(),())
    assembler = _Assembler(source,None,{},
        {'formulas':[{'occurrence_id':'o1','display':True}]},(),[],assignment)
    blocks,_ = assembler._body()
    paragraphs = [b for b in blocks if isinstance(b,sm.Paragraph)]
    print({'source':'Before [formula] after.',
           'actual_blocks':[type(b).__name__ for b in blocks],
           'surrounding_text_preserved':bool(paragraphs)})
    return 0 if paragraphs else 1


if __name__ == '__main__':
    raise SystemExit(main())
