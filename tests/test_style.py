"""Keep the code base consistent: line length, docstrings, unused imports, risky constructs."""
from __future__ import annotations

import ast
import io
import tokenize
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIBRARY = sorted((REPO / "mforge").glob("*.py"))
ALL_PYTHON = LIBRARY + [REPO / "macroforge.py"] + sorted((REPO / "tests").glob("*.py"))
MAX_LINE = 100        # library and entry script
MAX_LINE_TESTS = 120  # test files carry long fixtures and assertions


def tree_of(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


class StyleTests(unittest.TestCase):
    def test_line_length(self):
        problems = []
        for path in ALL_PYTHON:
            limit = MAX_LINE_TESTS if path.parent.name == "tests" else MAX_LINE
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if len(line) > limit:
                    problems.append(f"{path.name}:{number} ({len(line)} > {limit})")
        self.assertEqual(problems, [], "lines are too long")

    def test_no_semicolon_statements(self):
        problems = []
        for path in ALL_PYTHON:
            with open(path, "rb") as handle:
                for tok in tokenize.tokenize(handle.readline):
                    if tok.type == tokenize.OP and tok.string == ";":
                        problems.append(f"{path.name}:{tok.start[0]}")
        self.assertEqual(problems, [])

    def test_library_has_docstrings(self):
        problems = []
        for path in LIBRARY:
            tree = tree_of(path)
            if not ast.get_docstring(tree):
                problems.append(f"{path.name}: module")
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not node.name.startswith("_") \
                        and not ast.get_docstring(node):
                    problems.append(f"{path.name}:{node.lineno} {node.name}")
        self.assertEqual(problems, [], "public modules, classes and functions need docstrings")

    def test_no_unused_imports(self):
        problems = []
        for path in ALL_PYTHON:
            tree = tree_of(path)
            imported = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported[(alias.asname or alias.name).split(".")[0]] = node.lineno
                elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
                    for alias in node.names:
                        imported[alias.asname or alias.name] = node.lineno
            used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            text = path.read_text(encoding="utf-8")
            for name, line in imported.items():
                # names used only inside string annotations still count
                if name not in used and text.count(name) < 2 and path.name != "macroforge.py":
                    problems.append(f"{path.name}:{line} {name}")
        self.assertEqual(problems, [])

    def test_no_bare_or_redundant_excepts(self):
        problems = []
        for path in LIBRARY:
            for node in ast.walk(tree_of(path)):
                if isinstance(node, ast.ExceptHandler):
                    if node.type is None:
                        problems.append(f"{path.name}:{node.lineno} bare except")
                    elif "SystemExit" in ast.dump(node.type):
                        problems.append(f"{path.name}:{node.lineno} catches SystemExit")
        self.assertEqual(problems, [])

    def test_library_never_exits_the_process(self):
        """Only the command line decides the exit code; library code raises MacroForgeError."""
        problems = []
        for path in LIBRARY:
            if path.name in ("cli.py", "__main__.py"):
                continue
            for node in ast.walk(tree_of(path)):
                if isinstance(node, ast.Call):
                    func = ast.unparse(node.func)
                    if func in ("sys.exit", "exit", "quit", "os._exit") or func == "SystemExit":
                        problems.append(f"{path.name}:{node.lineno} {func}")
                if isinstance(node, ast.Raise) and node.exc is not None \
                        and "SystemExit" in ast.unparse(node.exc):
                    problems.append(f"{path.name}:{node.lineno} raise SystemExit")
        self.assertEqual(problems, [])

    def test_no_import_time_filesystem_or_environment_access(self):
        """Module level code may only define things (settings are loaded explicitly)."""
        allowed = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign,
                   ast.Expr, ast.If)
        problems = []
        for path in LIBRARY:
            for node in tree_of(path).body:
                if not isinstance(node, allowed):
                    problems.append(f"{path.name}:{node.lineno} {type(node).__name__}")
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                    for call in ast.walk(node.value):
                        if isinstance(call, ast.Call) and ast.unparse(call.func) in (
                                "open", "Path.exists", "os.environ.get", "json.loads", "Path.read_text"):
                            problems.append(f"{path.name}:{node.lineno} calls {ast.unparse(call.func)}")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
