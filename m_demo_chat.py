"""
本地演示聊天客户端（临时工具，配合 m_demo_llm_server.py 使用，可删除）

依次演示: 完整报修建单 -> 保修查询 -> 工单进度 -> 指定工程师冲突推荐换人 -> 投诉转人工 -> 闲聊兜底
用法: python m_demo_chat.py
"""

import re
import urllib.request
import json

BASE = "http://127.0.0.1:8001/chat/stream"


def chat(message: str) -> str:
    req = urllib.request.Request(
        BASE,
        data=json.dumps({"message": message}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def show(title: str, message: str, raw: str):
    print("\n" + "=" * 72)
    print(f"[{title}] 用户: {message}")
    print("-" * 72)
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("[THOUGHT]"):
            print(f"  {line}")
        elif line.startswith("[REPLY]"):
            print(f"  {line}")
        elif line.startswith("[SIGNAL]"):
            print(f"  [内部信号] {line}")
        elif line.startswith("[ERROR]"):
            print(f"  [错误] {line}")
        else:
            print(f"  {line}")


if __name__ == "__main__":
    steps = [
        ("1. 完整报修（王芳-空调，明天15:00）",
         "空调不制冷，地址：海淀区中关村某小区3号楼，我的手机号13800138000，明天下午3点上门报修"),
        ("2. 保修查询（13800138000 名下空调）",
         "帮我看看13800138000名下的空调还在保修期吗"),
        ("3. 工单进度（用上一步拿到的单号）",
         ""),  # 动态填充
        ("4. 指定工程师冲突（李强-空调异响，同明天15:00 指定张建国）",
         "我要报修空调，启动有异响，地址：海淀区中关村另一小区5号楼，手机13900000000，明天下午3点上门，麻烦指定张建国师傅"),
        ("5. 确认换人",
         "好的"),
        ("6. 投诉转人工（李强）",
         "你们的服务太差了，我要投诉！手机号13900000000"),
        ("7. 闲聊兜底",
         "今天天气怎么样？"),
    ]

    import sys
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    ticket_no = None
    for title, message in steps:
        if int(title[0]) < start:
            continue
        if title.startswith("3."):
            message = f"报修单{ticket_no}现在什么进度了？" if ticket_no else "(无单号，跳过)"
            print(f"\n[3] 单号自动回填: {ticket_no}")
        raw = chat(message)
        show(title, message, raw)
        m = re.search(r"AX\d{10}", raw)
        if m and ticket_no is None:
            ticket_no = m.group(0)
        if ticket_no is None and title.startswith("3."):
            break
    print("\n" + "=" * 72)
