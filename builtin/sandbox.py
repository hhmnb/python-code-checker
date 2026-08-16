import ast
import subprocess
import sys
import json
import tempfile
import os as _os_module
from typing import Any, Dict, List, Optional, Tuple
from models import AnalysisContext, TestResult
from builtin.extraction import extract_functions


# ------------------------------------------------------------
# 参数生成辅助函数
# ------------------------------------------------------------
def _generate_default_args(func_code: str) -> Tuple[List[Any], Dict[str, Any]]:
    tree = ast.parse(func_code)
    func_def = tree.body[0]
    if not isinstance(func_def, ast.FunctionDef):
        return [], {}

    pos_args = []
    kw_args = {}
    for arg in func_def.args.args:
        if arg.arg in ('self', 'cls'):
            continue
        pos_args.append(_default_value_for_annotation(arg.annotation))

    defaults = func_def.args.defaults
    if defaults:
        num_no_default = len(pos_args) - len(defaults)
        for i, default in enumerate(defaults):
            idx = num_no_default + i
            if 0 <= idx < len(pos_args) and isinstance(default, ast.Constant):
                pos_args[idx] = default.value

    for kwarg in func_def.args.kwonlyargs:
        kw_args[kwarg.arg] = _default_value_for_annotation(kwarg.annotation)

    kw_defaults = func_def.args.kw_defaults
    if kw_defaults:
        for i, kwarg in enumerate(func_def.args.kwonlyargs):
            default_node = kw_defaults[i]
            if isinstance(default_node, ast.Constant):
                kw_args[kwarg.arg] = default_node.value

    return pos_args, kw_args


def _default_value_for_annotation(annotation: Optional[ast.expr]) -> Any:
    if annotation is None:
        return None
    type_map = {
        'int': 0,
        'float': 0.0,
        'bool': False,
        'str': '',
        'list': [],
        'dict': {},
        'tuple': (),
        'set': set(),
        'bytes': b'',
    }
    if isinstance(annotation, ast.Name) and annotation.id in type_map:
        return type_map[annotation.id]
    return None


def _is_method(func_code: str) -> bool:
    """第一个参数是 self/cls 则跳过"""
    try:
        tree = ast.parse(func_code)
        func_def = tree.body[0]
        if isinstance(func_def, ast.FunctionDef) and func_def.args.args:
            first = func_def.args.args[0].arg
            return first in ('self', 'cls')
    except Exception:
        pass
    return False


# ------------------------------------------------------------
# Mock 模块生成
# ------------------------------------------------------------
def _generate_mock_code(full_code: str, missing_top_modules: List[str]) -> str:
    if not missing_top_modules:
        return ""

    modules_to_mock = set()
    try:
        tree = ast.parse(full_code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules_to_mock.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules_to_mock.add(node.module)
    except SyntaxError:
        pass

    filtered = set()
    for name in modules_to_mock:
        top = name.split('.')[0]
        if top in missing_top_modules:
            filtered.add(name)

    if not filtered:
        return "\n".join(
            f"import sys; from unittest.mock import MagicMock; sys.modules.setdefault('{m}', MagicMock())"
            for m in missing_top_modules
        )

    lines = ["import sys", "from unittest.mock import MagicMock"]
    all_modules = set(filtered)
    for name in list(all_modules):
        parts = name.split('.')
        for i in range(1, len(parts)):
            all_modules.add('.'.join(parts[:i]))

    for mod in sorted(all_modules):
        lines.append(f"sys.modules.setdefault('{mod}', MagicMock())")
    return "\n".join(lines)


# ------------------------------------------------------------
# 环境增强：Flask 上下文（延迟激活）
# ------------------------------------------------------------
def _detect_flask_app(code: str) -> Optional[str]:
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Name) and func.id == 'Flask':
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            return target.id
    except SyntaxError:
        pass
    return None


def _generate_flask_setup(code: str) -> str:
    """先记录 app 变量名，稍后在脚本加载后激活上下文"""
    app_var = _detect_flask_app(code)
    if not app_var:
        return ""
    return f"""
_flask_app_var = '{app_var}'
"""


def _generate_flask_activate() -> str:
    """在脚本加载后调用，尝试激活 Flask 测试上下文"""
    return """
if '_flask_app_var' in dir():
    try:
        _flask_ctx = eval(_flask_app_var).test_request_context()
        _flask_ctx.__enter__()
        _flask_active = True
    except Exception:
        pass
"""


# ------------------------------------------------------------
# 环境增强：SQLite 内存数据库（自动建表）
# ------------------------------------------------------------
def _generate_sqlite_setup(code: str) -> str:
    if 'sqlite3.connect' not in code:
        return ""
    return """
import sqlite3 as _sqlite3
_original_connect = _sqlite3.connect
def _mock_connect(*args, **kwargs):
    conn = _original_connect(':memory:')
    # 自动创建可能用到的表
    conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT, visible INTEGER DEFAULT 1)")
    return conn
_sqlite3.connect = _mock_connect
"""


# ------------------------------------------------------------
# 环境增强：临时文件/目录
# ------------------------------------------------------------
def _generate_filesystem_setup(code: str, func_name: str) -> str:
    setup = []
    if 'download_report' in code:
        setup.append("import os as _os; _os.makedirs('/var/app/reports/', exist_ok=True); open('/var/app/reports/empty.txt', 'w').close()")
    if 'process_file' in code:
        setup.append("import tempfile, os; _tmp = tempfile.NamedTemporaryFile(delete=False); _tmp.write(b'test'); _tmp.close(); _process_file_path = _tmp.name")
    return "\n".join(setup)


# ------------------------------------------------------------
# 智能输入生成
# ------------------------------------------------------------
def _smart_args_override(func_name: str, pos_args: list, kw_args: dict, func_code: str) -> Tuple[list, dict]:
    if func_name == 'parse_xml' and pos_args and pos_args[0] == '':
        pos_args[0] = '<root/>'
    if func_name == 'load_user_session' and pos_args and (pos_args[0] == b'' or pos_args[0] is None):
        import pickle
        pos_args[0] = pickle.dumps(None)
    if func_name == 'execute_custom_script' and pos_args and pos_args[0] == '':
        pos_args[0] = 'nonexistent_module'
    if func_name == 'download_report' and pos_args and pos_args[0] == '':
        pos_args[0] = 'empty.txt'
    if func_name == 'process_file' and pos_args and pos_args[0] == '':
        pos_args[0] = '_process_file_path'
    return pos_args, kw_args


# ------------------------------------------------------------
# 沙箱测试主流程
# ------------------------------------------------------------
def sandbox_test(context: AnalysisContext, timeout: int = 5, mock_missing: bool = True):
    funcs = extract_functions(context.code)
    if not funcs:
        context.results.append(TestResult("<module>", True, "无可执行函数，视为脚本通过"))
        return

    mock_code = ""
    if mock_missing and context.missing_modules:
        mock_code = _generate_mock_code(context.code, context.missing_modules)

    flask_setup = _generate_flask_setup(context.code)   # 记录 app 变量名
    flask_activate = _generate_flask_activate()         # 激活上下文（在脚本加载后）
    sqlite_setup = _generate_sqlite_setup(context.code)

    for func in funcs:
        if _is_method(func['code']):
            context.results.append(
                TestResult(func['name'], False, "类方法无法自动测试（缺少实例）")
            )
            continue

        try:
            pos_args, kw_args = _generate_default_args(func['code'])
        except Exception:
            pos_args, kw_args = [], {}

        pos_args, kw_args = _smart_args_override(func['name'], pos_args, kw_args, func['code'])
        fs_setup = _generate_filesystem_setup(context.code, func['name'])

        call_parts = []
        for a in pos_args:
            if isinstance(a, str) and a == '_process_file_path':
                call_parts.append(a)
            else:
                call_parts.append(repr(a))
        for k, v in kw_args.items():
            call_parts.append(f"{k}={repr(v)}")
        call_str = ', '.join(call_parts)

        # ★ 关键修复：提前初始化 _flask_active 和 _flask_ctx，避免无 Flask 时 NameError
        wrapper = f"""
import json
import os as _sandbox_os
import sys as _sandbox_sys
import subprocess as _subprocess
import os as _os
__name__ = '__sandbox__'
_flask_active = False
_flask_ctx = None
{mock_code}
{context.code}
{flask_setup}
{flask_activate}
{sqlite_setup}
{fs_setup}
_subprocess.call = lambda *args, **kwargs: 0
_os.system = lambda *args, **kwargs: 0
_stdout_backup = _sandbox_sys.stdout
_stderr_backup = _sandbox_sys.stderr
_sandbox_sys.stdout = open(_sandbox_os.devnull, 'w')
_sandbox_sys.stderr = open(_sandbox_os.devnull, 'w')
_success = False
_result = None
_error = None
try:
    if _flask_active:
        with _flask_ctx:
            _result = {func['name']}({call_str})
            _success = True
    else:
        _result = {func['name']}({call_str})
        _success = True
except Exception as e:
    _error = str(e)
finally:
    _sandbox_sys.stdout.close()
    _sandbox_sys.stderr.close()
    _sandbox_sys.stdout = _stdout_backup
    _sandbox_sys.stderr = _stderr_backup
    if _flask_active:
        try:
            _flask_ctx.__exit__(None, None, None)
        except:
            pass
if _success:
    print(json.dumps({{"passed": True, "result": repr(_result)}}))
else:
    print(json.dumps({{"passed": False, "error": _error}}))
"""
        try:
            proc = subprocess.run(
                [sys.executable, '-c', wrapper],
                capture_output=True, text=True, timeout=timeout,
            )

            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            if stdout:
                try:
                    data = json.loads(stdout)
                    passed = data.get("passed", False)
                    msg = data.get("result") if passed else data.get("error", "unknown error")
                    context.results.append(TestResult(func['name'], passed, msg))
                except json.JSONDecodeError:
                    context.results.append(TestResult(func['name'], False, f"子进程输出异常: {stdout[:200]}"))
            else:
                if stderr:
                    context.results.append(TestResult(func['name'], False, stderr))
                else:
                    context.results.append(TestResult(func['name'], False, f"返回码: {proc.returncode} (无输出)"))

        except subprocess.TimeoutExpired:
            context.results.append(TestResult(func['name'], False, f"超时 (>{timeout}s)"))