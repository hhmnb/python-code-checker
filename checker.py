
# checker.py
import os
import importlib.util
import inspect
import subprocess
import sys
import re
from typing import List, Optional

from models import AnalysisContext
from builtin.syntax import check_syntax
from builtin.sandbox import sandbox_test

MODULE_ALIASES = {
    'PIL': 'Pillow',
    'Crypto': 'pycryptodome',
    'bs4': 'beautifulsoup4',
    'sklearn': 'scikit-learn',
    'cv2': 'opencv-python',
    'MySQLdb': 'mysqlclient',
    'yaml': 'pyyaml',
}


class RunnableChecker:
    def __init__(self, checks_dir: str = 'checks'):
        self.plugin_funcs = self._discover_plugins(checks_dir)

    def _discover_plugins(self, dirpath: str) -> list:
        """加载 checks 目录下所有以 check_ 开头的函数作为检测插件"""
        funcs = []
        if not os.path.isdir(dirpath):
            return funcs
        for fname in os.listdir(dirpath):
            if fname.endswith('.py') and not fname.startswith('__'):
                path = os.path.join(dirpath, fname)
                spec = importlib.util.spec_from_file_location(fname[:-3], path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for name, obj in inspect.getmembers(module, inspect.isfunction):
                    if name.startswith('check_'):
                        funcs.append(obj)
        return funcs

    def _extract_missing_module(self, error_text: str) -> Optional[str]:
        match = re.search(r"No module named '(\w+)'", error_text)
        if match:
            return match.group(1)
        return None

    def _resolve_package_name(self, module_name: str) -> str:
        return MODULE_ALIASES.get(module_name, module_name)

    def _try_install_module(self, module_name: str) -> bool:
        package = self._resolve_package_name(module_name)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", package],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    def _prepare_environment(self, context: AnalysisContext, max_retries: int = 5, env_timeout: int = 30):
        """
        动态环境准备：实际运行脚本，缺失模块自动安装。
        安装失败或安装成功但导入仍失败的模块会被记录到 missing_modules。
        """
        failed_modules: set = set()
        installed_once: set = set()

        for _ in range(max_retries):
            wrapper = f"""
import sys
__name__ = '__checker__'
try:
    exec({repr(context.code)})
except ModuleNotFoundError as e:
    print(f"MISSING_MODULE:{{e.name}}", file=sys.stderr)
    sys.exit(1)
except SyntaxError:
    sys.exit(2)
except Exception:
    sys.exit(3)
"""
            try:
                proc = subprocess.run(
                    [sys.executable, '-c', wrapper],
                    capture_output=True, text=True,
                    timeout=env_timeout
                )
            except subprocess.TimeoutExpired:
                context.errors.append(f"环境准备超时（{env_timeout}秒），脚本可能包含死循环")
                context.missing_modules.extend(failed_modules | installed_once)
                return

            if proc.returncode == 0:
                # 环境准备成功
                context.missing_modules = list(failed_modules | installed_once)
                return

            stderr = proc.stderr.strip()
            if proc.returncode == 1 and stderr.startswith("MISSING_MODULE:"):
                module_name = stderr.split(":", 1)[1].strip()
                if module_name in installed_once or module_name in failed_modules:
                    failed_modules.add(module_name)
                    continue
                if self._try_install_module(module_name):
                    installed_once.add(module_name)
                    continue
                else:
                    failed_modules.add(module_name)
                    context.errors.append(f"❌ 缺少模块: {module_name} (自动安装失败)")
                    continue
            else:
                # 其他错误退出，不再重试
                break

        context.missing_modules.extend(failed_modules | installed_once)
        if context.missing_modules:
            context.errors.append("动态环境准备完成，部分模块无法自动安装，后续将 Mock")

    def check(self, code: str, filename: str = None, mock_missing: bool = True) -> AnalysisContext:
        context = AnalysisContext(code=code, filename=filename)
        # 语法检查
        check_syntax(context)
        # 动态环境准备（自动安装缺失模块）
        self._prepare_environment(context)
        # 沙箱逐个函数测试
        sandbox_test(context, mock_missing=mock_missing)
        # 执行外部插件检测
        for func in self.plugin_funcs:
            func(context)
        return context
