import ast
import json
import os
import sys
from typing import Dict, List, Any

LEGACY_TARGETS = {
    "nexa_consent_engine",
    "consent_service",
    "routine",
    "break_glass",
}

class ConsentAuditVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.results: List[Dict[str, Any]] = []
        # Maps aliases to full names
        self.aliases: Dict[str, str] = {}

    def _add_match(self, node: ast.AST, symbol: str):
        is_test = "tests/" in self.file_path
        self.results.append({
            "file": self.file_path,
            "line": node.lineno,
            "symbol": symbol,
            "type": "test" if is_test else "production"
        })

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            name = alias.name.split('.')[-1]
            if name in LEGACY_TARGETS or any(t in alias.name for t in LEGACY_TARGETS):
                self._add_match(node, alias.name)
                if alias.asname:
                    self.aliases[alias.asname] = alias.name
                else:
                    self.aliases[alias.name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            if any(t in node.module for t in LEGACY_TARGETS):
                self._add_match(node, node.module)
            for alias in node.names:
                if alias.name in LEGACY_TARGETS:
                    self._add_match(node, f"{node.module}.{alias.name}")
                    if alias.asname:
                        self.aliases[alias.asname] = f"{node.module}.{alias.name}"
                    else:
                        self.aliases[alias.name] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Direct calls
        if isinstance(node.func, ast.Name):
            if node.func.id in self.aliases:
                self._add_match(node, self.aliases[node.func.id])
        
        # Attribute calls (e.g. routine.issue)
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                base = node.func.value.id
                if base in self.aliases:
                    full_symbol = f"{self.aliases[base]}.{node.func.attr}"
                    if any(t in full_symbol for t in LEGACY_TARGETS):
                        self._add_match(node, full_symbol)
        
        # getattr pattern
        elif isinstance(node.func, ast.Name) and node.func.id == "getattr":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                attr_name = node.args[1].value
                if any(t in str(attr_name) for t in LEGACY_TARGETS):
                    self._add_match(node, f"getattr({attr_name})")

        self.generic_visit(node)

def audit_directory(path: str) -> List[Dict[str, Any]]:
    matches = []
    for root, _, files in os.walk(path):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read(), filename=file_path)
                    visitor = ConsentAuditVisitor(file_path)
                    visitor.visit(tree)
                    matches.extend(visitor.results)
                except Exception as e:
                    print(f"Error parsing {file_path}: {e}", file=sys.stderr)
    return matches

if __name__ == "__main__":
    report = []
    report.extend(audit_directory("app"))
    report.extend(audit_directory("tests"))
    
    print(json.dumps(report, indent=2))
