import ast
from typing import List, Dict

class FunctionLocation:
    def __init__(self, name, start_line, end_line, nest_level, indent_spaces, source):
        self.name = name
        self.start_line = start_line
        self.end_line = end_line
        self.nest_level = nest_level
        self.indent_spaces = indent_spaces   # 新增：def 关键字前的空格数量
        self.source = source

    def to_dict(self):
        return {
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "nest_level": self.nest_level,
            "indent_spaces": self.indent_spaces,
            "source": self.source
        }

def _walk_functions(node, depth=0):
    """递归遍历 AST，返回所有 FunctionDef 及嵌套深度、缩进空格数"""
    funcs = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef):
            start = child.lineno
            end = child.end_lineno
            # col_offset 即为 def 关键字所在的列偏移（0-indexed，即缩进空格数）
            indent = child.col_offset
            funcs.append(FunctionLocation(child.name, start, end, depth, indent, ""))
            # 递归处理函数内部，深度+1
            funcs.extend(_walk_functions(child, depth + 1))
        else:
            funcs.extend(_walk_functions(child, depth))
    return funcs

def analyze_functions(code: str) -> List[FunctionLocation]:
    tree = ast.parse(code)
    funcs = _walk_functions(tree, 0)
    # 补充源码
    lines = code.splitlines()
    for f in funcs:
        f.source = "\n".join(lines[f.start_line-1:f.end_line])
    return funcs

def locate_problem_functions(code: str, error_names: List[str]) -> List[Dict]:
    all_funcs = analyze_functions(code)
    name_map = {f.name: f for f in all_funcs}
    result = []
    for name in error_names:
        if name in name_map:
            result.append(name_map[name].to_dict())
        else:
            result.append({
                "name": name,
                "start_line": None,
                "end_line": None,
                "nest_level": None,
                "indent_spaces": None,
                "source": None,
                "note": "无法自动定位（可能为类方法或内建）"
            })
    return result

def export_locate_report(context, code: str, output_path: str):
    failed_names = [r.function_name for r in context.results if not r.passed]
    locations = locate_problem_functions(code, failed_names)
    import json
    report = {
        "file": context.filename,
        "failed_functions": locations
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"📍 问题函数定位报告已保存至: {output_path}")