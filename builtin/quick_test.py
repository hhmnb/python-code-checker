import ast
import importlib
import subprocess
import sys
import json
from typing import Optional, Tuple, Any, List
from models import TestResult

# 常见模块名到导入语句的映射，可扩展
MODULE_IMPORT_MAP = {
    'os': 'import os',
    'sys': 'import sys',
    'json': 'import json',
    're': 'import re',
    'math': 'import math',
    'random': 'import random',
    'datetime': 'import datetime',
    'collections': 'import collections',
    'itertools': 'import itertools',
    'functools': 'import functools',
    'typing': 'import typing',
    'subprocess': 'import subprocess',
    'sqlite3': 'import sqlite3',
    'pickle': 'import pickle',
}


def _extract_function(code: str) -> Optional[Tuple[str, ast.FunctionDef]]:
    """从代码片段中提取第一个函数定义"""
    tree = ast.parse(code)
    for node in ast.body:
        if isinstance(node, ast.FunctionDef):
            return (ast.get_source_segment(code, node) or code, node)
    return None


def _infer_used_modules(func_node: ast.FunctionDef) -> List[str]:
    """分析函数内部使用的模块名（通过属性访问和名称引用）"""
    used_modules = set()
    for node in ast.walk(func_node):
        # 检查 Attribute: os.path, json.dumps 等
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module_name = node.value.id
            if module_name in MODULE_IMPORT_MAP:
                used_modules.add(module_name)
        # 检查直接名称调用: subprocess.call 等
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                module_name = node.func.value.id
                if module_name in MODULE_IMPORT_MAP:
                    used_modules.add(module_name)
    return list(used_modules)


def _generate_args(func_node: ast.FunctionDef) -> Tuple[List[Any], dict]:
    """为函数生成调用参数（同 sandbox 中的逻辑）"""
    pos_args = []
    kw_args = {}
    for arg in func_node.args.args:
        if arg.arg in ('self', 'cls'):
            continue
        annotation = arg.annotation
        val = None
        if annotation is not None:
            if isinstance(annotation, ast.Name):
                type_map = {
                    'int': 0, 'float': 0.0, 'bool': False, 'str': '',
                    'list': [], 'dict': {}, 'tuple': (), 'set': set(), 'bytes': b''
                }
                val = type_map.get(annotation.id, None)
        pos_args.append(val)

    # 处理默认值
    defaults = func_node.args.defaults
    if defaults:
        num_no_default = len(pos_args) - len(defaults)
        for i, default in enumerate(defaults):
            idx = num_no_default + i
            if 0 <= idx < len(pos_args) and isinstance(default, ast.Constant):
                pos_args[idx] = default.value
    return pos_args, kw_args


def _generate_mock_code(missing_modules: List[str]) -> str:
    """为缺失的模块生成 Mock 代码"""
    lines = []
    for mod in missing_modules:
        lines.append(
            f"import sys; from unittest.mock import MagicMock; sys.modules['{mod}'] = MagicMock()"
        )
    return "\n".join(lines)


def quick_test(function_code: str, timeout: int = 5, mock_missing: bool = False) -> TestResult:
    """
    对单独函数进行智能快速测试。

    Args:
        function_code: 包含函数定义的代码字符串。
        timeout: 执行超时时间（秒）。
        mock_missing: 是否对无法安装的缺失模块使用 Mock（默认 False，暴露真实错误）。

    Returns:
        TestResult 包含测试状态和消息。
    """
    # 提取函数定义
    extracted = _extract_function(function_code)
    if not extracted:
        return TestResult("<unknown>", False, "未找到有效函数定义")

    func_source, func_node = extracted
    func_name = func_node.name

    # 推断需导入的模块并尝试自动安装
    modules = _infer_used_modules(func_node)
    missing = []
    for mod in modules:
        try:
            importlib.import_module(mod)
        except ImportError:
            # 尝试用 pip 安装
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", mod],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                # 安装后重新验证
                importlib.import_module(mod)
            except Exception:
                missing.append(mod)  # 记录安装失败的模块

    # 构建导入语句（已安装的模块正常导入）
    imports = "\n".join([MODULE_IMPORT_MAP[m] for m in modules if m in MODULE_IMPORT_MAP])

    # 如果启用 Mock 且存在安装失败的模块，注入 Mock 代码
    mock_code = ""
    if mock_missing and missing:
        mock_code = _generate_mock_code(missing)

    # 生成参数
    pos_args, kw_args = _generate_args(func_node)
    call_parts = [repr(a) for a in pos_args]
    for k, v in kw_args.items():
        call_parts.append(f"{k}={repr(v)}")
    call_str = ", ".join(call_parts)

    # 构造执行代码：先 Mock（如果需要），再正常导入，然后执行函数
    wrapper = f"""
import json
{mock_code}
{imports}
{func_source}
try:
    result = {func_name}({call_str})
    print(json.dumps({{"passed": True, "result": repr(result)}}))
except Exception as e:
    print(json.dumps({{"passed": False, "error": str(e)}}))
"""
    try:
        proc = subprocess.run(
            [sys.executable, '-c', wrapper],
            capture_output=True, text=True, timeout=timeout
        )
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout)
            passed = data.get("passed", False)
            msg = data.get("result") if passed else data.get("error", "unknown error")
            # 若使用了 Mock，附加提示信息
            if mock_missing and missing:
                msg += f" (已Mock缺失模块: {', '.join(missing)})"
            return TestResult(func_name, passed, msg)
        else:
            return TestResult(func_name, False, proc.stderr.strip())
    except subprocess.TimeoutExpired:
        return TestResult(func_name, False, f"超时 (>{timeout}s)")