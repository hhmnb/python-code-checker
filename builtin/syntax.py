import ast
from models import AnalysisContext

def check_syntax(context: AnalysisContext):
    try:
        ast.parse(context.code)
    except SyntaxError as e:
        context.errors.append(f"SyntaxError: {e}")
