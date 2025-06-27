#!/usr/bin/env python3
"""
Code cleanup analyzer for Dexalot Bot.
Helps identify unused imports, variables, and potential dead code.
"""

import ast
import os
import sys
from typing import Set, Dict, List
from pathlib import Path

class CodeAnalyzer:
    """Analyzes Python code for unused elements."""
    
    def __init__(self, project_root: str = "."):
        self.project_root = Path(project_root)
        self.python_files = list(self.project_root.glob("*.py"))
        self.unused_imports = {}
        self.unused_variables = {}
        self.potential_dead_code = []
    
    def analyze_file(self, file_path: Path) -> Dict:
        """Analyze a single Python file."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            tree = ast.parse(content)
            analyzer = FileAnalyzer(content)
            analyzer.visit(tree)
            
            return {
                'file': str(file_path),
                'unused_imports': analyzer.unused_imports,
                'unused_variables': analyzer.unused_variables,
                'commented_code': analyzer.commented_code,
                'long_functions': analyzer.long_functions
            }
        except Exception as e:
            print(f"Error analyzing {file_path}: {e}")
            return {}
    
    def analyze_project(self):
        """Analyze the entire project."""
        results = {}
        
        for py_file in self.python_files:
            if py_file.name.startswith('__'):
                continue
            
            result = self.analyze_file(py_file)
            if result:
                results[str(py_file)] = result
        
        return results
    
    def print_report(self, results: Dict):
        """Print analysis report."""
        print("=" * 60)
        print("DEXALOT BOT CODE CLEANUP ANALYSIS")
        print("=" * 60)
        
        for file_path, analysis in results.items():
            if not any([analysis.get('unused_imports'), 
                       analysis.get('unused_variables'),
                       analysis.get('commented_code'),
                       analysis.get('long_functions')]):
                continue
            
            print(f"\n📁 {file_path}")
            print("-" * 40)
            
            # Unused imports
            if analysis.get('unused_imports'):
                print("🔶 Potentially unused imports:")
                for imp in analysis['unused_imports']:
                    print(f"   - {imp}")
            
            # Unused variables
            if analysis.get('unused_variables'):
                print("🔶 Potentially unused variables:")
                for var in analysis['unused_variables']:
                    print(f"   - {var}")
            
            # Commented code
            if analysis.get('commented_code'):
                print("🔶 Commented code blocks:")
                for line_num, comment in analysis['commented_code']:
                    print(f"   - Line {line_num}: {comment[:50]}...")
            
            # Long functions
            if analysis.get('long_functions'):
                print("🔶 Long functions (>50 lines):")
                for func_name, lines in analysis['long_functions']:
                    print(f"   - {func_name}: {lines} lines")

class FileAnalyzer(ast.NodeVisitor):
    """AST visitor for analyzing individual files."""
    
    def __init__(self, content: str):
        self.content = content
        self.lines = content.split('\n')
        self.imports = set()
        self.used_names = set()
        self.defined_vars = set()
        self.unused_imports = []
        self.unused_variables = []
        self.commented_code = []
        self.long_functions = []
        self.current_function = None
        self.function_start_line = 0
        
        # Find commented code
        self._find_commented_code()
    
    def _find_commented_code(self):
        """Find potentially commented-out code."""
        for i, line in enumerate(self.lines, 1):
            stripped = line.strip()
            if stripped.startswith('#') and not stripped.startswith('##'):
                # Skip docstrings and obvious comments
                comment_content = stripped[1:].strip()
                if any(keyword in comment_content for keyword in 
                      ['import ', 'def ', 'class ', 'if ', 'for ', 'while ', 'try:', 'except']):
                    self.commented_code.append((i, stripped))
    
    def visit_Import(self, node):
        """Visit import statements."""
        for alias in node.names:
            self.imports.add(alias.name)
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node):
        """Visit from...import statements."""
        for alias in node.names:
            self.imports.add(alias.name)
        self.generic_visit(node)
    
    def visit_Name(self, node):
        """Visit name references."""
        if isinstance(node.ctx, ast.Load):
            self.used_names.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            self.defined_vars.add(node.id)
        self.generic_visit(node)
    
    def visit_FunctionDef(self, node):
        """Visit function definitions."""
        self.current_function = node.name
        self.function_start_line = node.lineno
        
        # Calculate function length
        if hasattr(node, 'end_lineno') and node.end_lineno:
            func_length = node.end_lineno - node.lineno
            if func_length > 50:
                self.long_functions.append((node.name, func_length))
        
        self.generic_visit(node)
        self.current_function = None
    
    def visit_AsyncFunctionDef(self, node):
        """Visit async function definitions."""
        self.visit_FunctionDef(node)
    
    def finalize(self):
        """Finalize analysis and determine unused elements."""
        # Find unused imports (simple heuristic)
        for imp in self.imports:
            if imp not in self.used_names and imp not in ['sys', 'os', 'asyncio']:
                self.unused_imports.append(imp)
        
        # Find potentially unused variables (very basic)
        for var in self.defined_vars:
            if var not in self.used_names and not var.startswith('_'):
                self.unused_variables.append(var)

def main():
    """Main function."""
    analyzer = CodeAnalyzer()
    results = analyzer.analyze_project()
    analyzer.print_report(results)
    
    print("\n" + "=" * 60)
    print("CLEANUP RECOMMENDATIONS")
    print("=" * 60)
    print("""
1. 🧹 Remove commented-out code blocks
2. 📦 Remove unused imports
3. 🔧 Refactor long functions (>50 lines) into smaller ones
4. 🗑️  Remove unused variables (check carefully!)
5. 📝 Add docstrings to functions without them
6. 🔄 Consider using type hints consistently
7. 🧪 Add error handling where missing

Note: This is a basic analysis. Always verify before removing code!
    """)

if __name__ == "__main__":
    main() 