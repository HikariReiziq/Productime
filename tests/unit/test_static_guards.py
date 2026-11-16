"""Source-level guards for the security and layering rules in CLAUDE.md."""

import ast
import re
import sys
import tomllib
import unittest

import _bootstrap

SRC = _bootstrap.SRC / "productime"
SYSTEM_BINDINGS = {"gi", "cairo"}
DOMAIN_ALLOWED = {
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "math",
    "random",
    "re",
    "typing",
}
FORBIDDEN_CALLS = {
    "set_markup(": "user text must go through set_text() or set_label()",
    "shell=True": "no shell command execution",
    "os.system(": "no shell command execution",
    "os.popen(": "no shell command execution",
    "use_markup=True": "markup parsing must stay off",
    "set_use_markup(True": "markup parsing must stay off",
}


def python_files():
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def imported_modules(path):
    """Yield (level, dotted_name) for every import in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield 0, alias.name
        elif isinstance(node, ast.ImportFrom):
            yield node.level, node.module or ""


class RuntimeDependencyTests(unittest.TestCase):
    def test_pyproject_declares_no_pip_dependencies(self):
        with open(_bootstrap.ROOT / "pyproject.toml", "rb") as handle:
            project = tomllib.load(handle)["project"]

        self.assertEqual(project["dependencies"], [])
        self.assertNotIn("optional-dependencies", project)

    def test_source_imports_only_the_standard_library_and_system_bindings(self):
        allowed = set(sys.stdlib_module_names) | SYSTEM_BINDINGS | {"productime"}
        for path in python_files():
            for level, name in imported_modules(path):
                if level == 0:
                    with self.subTest(file=path.name, module=name):
                        self.assertIn(name.split(".")[0], allowed)

    def test_no_browser_or_web_runtime_is_referenced(self):
        banned = re.compile(r"\b(WebKit|webkit2|electron|chromium|selenium|playwright)\b", re.IGNORECASE)
        for path in python_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0]
                with self.subTest(file=path.name, line=number):
                    self.assertIsNone(banned.search(code))

    def test_no_subprocess_is_used_by_the_application(self):
        for path in python_files():
            modules = {name.split(".")[0] for _level, name in imported_modules(path)}
            with self.subTest(file=path.name):
                self.assertNotIn("subprocess", modules)


class UnsafeCallTests(unittest.TestCase):
    def test_forbidden_calls_do_not_appear(self):
        for path in python_files():
            text = path.read_text(encoding="utf-8")
            for needle, reason in FORBIDDEN_CALLS.items():
                with self.subTest(file=path.name, call=needle):
                    self.assertNotIn(needle, text, reason)

    def test_sql_is_never_assembled_from_strings(self):
        dynamic = re.compile(r"execute(?:many|script)?\(\s*(?:f[\"']|[\"'][^\"']*[\"']\s*(?:%|\+|\.format))")
        for path in python_files():
            with self.subTest(file=path.name):
                self.assertIsNone(dynamic.search(path.read_text(encoding="utf-8")))

    def test_adwaita_widgets_that_parse_markup_never_receive_it_by_default(self):
        # Adw.Toast and Adw.Banner parse markup unless told otherwise.
        for path in python_files():
            text = path.read_text(encoding="utf-8")
            for constructor in ("Adw.Toast(", "Adw.Banner("):
                for match in re.finditer(re.escape(constructor) + r"([^)]*)\)", text):
                    with self.subTest(file=path.name, call=constructor):
                        self.assertIn("use_markup=False", match.group(1))
            with self.subTest(file=path.name):
                self.assertNotIn("Adw.Toast.new(", text)
                self.assertNotIn("Adw.Banner.new(", text)


class LayeringTests(unittest.TestCase):
    def packages_imported(self, path):
        found = set()
        depth = len(path.relative_to(SRC).parts) - 1
        for level, name in imported_modules(path):
            if level == 0 and name.startswith("productime."):
                found.add(name.split(".")[1])
            elif level > 0:
                # Resolve a relative import to the top-level package it reaches.
                up = level - 1
                base = list(path.relative_to(SRC).parts[:-1])
                base = base[: len(base) - up] if up else base
                parts = [*base, *(name.split(".") if name else [])]
                if parts and depth >= up:
                    found.add(parts[0])
        return found

    def test_domain_is_pure_python(self):
        for path in sorted((SRC / "domain").glob("*.py")):
            for level, name in imported_modules(path):
                if level == 0:
                    with self.subTest(file=path.name, module=name):
                        self.assertIn(name.split(".")[0], DOMAIN_ALLOWED)
            with self.subTest(file=path.name):
                self.assertLessEqual(self.packages_imported(path), {"domain"} | self.siblings("domain"))

    def siblings(self, package):
        return {p.stem for p in (SRC / package).glob("*.py")}

    def test_lower_layers_never_import_upward(self):
        forbidden = {
            "domain": {"db", "services", "ui", "app"},
            "db": {"services", "ui", "app"},
            "services": {"ui", "app"},
        }
        for package, banned in forbidden.items():
            for path in sorted((SRC / package).rglob("*.py")):
                with self.subTest(file=f"{package}/{path.name}"):
                    self.assertFalse(self.packages_imported(path) & banned)


if __name__ == "__main__":
    unittest.main()
