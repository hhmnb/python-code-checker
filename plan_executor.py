#!/usr/bin/env python3
"""
计划执行器（最终版，支持 requirements_file）
- 支持 packages / package_name / package / requirements_file 等多种参数格式
- 自动将命令中的 python 替换为当前解释器路径，确保使用正确的虚拟环境
- 其他功能不变
"""

import os
import sys
import json
import subprocess
import time
import re
import ast as python_ast
from instruction_loader import build_norm_text

os.environ['PYTHONUTF8'] = '1'


class PlanExecutor:
    def __init__(self, session, instruction_lib: dict, selected_rules: list,
                 project_root: str = '.', switch_every: int = 500):
        self.session = session
        self.instruction_lib = instruction_lib
        self.norm_text = build_norm_text(selected_rules, instruction_lib)
        self.project_root = os.path.abspath(project_root)
        os.makedirs(self.project_root, exist_ok=True)
        self.context = {}
        self.switch_every = switch_every
        self.step_count = 0

    def execute(self, plan_json_str: str) -> bool:
        plan = json.loads(plan_json_str)
        print(f"📋 开始执行计划: {plan.get('plan_name', '未命名')}")
        for step in plan['steps']:
            if self.switch_every > 0 and self.step_count > 0 and self.step_count % self.switch_every == 0:
                print(f"🔄 已执行 {self.step_count} 步，自动新建对话...")
                try:
                    if hasattr(self.session, 'new_chat_with_expert'):
                        self.session.new_chat_with_expert()
                except Exception as e:
                    print(f"   ⚠️ 自动新建对话失败: {e}")
                time.sleep(2)

            success = self._execute_step(step)
            self.step_count += 1
            if not success:
                print(f"❌ 步骤 {step['step_id']} 失败，中断执行。")
                return False
        print("🎉 计划执行完毕。")
        return True

    def _execute_step(self, step: dict) -> bool:
        step_id = step['step_id']
        action = step['action']
        params = step.get('params', {})
        reason = step.get('reason', '')
        print(f"\n➡️ 步骤 {step_id}: {action} | {reason}")

        # 将步骤顶层的 content_from_ai 和 prompt_hint 合并到 params 中
        if 'content_from_ai' in step and 'content_from_ai' not in params:
            params['content_from_ai'] = step['content_from_ai']
        if 'prompt_hint' in step and 'prompt_hint' not in params:
            params['prompt_hint'] = step['prompt_hint']
        if 'patch_content_from_ai' in step and 'patch_content_from_ai' not in params:
            params['patch_content_from_ai'] = step['patch_content_from_ai']

        if action == 'create_directory':
            return self._do_create_directory(params)
        elif action == 'create_file':
            return self._do_create_file(params)
        elif action == 'install_package':
            return self._do_install_package(params)
        elif action == 'run_command':
            return self._do_run_command(params)
        elif action == 'patch_function':
            return self._do_patch_function(params)
        elif action == 'run_health_check':
            return self._do_health_check(params)
        elif action == 'wait':
            return self._do_wait(params)
        else:
            print(f"❌ 未知动作: {action}")
            return False

    def _do_create_directory(self, params):
        path = os.path.join(self.project_root, params['path'])
        os.makedirs(path, exist_ok=params.get('exist_ok', True))
        print(f"   📁 已创建目录: {path}")

        subdirs = params.get('subdirs', [])
        for sub in subdirs:
            sub_path = os.path.join(path, sub)
            os.makedirs(sub_path, exist_ok=True)
            print(f"   📁 已创建子目录: {sub_path}")
        return True

    def _do_create_file(self, params):
        relative_path = params.get('path') or params.get('file_path')
        if not relative_path:
            print("   ❌ 缺少文件路径参数 (path 或 file_path)")
            return False
        path = os.path.join(self.project_root, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        content_from_ai = params.get('content_from_ai', False)
        if not content_from_ai and 'prompt_hint' in params:
            content_from_ai = True

        # 文件缓存检查（带语法验证）
        if content_from_ai and os.path.exists(path) and os.path.getsize(path) > 0:
            if path.endswith('.py'):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        python_ast.parse(f.read())
                    print(f"   📄 文件已存在且语法有效，跳过生成: {path}")
                    return True
                except SyntaxError:
                    print(f"   ⚠️ 文件存在但语法无效，将重新生成: {path}")
                except Exception:
                    print(f"   ⚠️ 无法验证文件完整性，将重新生成: {path}")
            else:
                print(f"   📄 文件已存在且非空，跳过生成: {path}")
                return True

        if content_from_ai:
            prompt_hint = params.get('prompt_hint', '')
            full_prompt = f"{prompt_hint}\n\n请严格遵循以下规范：\n{self.norm_text}\n\n请只输出代码块，不要包含任何解释。"
            reply = self.session.send(full_prompt)
            if not reply:
                print("   ❌ AI 无响应")
                return False
            code = self._extract_code(reply)
            if not code:
                print("   ❌ AI 未返回有效代码块")
                return False
            content = code
        else:
            content = params.get('content', '')

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"   📄 已创建文件: {path}")
        return True

    def _do_install_package(self, params):
        # 兼容多种参数格式
        packages = []
        # 直接指定的包名
        if 'packages' in params:
            packages = params['packages']
        elif 'package_name' in params:
            packages = [params['package_name']]
        elif 'package' in params:
            packages = [params['package']]
        # 从 requirements.txt 读取
        elif 'requirements_file' in params:
            req_path = os.path.join(self.project_root, params['requirements_file'])
            if not os.path.exists(req_path):
                print(f"   ❌ 依赖文件不存在: {req_path}")
                return False
            with open(req_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        packages.append(line)
        else:
            packages = [params.get('name', '')]

        # 也支持 dev_packages（可选）
        dev_packages = params.get('dev_packages', [])
        all_packages = packages + dev_packages

        if not all_packages or any(not p for p in all_packages):
            print("   ❌ 未指定要安装的包名")
            return False

        success = True
        for package in all_packages:
            try:
                subprocess.run([sys.executable, '-m', 'pip', 'install', package],
                               check=True, encoding='utf-8', errors='replace')
                print(f"   📦 已安装: {package}")
            except subprocess.CalledProcessError as e:
                print(f"   ❌ 安装失败: {package} - {e}")
                success = False
        return success

    def _resolve_cwd(self, cwd: str) -> str:
        if not os.path.isabs(cwd):
            return os.path.join(self.project_root, cwd)
        return cwd

    def _parse_cd_command(self, cmd_str: str):
        match = re.match(r'^cd\s+([^\s&;]+)\s*[&;]+\s*(.*)', cmd_str)
        if match:
            cd_dir = match.group(1)
            remaining = match.group(2).strip()
            return remaining, cd_dir
        return cmd_str, None

    def _do_run_command(self, params):
        cmd_str = params['command']

        actual_cmd, cd_dir = self._parse_cd_command(cmd_str)
        if cd_dir:
            print(f"   🔄 检测到 cd 到 '{cd_dir}'，将作为工作目录执行后续命令")

        # ★ 将命令中的 python 替换为当前解释器路径
        actual_cmd = re.sub(r'\bpython\b', sys.executable.replace('\\', '\\\\'), actual_cmd)

        # 安全白名单
        allowed_prefixes = ['python', 'pip', 'pytest', 'echo', 'git', 'dir', 'ls', 'cd', 'mkdir',
                            sys.executable]
        first_word = actual_cmd.split()[0].lower() if actual_cmd else ''
        if first_word not in allowed_prefixes and first_word != sys.executable.lower():
            print(f"   ⛔ 禁止运行命令: {first_word}")
            return False

        cwd = params.get('cwd') or params.get('working_dir', self.project_root)
        if cd_dir:
            cwd = os.path.join(cwd, cd_dir)
        cwd = self._resolve_cwd(cwd)
        timeout = params.get('timeout', 30)

        print(f"   ⚙️ 执行命令: {actual_cmd} (工作目录: {cwd})")
        try:
            result = subprocess.run(actual_cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                                    timeout=timeout, encoding='utf-8', errors='replace')
        except subprocess.TimeoutExpired:
            print(f"   ❌ 命令超时 ({timeout}s)")
            return False

        expected_code = params.get('expected_exit_code', 0)
        if result.returncode != expected_code:
            print(f"   ❌ 命令失败 (退出码 {result.returncode}): {result.stderr}")
            return False
        print(f"   ✅ 命令执行成功")
        return True

    def _do_patch_function(self, params):
        target_file = os.path.join(self.project_root, params.get('target_file', ''))
        func_name = params.get('function_name', '')
        if not target_file or not func_name:
            print("   ❌ 缺少 target_file 或 function_name")
            return False

        patch_from_ai = params.get('patch_content_from_ai', False) or params.get('content_from_ai', False)
        new_code = params.get('new_code', '')

        if patch_from_ai:
            prompt_hint = params.get('prompt_hint', f'请修复函数 {func_name} 的实现')
            full_prompt = f"{prompt_hint}\n\n请严格遵循以下规范：\n{self.norm_text}\n\n请只输出代码块，不要包含任何解释。"
            reply = self.session.send(full_prompt)
            if not reply:
                print("   ❌ AI 无响应")
                return False
            new_code = self._extract_code(reply)
            if not new_code:
                print("   ❌ AI 未返回有效修复代码")
                return False

        if not new_code:
            print("   ⚠️ 未提供新代码，跳过修复。")
            return False

        tmp_file = f"_fix_{func_name}.py"
        with open(tmp_file, 'w', encoding='utf-8') as f:
            f.write(new_code)
        try:
            subprocess.run([sys.executable, 'fix_function.py', target_file, func_name, tmp_file],
                           check=True, encoding='utf-8', errors='replace')
            os.remove(tmp_file)
            print(f"   🔧 已修复函数: {func_name}")
            return True
        except Exception as e:
            print(f"   ❌ 修复失败: {e}")
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
            return False

    def _do_health_check(self, params):
        command = params.get('command')
        if not command:
            if 'expected' in params or 'expected_output' in params or 'expected_output_contains' in params:
                command = "python main.py"
            else:
                print("   ❌ 健康检查缺少 'command' 参数")
                return False

        actual_cmd, cd_dir = self._parse_cd_command(command)
        if cd_dir:
            print(f"   🔄 检测到 cd 到 '{cd_dir}'，将作为工作目录执行后续命令")

        cwd = params.get('cwd') or params.get('working_dir', self.project_root)
        if cd_dir:
            cwd = os.path.join(cwd, cd_dir)
        cwd = self._resolve_cwd(cwd)
        expected = params.get('expected') or params.get('expected_output', '')
        expected_contains = params.get('expected_output_contains', [])
        timeout = params.get('timeout', 30)

        # 替换 python 为当前解释器
        actual_cmd = re.sub(r'\bpython\b', sys.executable.replace('\\', '\\\\'), actual_cmd)

        allowed_prefixes = ['python', 'pip', 'pytest', 'echo', 'git', 'dir', 'ls', 'cd', 'mkdir',
                            sys.executable]
        first_word = actual_cmd.split()[0].lower() if actual_cmd else ''
        if first_word not in allowed_prefixes and first_word != sys.executable.lower():
            print(f"   ⛔ 禁止执行命令: {first_word}")
            return False

        print(f"   🩺 健康检查: {actual_cmd} (工作目录: {cwd})")
        try:
            result = subprocess.run(actual_cmd, shell=True, cwd=cwd,
                                    capture_output=True, text=True,
                                    timeout=timeout,
                                    encoding='utf-8', errors='replace')
        except subprocess.TimeoutExpired:
            print(f"   ❌ 命令超时 ({timeout}s)")
            return False

        if result.returncode != 0:
            print(f"   ❌ 命令执行失败 (退出码 {result.returncode})")
            if result.stderr:
                print(f"   stderr: {result.stderr.strip()}")
            return False

        if expected:
            actual = result.stdout.strip().replace('\r\n', '\n')
            exp = expected.strip().replace('\r\n', '\n')
            if actual != exp:
                print(f"   ❌ 输出不匹配: 期望 {repr(exp)} 实际 {repr(actual)}")
                return False
            else:
                print("   ✅ 输出匹配预期")
        elif expected_contains:
            output = result.stdout
            missing = [exp for exp in expected_contains if exp not in output]
            if missing:
                print(f"   ❌ 输出缺少期望内容: {missing}")
                return False
            else:
                print("   ✅ 输出包含所有预期内容")
        else:
            print("   ✅ 命令执行成功")
        return True

    def _do_wait(self, params):
        seconds = params.get('seconds', 1)
        print(f"   ⏳ 等待 {seconds} 秒...")
        time.sleep(seconds)
        return True

    def _extract_code(self, reply: str) -> str:
        match = re.search(r'```(?:python)?\s*\n(.*?)```', reply, re.DOTALL)
        if match:
            return match.group(1).strip()
        return reply.strip()