import ast
import importlib
import importlib.util
import subprocess
import sys
from models import AnalysisContext


def _is_standard_lib(module_name: str) -> bool:
    """判断模块名是否属于标准库（避免尝试 pip install）"""
    # 内置模块名集合
    builtin = set(sys.builtin_module_names)
    # 常见标准库名称补充
    common_stdlib = {
        'os', 'sys', 'json', 're', 'math', 'random', 'datetime', 'collections',
        'itertools', 'functools', 'typing', 'subprocess', 'sqlite3', 'pickle',
        'hashlib', 'xml', 'threading', 'asyncio', 'importlib', 'inspect',
        'ast', 'io', 'pathlib', 'shutil', 'tempfile', 'logging', 'unittest',
        'http', 'urllib', 'socket', 'email', 'csv', 'configparser',
        'argparse', 'getopt', 'struct', 'base64', 'hashlib', 'binascii',
        'json', 'html', 'multiprocessing', 'ctypes', 'traceback',
    }
    if module_name in builtin or module_name in common_stdlib:
        return True
    # 进一步通过查找模块 spec 判断：标准库的 origin 通常不在 site-packages
    try:
        spec = importlib.util.find_spec(module_name)
        if spec and spec.origin and 'site-packages' not in spec.origin:
            return True
    except Exception:
        pass
    return False


def check_dependencies(context: AnalysisContext, auto_install: bool = True):
    """检测并自动安装缺失的第三方模块"""
    tree = ast.parse(context.code)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split('.')[0])

    for imp in imports:
        # 跳过标准库和内建模块
        if _is_standard_lib(imp):
            continue

        try:
            importlib.import_module(imp)
        except ImportError:
            if auto_install:
                # 尝试用 pip 安装
                try:
                    subprocess.check_call(
                        [sys.executable, "-m", "pip", "install", imp],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    # 安装后再次验证
                    importlib.import_module(imp)
                    # 安装成功，添加提示信息
                    context.errors.append(f"✅ 模块 '{imp}' 已自动安装成功")
                except Exception:
                    # 安装失败，记录错误并将模块放入缺失列表
                    context.errors.append(f"❌ 缺少模块: {imp} (自动安装失败)")
                    if imp not in context.missing_modules:
                        context.missing_modules.append(imp)
            else:
                # 不自动安装，直接记录并放入缺失列表
                context.errors.append(f"缺少模块: {imp}")
                if imp not in context.missing_modules:
                    context.missing_modules.append(imp)