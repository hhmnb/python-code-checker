import ast

def extract_functions(code: str) -> list[dict]:
    """提取所有函数名和源码，返回列表"""
    tree = ast.parse(code)
    funcs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            funcs.append({
                "name": node.name,
                "code": ast.get_source_segment(code, node),
            })
    return funcs
