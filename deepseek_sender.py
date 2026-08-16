#!/usr/bin/env python3
"""
DeepSeek 网页自动化发送器（完善版）
- 基于 pyautogui + pyperclip 实现网页操作
- 支持发送消息、复制回复、新建对话、开启专家模式
- 兼容旧版 config.json（含 coordinates 字典和运行参数）
- 智能输入框切换：首次使用空输入框，重试时自动切换到正常输入框
"""

import time
import pyautogui
import pyperclip
from typing import Dict, Any


class DeepSeekWebSender:
    """DeepSeek 网页自动化发送器（智能输入框切换）"""

    def __init__(self, config: Dict[str, Any]):
        # 提取坐标
        coords = config.get("coordinates", {})
        self.coord_empty_input_box = coords.get("empty_input_box")
        self.coord_normal_input_box = coords.get("normal_input_box")
        self.coord_copy_btn = coords.get("copy_btn")
        self.coord_new_chat = coords.get("new_chat_btn")
        self.coord_expert_mode = coords.get("expert_mode")

        # 初始使用空输入框坐标
        self.input_box = self.coord_empty_input_box
        # 标记是否已发送过第一条消息（用于后续自动切换）
        self._has_sent_first = False

        # 运行参数
        self.max_retries = config.get("max_retries_per_note", 3)
        self.poll_interval = config.get("poll_interval", 5)
        self.slow_char_per_sec = config.get("slow_char_per_sec", 20.0)
        self.min_silent = config.get("min_silent", 5)
        self.min_poll = config.get("min_poll", 300)

        print("⚠️ 请确保 DeepSeek 网页已打开并保持在前台")
        time.sleep(3)
        pyautogui.FAILSAFE = True

    # ========== 基础操作 ==========
    def _click(self, pos):
        if pos is None:
            return
        pyautogui.click(pos)
        time.sleep(0.5)

    def _paste_with_fallback(self, text: str):
        try:
            pyperclip.copy(text)
            pyautogui.hotkey('ctrl', 'v')
            return
        except Exception:
            pass
        if text.isascii():
            print("    降级为逐字键入...")
            pyautogui.write(text, interval=0.01)
        else:
            raise RuntimeError("剪贴板不可用且文本包含非ASCII字符")

    # ========== 发送消息 ==========
    def _send_message(self, text: str):
        """清空输入框、粘贴文本、按 Enter 发送"""
        self._click(self.input_box)
        time.sleep(0.3)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.2)
        pyautogui.press('delete')
        time.sleep(0.2)
        self._click(self.input_box)
        time.sleep(0.2)
        self._paste_with_fallback(text)
        pyautogui.press('enter')
        time.sleep(1.5)

    def _try_copy_reply(self) -> str:
        """点击复制按钮，读取剪贴板，返回回复内容"""
        try:
            pyperclip.copy("")
        except Exception:
            pass
        time.sleep(0.2)
        self._click(self.coord_copy_btn)
        time.sleep(0.5)
        try:
            text = pyperclip.paste()
        except Exception:
            return ""
        if text and len(text.strip()) > 10:
            return text
        return ""

    # ========== 动态等待 ==========
    def _wait_and_copy(self, text_length: int) -> str:
        """
        先静默等待 min_silent 秒，然后轮询检测回复。
        确保至少轮询 min_poll 秒。
        """
        silent_wait = self.min_silent
        print(f"    静默等待 {silent_wait}s...", end="", flush=True)
        time.sleep(silent_wait)
        print(" 开始轮询")

        estimated_total = int(text_length / self.slow_char_per_sec) if self.slow_char_per_sec > 0 else 0
        poll_timeout = max(estimated_total - silent_wait, self.min_poll)
        print(f"    轮询最多 {poll_timeout}s（总时限估算 {silent_wait + poll_timeout}s）", flush=True)

        start = time.time()
        while time.time() - start < poll_timeout:
            reply = self._try_copy_reply()
            if reply:
                elapsed = silent_wait + int(time.time() - start)
                print(f"    检测到回复（总耗时 {elapsed}s）")
                return reply
            print(f"    未检测到回复，{self.poll_interval}s 后重试...")
            time.sleep(self.poll_interval)

        print("    超过动态轮询超时，未获取回复")
        return ""

    # ========== 核心发送接口 ==========
    def send(self, content: str) -> str:
        """
        发送消息并获取回复。
        首次发送使用空输入框坐标；
        若失败需要重试，或首次成功后，自动切换至正常输入框坐标（已对话状态）。
        """
        text_length = len(content)
        for attempt in range(1, self.max_retries + 1):
            # 如果不是第一次尝试，且当前还在使用空输入框，则切换到正常输入框
            if attempt > 1 and self.input_box == self.coord_empty_input_box:
                self.input_box = self.coord_normal_input_box
                print("    已切换到正常输入框坐标进行重试...")

            try:
                self._send_message(content)
                print(f"    已发送（第{attempt}次），等待回复...")

                reply = self._wait_and_copy(text_length)
                if reply:
                    # 第一次成功获取回复后，若仍在使用空输入框，则切换至正常输入框
                    if self.input_box == self.coord_empty_input_box:
                        self.input_box = self.coord_normal_input_box
                        print("    首次发送成功，后续将使用正常输入框坐标。")
                    return reply

                print(f"    第{attempt}次未获取回复", end="")
                if attempt < self.max_retries:
                    print("，2秒后重试...")
                    time.sleep(2)
                else:
                    print("，已达最大尝试次数")
            except Exception as e:
                print(f"    异常: {e}，等待5秒后重试...")
                time.sleep(5)
        return ""

    # ========== 新建对话与专家模式 ==========
    def new_chat_with_expert(self):
        """点击新对话按钮，再点击专家模式按钮，重置输入框为空输入框坐标"""
        if not self.coord_new_chat or not self.coord_expert_mode:
            print("    ⚠️ 未配置新对话/专家模式按钮，跳过新建对话")
            return
        self._click(self.coord_new_chat)
        time.sleep(3)
        self._click(self.coord_expert_mode)
        time.sleep(1)
        # 重置为空输入框坐标，并清除首次发送标记
        self.input_box = self.coord_empty_input_box
        self._has_sent_first = False
        self._click(self.input_box)
        time.sleep(0.5)
        print("    🆕 已新建对话并开启专家模式")