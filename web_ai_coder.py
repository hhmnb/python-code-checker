#!/usr/bin/env python3
"""
Web AI Coder —— 本地 AI 编程助手（终极稳定版，智能路径修复 + 智能启动验证）
用法:
  python web_ai_coder.py [task.txt]

特性：
- 彻底清除反斜杠路径，杜绝 SyntaxWarning
- 智能启动验证：GUI 项目改用导入检查，避免因无图形环境误判
- 其余功能同前
"""

import os
import sys
import json
import re
import time
import py_compile
import subprocess
import shutil

os.environ['PYTHONUTF8'] = '1'
if sys.getdefaultencoding() != 'utf-8':
    import locale
    try:
        locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    except:
        pass

from deepseek_sender import DeepSeekWebSender
from instruction_loader import load_instructions, generate_summary, build_norm_text
from plan_executor import PlanExecutor

# ================= 配置 =================
CONFIG_FILE = "config.json"
MAX_PLAN_RETRIES = 3
CONFIRM_BEFORE_EXECUTE = False
STARTUP_TIMEOUT = 15
MAX_AUTO_FIX_ATTEMPTS = 5

_STDLIB_PREFIX = os.path.normpath(os.path.dirname(os.__file__)).lower()


def load_task(task_file: str) -> dict:
    config = {}
    if not os.path.exists(task_file):
        print(f"❌ 任务文件不存在: {task_file}")
        sys.exit(1)
    with open(task_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith('[') and ']' in line:
                key = line[1:line.index(']')].strip()
                value = line[line.index(']')+1:].strip()
                config[key] = value
    return config


def load_web_config() -> dict:
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ 配置文件 {CONFIG_FILE} 不存在，请先运行 setup_wizard.py 生成。")
        sys.exit(1)
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_structure_file(project_dir: str):
    output_path = os.path.join(project_dir, "project_structure.txt")
    lines = []

    def walk(dir_path, prefix=""):
        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            return
        entries = [e for e in entries if e not in ('__pycache__', '.git', '.idea') and not e.endswith('.pyc')]
        for i, entry in enumerate(entries):
            full = os.path.join(dir_path, entry)
            is_dir = os.path.isdir(full)
            is_last = (i == len(entries) - 1)
            connector = "└── " if is_last else "├── "
            next_prefix = prefix + ("    " if is_last else "│   ")
            lines.append(prefix + connector + entry + ("/" if is_dir else ""))
            if is_dir:
                walk(full, next_prefix)

    lines.append(os.path.basename(project_dir) + "/")
    walk(project_dir, "")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"📁 项目结构图已保存至: {output_path}")


def project_exists(project_dir: str) -> bool:
    if not os.path.isdir(project_dir):
        return False
    for root, dirs, files in os.walk(project_dir):
        for file in files:
            if not file.startswith('.') and not file.endswith('.pyc'):
                return True
    return False


def _filter_noise(problems: list) -> list:
    filtered = []
    for p in problems:
        if "类方法无法自动测试" in p:
            continue
        if "动态环境准备完成" in p:
            continue
        if "代码中包含 print()" in p:
            continue
        if "缺少模块" in p:
            continue
        filtered.append(p)
    return filtered


def check_project_health(project_dir: str) -> list:
    """仅语法检查，返回：[(文件路径, [问题描述字符串])]"""
    issues = []
    for root, _, files in os.walk(project_dir):
        for file in files:
            if file.endswith('.py') and not file.startswith('__'):
                filepath = os.path.join(root, file)
                try:
                    py_compile.compile(filepath, doraise=True)
                except py_compile.PyCompileError as e:
                    issues.append((filepath, [f"语法错误: {e}"]))
                except Exception as e:
                    issues.append((filepath, [f"读取失败: {e}"]))
    return issues


def build_single_file_prompt(filepath: str, problems: list, extra_context: str = "") -> str:
    with open(filepath, 'r', encoding='utf-8') as f:
        code = f.read()
    rel_path = os.path.relpath(filepath)
    prompt = (
        f"你是一个 Python 代码优化专家。请修复以下文件中的问题，输出该文件的完整修复后代码。\n\n"
        f"## 文件: {rel_path}\n"
        f"当前代码:\n```python\n{code}\n```\n\n"
        f"检测到的问题:\n"
    )
    for p in problems:
        prompt += f"- {p}\n"
    if extra_context:
        prompt += f"\n补充说明：{extra_context}\n"
    prompt += "\n请只输出修复后的完整代码，使用 ```python ... ``` 包裹，不要包含任何解释。"
    return prompt


def extract_code_from_reply(reply: str) -> str:
    match = re.search(r'```(?:python)?\s*\n(.*?)```', reply, re.DOTALL)
    if match:
        return match.group(1).strip()
    return reply.strip()


def validate_file_syntax_only(filepath: str) -> list:
    try:
        py_compile.compile(filepath, doraise=True)
        return []
    except py_compile.PyCompileError as e:
        return [f"语法错误: {e}"]
    except Exception as e:
        return [f"读取失败: {e}"]


def find_main_script(project_dir: str) -> str:
    candidates = [
        os.path.join(project_dir, "main.py"),
        os.path.join(project_dir, "src", "main.py"),
        os.path.join(project_dir, "book_manager", "main.py"),
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    for root, _, files in os.walk(project_dir):
        if "main.py" in files:
            return os.path.join(root, "main.py")
    return None


def _is_gui_project(project_dir: str) -> bool:
    """检测项目中是否包含 GUI 库的导入"""
    gui_keywords = ['PyQt5', 'PySide6', 'tkinter', 'wx', 'PySimpleGUI']
    for root, _, files in os.walk(project_dir):
        for file in files:
            if file.endswith('.py'):
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                for kw in gui_keywords:
                    if kw in content:
                        return True
    return False


def try_startup(project_dir: str) -> tuple[bool, str]:
    """启动验证，GUI 项目只检查导入，非 GUI 项目正常启动"""
    main_script = find_main_script(project_dir)
    if not main_script:
        return False, "未找到主入口脚本"

    if _is_gui_project(project_dir):
        # 构建一个只检查导入的脚本
        with open(main_script, 'r', encoding='utf-8') as f:
            code = f.read()
        # 提取所有 import 语句，构建测试脚本
        imports = re.findall(r'^\s*(?:from\s+(\S+)\s+import\s+\S+|\bimport\s+(\S+))', code, re.MULTILINE)
        test_script = f"import sys, os\nsys.path.insert(0, {repr(os.path.abspath(project_dir).replace(chr(92), '/'))})\n"
        for imp in imports:
            if imp[0]:
                test_script += f"from {imp[0]} import *  # noqa\n"
            elif imp[1]:
                test_script += f"import {imp[1]}\n"
        # 执行导入测试
        try:
            result = subprocess.run(
                [sys.executable, '-c', test_script],
                capture_output=True, text=True,
                timeout=STARTUP_TIMEOUT,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0:
                return True, ""
            else:
                return False, result.stderr or result.stdout
        except Exception as e:
            return False, str(e)
    else:
        try:
            result = subprocess.run(
                [sys.executable, main_script],
                capture_output=True, text=True,
                timeout=STARTUP_TIMEOUT,
                encoding='utf-8', errors='replace'
            )
            if result.returncode == 0:
                return True, ""
            else:
                return False, result.stderr or result.stdout
        except subprocess.TimeoutExpired:
            return False, f"启动超时 (>{STARTUP_TIMEOUT}s)"
        except Exception as e:
            return False, str(e)


def extract_error_files(error_output: str, project_dir: str) -> list:
    pattern = r'File "(.*?)"'
    matches = re.findall(pattern, error_output)
    project_dir = os.path.normpath(project_dir)
    files = []
    for m in matches:
        if os.path.isabs(m):
            filepath = m
        else:
            filepath = os.path.join(project_dir, m)
        filepath = os.path.normpath(filepath)
        if filepath.lower().startswith(_STDLIB_PREFIX):
            continue
        if not filepath.lower().startswith(project_dir.lower()):
            continue
        if filepath.endswith('.py') and os.path.exists(filepath):
            files.append(filepath)
    seen = set()
    unique_files = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)
    return unique_files


def detect_failures_in_script(script_path: str) -> dict:
    if not os.path.exists(script_path):
        return {}
    cmd = [sys.executable, "main.py", script_path, "-o", os.devnull, "--locate", os.devnull]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              encoding='utf-8', errors='replace')
        output = proc.stdout
        failures = re.findall(r'  - (\w+): (.+)', output)
        if failures:
            return {script_path: [f"运行时错误 - 函数 {f[0]}: {f[1]}" for f in failures]}
    except Exception:
        pass
    return {}


def fix_file(session, filepath: str, problems: list, extra_context: str = "") -> bool:
    backup_path = filepath + ".backup"
    try:
        shutil.copy2(filepath, backup_path)
    except Exception:
        return False

    prompt = build_single_file_prompt(filepath, problems, extra_context)
    reply = session.send(prompt)
    if not reply:
        os.remove(backup_path)
        return False

    fixed_code = extract_code_from_reply(reply)
    if not fixed_code:
        os.remove(backup_path)
        return False

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(fixed_code)
    except Exception:
        os.remove(backup_path)
        return False

    remaining = validate_file_syntax_only(filepath)
    if remaining:
        try:
            shutil.move(backup_path, filepath)
        except Exception:
            pass
        return False
    else:
        print("  ✅ 修复成功（语法检查通过）")
        os.remove(backup_path)
        return True


def apply_local_fixes(project_dir: str) -> bool:
    """
    自动修复常见结构问题，并智能添加项目根到搜索路径（全部使用正斜杠）。
    同时清除文件中已有的反斜杠路径，避免 SyntaxWarning。
    """
    modified = False
    abs_project_dir = os.path.abspath(project_dir).replace('\\', '/')

    for root, _, files in os.walk(project_dir):
        for file in files:
            if not file.endswith('.py') or file.startswith('__'):
                continue
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()

            new_content = content

            # 修复相对导入
            def replace_relative_import(match):
                module = match.group(1)
                dir_path = os.path.dirname(path)
                init_file = os.path.join(dir_path, '__init__.py')
                if os.path.exists(init_file):
                    rel_dir = os.path.relpath(dir_path, abs_project_dir).replace('\\', '/')
                    package_name = rel_dir.replace('/', '.')
                    return f'from {package_name} import'
                else:
                    return f'from {module} import'

            new_content = re.sub(r'from\s+\.(\w+)\s+import', replace_relative_import, new_content)

            # 修复 Final 误用
            new_content = re.sub(r'Final\(Path\(', 'Path(', new_content)
            new_content = re.sub(r':\s*Final\s*=\s*Path\(', ': Final[Path] = Path(', new_content)

            # 彻底清除反斜杠路径：移除包含反斜杠的行并替换所有反斜杠为正斜杠
            lines = new_content.split('\n')
            cleaned_lines = []
            for line in lines:
                # 移除任何包含反斜杠的路径设置行（避免错误残留）
                if re.search(r"""['"]\s*\.\.\s*[/\\]\s*\.\.\s*[/\\]""", line) and 'sys.path' in line:
                    continue
                # 替换所有字符串中的反斜杠为正斜杠
                line = re.sub(r"'([^']*?)\\\\([^']*?)'", r"'\1/\2'", line)
                line = re.sub(r'"([^"]*?)\\\\([^"]*?)"', r'"\1/\2"', line)
                cleaned_lines.append(line)
            new_content = '\n'.join(cleaned_lines)

            # 如果没有路径设置，则添加绝对路径（正斜杠形式）
            if "sys.path.insert" not in new_content:
                insert_code = f"import sys, os\nsys.path.insert(0, '{abs_project_dir}')\n"
                new_content = insert_code + new_content

            if new_content != content:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"🔧 本地修复: {os.path.relpath(path)}")
                modified = True

    # 清理被污染的 main.py
    main_script = find_main_script(project_dir)
    if main_script:
        with open(main_script, 'r', encoding='utf-8') as f:
            code = f.read()
        if re.search(r'[├└│]', code):
            print("🔧 本地修复: 主入口脚本被污染，已替换为标准模板")
            default_entry = f"""import sys, os
sys.path.insert(0, '{abs_project_dir}')

from PyQt5.QtWidgets import QApplication
from book_manager.src.gui import BookManagerApp   # 根据实际模块名调整

def main():
    app = QApplication(sys.argv)
    window = BookManagerApp()
    window.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
"""
            with open(main_script, 'w', encoding='utf-8') as f:
                f.write(default_entry)
            modified = True
        else:
            if "sys.path.insert" not in code:
                with open(main_script, 'w', encoding='utf-8') as f:
                    f.write(f"import sys, os\nsys.path.insert(0, '{abs_project_dir}')\n" + code)
                print(f"🔧 本地修复: 主入口添加路径搜索")
                modified = True

    return modified


def auto_fix_loop(target_dir: str, session) -> bool:
    apply_local_fixes(target_dir)
    for attempt in range(1, MAX_AUTO_FIX_ATTEMPTS + 1):
        print(f"\n🔍 启动验证 (第 {attempt} 次)...")
        success, error_output = try_startup(target_dir)
        if success:
            print("🚀 主程序启动成功！")
            return True
        print("❌ 启动失败")
        if error_output:
            print(f"  错误信息: {error_output.strip()}")
            error_files = extract_error_files(error_output, target_dir)
            print(f"  定位到 {len(error_files)} 个项目文件: {[os.path.basename(f) for f in error_files]}")
            if error_files:
                for filepath in error_files:
                    rel_path = os.path.relpath(filepath)
                    print(f"\n🔧 AI 修复文件: {rel_path} ...")
                    failures = detect_failures_in_script(filepath)
                    extra_msg = ("请特别注意：如果文件中使用了相对导入 (from .xxx import ...) "
                                 "或 typing.Final 导致启动失败，请将其改为绝对导入或移除 Final 修饰。")
                    if failures:
                        for f, problems in failures.items():
                            if fix_file(session, f, problems, extra_msg):
                                print(f"  ✅ {rel_path} 已修复")
                            else:
                                print(f"  ❌ {rel_path} 修复失败")
                    else:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            code = f.read()
                        prompt = (f"你是一个 Python 代码优化专家。下面的脚本启动时失败，错误信息如下：\n{error_output}\n\n"
                                  f"请修复该脚本，输出完整代码。\n文件路径: {rel_path}\n"
                                  f"要求：重点解决相对导入问题（将 from .xxx import 改为绝对导入），"
                                  f"修复 typing.Final 用法错误（如 'Final(Path(...))' 改为直接赋值）。\n"
                                  f"当前代码:\n```python\n{code}\n```\n")
                        reply = session.send(prompt)
                        if reply:
                            fixed_code = extract_code_from_reply(reply)
                            if fixed_code:
                                try:
                                    with open(filepath, 'w', encoding='utf-8') as f:
                                        f.write(fixed_code)
                                    print(f"  ✅ {rel_path} 根据启动错误整体修复完成")
                                except Exception:
                                    print(f"  ❌ {rel_path} 写入失败")
                    time.sleep(1)
            else:
                print("  未找到项目内的错误文件，尝试修复主入口脚本...")
                main_script = find_main_script(target_dir)
                if main_script:
                    with open(main_script, 'r', encoding='utf-8') as f:
                        code = f.read()
                    prompt = (f"你是一个 Python 代码优化专家。下面的脚本启动时失败，错误信息如下：\n{error_output}\n\n"
                              f"请修复该脚本，输出完整代码。\n脚本路径: {os.path.relpath(main_script)}\n"
                              f"要求：重点解决相对导入问题，修复 typing.Final 用法错误。\n"
                              f"当前代码:\n```python\n{code}\n```\n")
                    reply = session.send(prompt)
                    if reply:
                        fixed_code = extract_code_from_reply(reply)
                        if fixed_code:
                            try:
                                with open(main_script, 'w', encoding='utf-8') as f:
                                    f.write(fixed_code)
                                print("  主入口脚本已整体修复。")
                            except Exception:
                                pass
        else:
            print("  未捕获到错误输出。")
        time.sleep(2)

    print(f"⚠️ 经过 {MAX_AUTO_FIX_ATTEMPTS} 次尝试，主程序仍无法启动。请人工检查。")
    return False


def main():
    if len(sys.argv) < 2:
        default_task = "task.txt"
        if os.path.isfile(default_task):
            task_file = default_task
            print(f"📄 使用默认任务文件: {default_task}")
        else:
            print("❌ 未指定任务文件，且当前目录下没有 task.txt")
            print("用法: python web_ai_coder.py <任务文件>")
            sys.exit(1)
    else:
        task_file = sys.argv[1]

    task = load_task(task_file)
    target_dir = task.get('目标根目录', './project')
    instruction_lib_path = task.get('指令库路径', '开发指令库.txt')
    instruction_tags = task.get('指令标签', '')
    task_description = task.get('任务描述', '请根据任务描述生成项目')

    if not os.path.exists(instruction_lib_path):
        print(f"❌ 指令库不存在: {instruction_lib_path}")
        sys.exit(1)
    lib = load_instructions(instruction_lib_path)
    print(f"✅ 已加载 {len(lib)} 条指令")

    web_config = load_web_config()
    print("🌐 正在初始化 DeepSeek 网页会话...")
    session = DeepSeekWebSender(web_config)
    session.new_chat_with_expert()
    print("✅ 专家模式对话已就绪")

    if project_exists(target_dir):
        print(f"\n📂 项目目录 '{target_dir}' 已存在，跳过构建，直接进入打磨与验证。")
    else:
        print("\n🆕 开始新建项目...")

    # 提前应用本地修复
    if os.path.isdir(target_dir):
        print("🔧 应用本地结构修复（路径修正、导入修复）...")
        apply_local_fixes(target_dir)

    if project_exists(target_dir):
        print("\n🔍 执行语法级打磨...")
        issues = check_project_health(target_dir)
        if not issues:
            print("✅ 所有文件语法正确，无需打磨。")
        else:
            print(f"⚠️ 发现 {len(issues)} 个语法问题文件，开始逐文件修复...")
            for filepath, problems in issues:
                rel_path = os.path.relpath(filepath)
                print(f"🔧 正在修复: {rel_path} ...", end="", flush=True)
                if fix_file(session, filepath, problems):
                    print()
                else:
                    print(" 失败（已回滚）")
                time.sleep(1)

        if auto_fix_loop(target_dir, session):
            print("🎉 项目启动验证通过！")
        else:
            print("⚠️ 项目未能通过启动验证，请手动修复。")

        if os.path.isdir(target_dir):
            print("\n📁 正在生成项目结构图...")
            generate_structure_file(target_dir)
        return

    # 新建项目流程
    print("\n📝 正在向 AI 请求生成操作计划...")
    summary = generate_summary(lib)
    plan_prompt = f"""
你是一个智能项目构建规划师。根据下面的任务描述和可用的规则库，请生成一个详细的操作计划（JSON 格式）。

任务描述：
{task_description}
{instruction_tags}

可用的规则库（编号: 简要说明）：
{summary}

要求：
1. 返回一个 JSON 对象，格式如下：
{{
  "plan_name": "计划名称",
  "steps": [
    {{
      "step_id": 1,
      "action": "create_directory 或 create_file 或 install_package 或 run_command 或 patch_function 或 run_health_check",
      "params": {{ ... 具体参数 ... }},
      "reason": "执行理由"
    }}
  ]
}}
2. 对于需要生成代码的步骤，将 "content_from_ai" 设为 true，并在 "prompt_hint" 中说明代码需求。
3. 计划应包含安装依赖、创建文件、运行测试等步骤，尽量原子化。
4. 此外，请在 JSON 中添加一个 "suggested_rules" 字段，值为你认为需要应用的规则编号列表（如 ["#8","#15","#21"]）。
5. **重要：所有生成的 Python 代码必须使用绝对导入（例如 from package.module import ...），禁止使用相对导入（例如 from .module import ...）。禁止使用 Final(Path(...)) 这种错误写法，请直接赋值或使用 Final[Path] 类型注解。**
6. 只输出 JSON，不要任何额外说明。
"""

    plan_json = None
    for attempt in range(MAX_PLAN_RETRIES):
        reply = session.send(plan_prompt)
        if not reply:
            print(f"⚠️ 第 {attempt+1} 次未获取到回复")
            continue
        try:
            json_match = re.search(r'\{.*\}', reply, re.DOTALL)
            if json_match:
                plan_json = json.loads(json_match.group())
            else:
                plan_json = json.loads(reply)
            break
        except Exception as e:
            print(f"⚠️ 第 {attempt+1} 次解析计划失败: {e}")
            plan_prompt += "\n上次返回的不是有效 JSON，请严格按照 JSON 格式重新输出。"
            time.sleep(2)
    else:
        print("❌ 无法生成有效的操作计划，退出。")
        sys.exit(1)

    suggested_rules = plan_json.get('suggested_rules', ['#15', '#70'])
    print(f"✅ AI 建议规则: {suggested_rules}")
    print("\n📋 生成的操作计划：")
    print(json.dumps(plan_json, indent=2, ensure_ascii=False))

    if CONFIRM_BEFORE_EXECUTE:
        input("\n按回车键开始执行计划...")

    executor = PlanExecutor(session, lib, suggested_rules, target_dir)
    success = executor.execute(json.dumps(plan_json))

    if success:
        print("\n🔧 应用本地智能修复（相对导入/Final 误用 + 路径修正）...")
        apply_local_fixes(target_dir)

        print("\n🔁 进入打磨与验证阶段...")
        issues = check_project_health(target_dir)
        if issues:
            print(f"⚠️ 发现 {len(issues)} 个语法问题，进行修复...")
            for filepath, problems in issues:
                rel_path = os.path.relpath(filepath)
                print(f"🔧 正在修复: {rel_path} ...", end="", flush=True)
                if fix_file(session, filepath, problems):
                    print()
                else:
                    print(" 失败（已回滚）")
                time.sleep(1)

        if auto_fix_loop(target_dir, session):
            print("🎉 项目启动验证通过！")
        else:
            print("⚠️ 项目未能通过启动验证，请手动修复。")
    else:
        print("⚠️ 计划执行未完全成功，请检查项目状态。")

    if os.path.isdir(target_dir):
        print("\n📁 正在生成项目结构图...")
        generate_structure_file(target_dir)


if __name__ == '__main__':
    main()