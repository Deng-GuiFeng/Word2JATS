"""生产路径与金标准、评测器及样例身份的隔离测试。"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "word2jats"
SAMPLE_IDENTITIES = {
    "01", "02", "03", "04", "05", "S01", "S02", "S03", "S04", "S05",
    "X01", "X02", "X03", "X04", "RCM46777", "RCM46175", "JIN49347",
    "JIN52316", "HSF49106",
}


def _modules():
    return sorted(SOURCE.rglob("*.py"))


def test_production_source_never_names_or_imports_gold_assets():
    forbidden_paths = {"样例数据", "结构参考.xml", "figures.zip"}
    for path in _modules():
        text = path.read_text(encoding="utf-8")
        assert not (forbidden_paths & {item for item in forbidden_paths if item in text}), path
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert all(not name.startswith(("tests", "scripts.eval_")) for name in names), path


def test_production_branches_never_use_sample_number_or_known_article_id():
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        branch_expressions = [
            node.test for node in ast.walk(tree)
            if isinstance(node, (ast.If, ast.IfExp, ast.While))
        ]
        for expression in branch_expressions:
            constants = {
                node.value for node in ast.walk(expression)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            assert not constants.intersection(SAMPLE_IDENTITIES), (
                path, constants.intersection(SAMPLE_IDENTITIES)
            )
