import ast
from models import AnalysisContext

def check_no_print(context: AnalysisContext):
    """示例检查：禁止代码中出现 print() 调用"""
    tree = ast.parse(context.code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'print':
            context.errors.append("代码中包含 print() 调用，建议移除")
