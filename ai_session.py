"""
ai_session.py —— 代码开发专用 AI 会话层
- 基于 DeepSeekWebSender 提供高级交互：发送提示词、获取回复、提取代码/JSON
- 管理对话状态（专家模式、首次发送标志等）
- 所有方法均包含重试和异常处理
"""

import time
import json
import re
from deepseek_sender import DeepSeekWebSender


class AISession:
    """为 Web AI Coder 定制的高级会话接口"""

    def __init__(self, config: dict):
        """
        config: 从 config.json 加载的字典（包含 coordinates 等）
        """
        self.sender = DeepSeekWebSender(config)
        self.expert_mode_active = False

    def start_new_expert_chat(self):
        """新建对话并开启专家模式"""
        if hasattr(self.sender, 'new_chat_with_expert'):
            self.sender.new_chat_with_expert()
        else:
            # 手动实现：点击新对话按钮 -> 等待 -> 点击专家模式
            if self.sender.coord_new_chat:
                self.sender._click(self.sender.coord_new_chat)
                time.sleep(3)
            if self.sender.coord_expert_mode:
                self.sender._click(self.sender.coord_expert_mode)
                time.sleep(1)
            self.sender.input_box = self.sender.coord_empty_input_box
            self.sender._first_send_done = False
        self.expert_mode_active = True
        print("    🆕 已新建专家对话")

    def send_prompt(self, prompt: str, max_retries: int = 2) -> str:
        """
        发送提示词并返回完整回复文本。
        内部自动处理输入框切换（首次发送后使用有对话的输入框）。
        返回空字符串表示最终失败。
        """
        for attempt in range(1, max_retries + 1):
            try:
                # 根据是否首次发送选择输入框
                if not self.sender._first_send_done:
                    self.sender.input_box = self.sender.coord_empty_input_box
                else:
                    self.sender.input_box = self.sender.coord_normal_input_box

                reply = self.sender.send(prompt)
                if reply and len(reply.strip()) > 10:
                    return reply

                print(f"    ⚠️ 第 {attempt} 次未获取有效回复，重试...")
                time.sleep(2)
            except Exception as e:
                print(f"    ❌ 发送异常: {e}")
                time.sleep(3)

        print("    ❌ 达到最大重试次数，返回空")
        return ""

    def send_and_extract_code(self, prompt: str) -> str:
        """
        发送提示词，并从回复中提取 Python 代码块。
        优先提取 ```python ... ```，否则返回整个回复（假设全是代码）。
        """
        reply = self.send_prompt(prompt)
        if not reply:
            return ""
        # 尝试提取代码块
        match = re.search(r'```(?:python)?\s*\n(.*?)```', reply, re.DOTALL)
        if match:
            return match.group(1).strip()
        # 没有代码围栏，返回全部内容
        return reply.strip()

    def send_and_get_json(self, prompt: str) -> dict:
        """
        发送提示词，并解析回复中的 JSON 对象。
        返回解析后的字典，失败返回空字典。
        """
        reply = self.send_prompt(prompt)
        if not reply:
            return {}
        # 尝试找到 JSON 对象（可能被 markdown 包裹）
        json_match = re.search(r'\{.*\}', reply, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                print("    ⚠️ JSON 解析失败，尝试修复...")
        try:
            return json.loads(reply)
        except json.JSONDecodeError as e:
            print(f"    ❌ 无法解析为 JSON: {e}")
            return {}