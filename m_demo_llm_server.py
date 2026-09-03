"""
本地演示用 mock OpenAI 兼容 LLM 服务（临时工具，仅本机演示，可删除）

用法: python m_demo_llm_server.py   # 监听 127.0.0.1:8002
作用: 按 prompt 特征路由到确定性分支，让无 Key 环境也能跑通完整对话链路:
  - "只输出纯JSON"            -> 报修信息抽取（返回契约 JSON）
  - "可用的任务类别"           -> 任务分类（appointment/query/complaint/other）
  - "只回答YES或NO"           -> 咨询域判定（YES）
  - "客户回访消息"             -> 回访话术
  - 其他                       -> 通用文本
注意: 无弯引号；纯 ASCII 引号；中文正则经测试通过。
"""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta

HOST, PORT = "127.0.0.1", 8002

ENGINEERS = ["张建国", "李卫东", "王海涛", "刘志强", "陈国华", "赵文斌", "孙建军", "周永康"]
PRODUCT_WORDS = ["空调", "冰箱", "洗衣机", "热水器", "净水器", "烟灶"]
PRODUCT_SYNONYMS = {"油烟机": "烟灶", "燃气灶": "烟灶", "烟机": "烟灶", "冰柜": "冰箱", "中央空调": "空调", "柜机": "空调"}
FAULT_WORDS = ["不制冷", "不制热", "漏水", "异响", "打不着火", "打不着", "不点火", "不工作", "不启动",
               "不转动", "不加热", "不通电", "不化霜", "不保鲜", "不出水", "排水不畅", "排烟不畅",
               "噪音大", "坏了", "故障", "跳闸", "有异味"]
POSITIVE_WORDS = {"是", "好", "可以", "行", "确定", "对的", "没错", "好的", "对"}
NEGATIVE_WORDS = {"不", "不要", "不行", "不用", "别", "不需要", "算了"}
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
REGION_HINT = ["区", "路", "街", "苑", "园", "村", "镇", "小区", "家园", "大厦", "公寓", "别墅", "楼"]
DAY_OFFSET = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2}


def _now():
    return datetime.now() + timedelta(hours=8)


def parse_start_time(usr: str):
    """解析 今天/明天/后天 + 上午/下午/晚上 + X点(半) -> YYYY-MM-DD HH:MM"""
    m = re.search(r"(今天|今日|明天|明日|后天|今|明)?(上午|中午|下午|晚上)?([0-9]{1,2}|[一二三四五六七八九十]{1,2})点(半|整|30)?", usr)
    if not m:
        return None
    day_word, period, hour_raw, half = m.group(1), m.group(2), m.group(3), m.group(4)
    offset = DAY_OFFSET.get(day_word, 0)
    hour = int(hour_raw) if hour_raw.isdigit() else CN_NUM.get(hour_raw)
    if hour is None:
        return None
    if period in ("下午", "晚上") and hour < 12:
        hour += 12
    if period == "中午" and hour < 12:
        hour += 12
    if half == "半":
        minute = 30
    else:
        minute = 0
    base = (_now() + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return base.strftime("%Y-%m-%d %H:%M")


def _clean_phrase(usr: str):
    for w in ["麻烦", "请帮我", "帮我", "我想要", "我想", "请", "需要", "要", "安排", "上门", "报修",
              "预约", "维修", "修理", "修一下", "师傅", "工程师", "指定", "师傅上门", "想", "我的手机号",
              "手机号", "联系电话", "电话", "地址是", "地址：", "地址:", "地址", "您好", "你好", "在",
              "麻烦您", "一下", "谢谢", "感谢", "给", "叫", "找", "让我"]:
        usr = usr.replace(w, "")
    return usr.strip("，。,.！! 的：")


def parse_fields(usr: str):
    """把用户本轮输入映射为报修抽取 JSON 契约（演示级确定性抽取）"""
    product, fault, address, phone, start_time, engineer_name, confirmation = "未知", "未知", "未知", "未知", "未知", "未知", "未知"
    stripped = usr

    for synonym, canonical in PRODUCT_SYNONYMS.items():
        if synonym in usr:
            product = canonical
            stripped = stripped.replace(synonym, canonical)
            break
    else:
        for w in PRODUCT_WORDS:
            if w in usr:
                product = w
                break

    for w in sorted(FAULT_WORDS, key=len, reverse=True):
        if w in usr:
            fault = w
            stripped = stripped.replace(w, "")
            break

    m = re.search(r"1\d{10}", usr)
    if m:
        phone = m.group(0)
        stripped = stripped.replace(phone, "")

    for name in ENGINEERS:
        if name in usr:
            engineer_name = name
            stripped = stripped.replace(name, "")
            break

    tm = parse_start_time(usr)
    if tm:
        start_time = tm
        m2 = re.search(r"(今天|今日|明天|明日|后天|今|明)?(上午|中午|下午|晚上)?([0-9]{1,2}|[一二三四五六七八九十]{1,2})点(半|整|30)?", usr)
        if m2:
            stripped = stripped.replace(m2.group(0), "")

    # confirmation 优先（简短回复）
    compact = re.sub(r"[\s，。,.！!？?、：:]", "", usr)
    if 0 < len(compact) <= 6:
        if compact in POSITIVE_WORDS:
            confirmation = compact
        elif compact in NEGATIVE_WORDS:
            confirmation = compact

    # 地址：先按地址形态正则，失败则走清洗
    addr_pat = re.search(r"([一-龥]{2,6}(?:区|镇|县))([一-龥A-Za-z0-9]{2,18})", stripped)
    if addr_pat:
        address = addr_pat.group(1) + addr_pat.group(2)
    else:
        tail = _clean_phrase(stripped)
        tail = tail.strip("：:，,。. ")
        if len(tail) >= 4 and any(h in tail for h in REGION_HINT):
            address = tail

    # unrelated：明显闲聊
    if any(w in usr for w in ["天气", "股票", "新闻", "音乐", "你好呀", "哈哈", "在吗", "讲讲", "笑话", "聊天"]):
        unrelated = True
    else:
        unrelated = False

    missing = []
    for key, val in [("product_type", product), ("fault_desc", fault), ("address", address),
                     ("phone", phone), ("start_time", start_time)]:
        if val == "未知":
            missing.append(key)
    return {
        "product_type": product, "fault_desc": fault, "address": address, "phone": phone,
        "start_time": start_time, "engineer_name": engineer_name, "confirmation": confirmation,
        "info_complete": not missing, "unrelated": unrelated, "missing_info": missing,
    }


def classify_task(task: str):
    t = task
    if any(w in t for w in ["投诉", "不满", "转人工", "人工客服", "找负责人", "态度差", "要求处理", "投诉电话"]):
        return "complaint"
    if re.search(r"(报修单|工单|单号)\s*AX|进度|保修|在保|超保|质保|出保|订单|收费|费用|退换|退货|延保", t):
        return "query"
    if any(w in t for w in ["报修", "预约", "上门", "师傅", "工程师", "维修", "修一下", "安排人"]):
        return "appointment"
    if any(w in t for w in PRODUCT_WORDS) and any(w in t for w in FAULT_WORDS):
        return "appointment"
    return "other"


def _recommend_reply(text: str) -> str:
    rec = re.search(r"- 姓名：(\S+)", text)
    rec_name = rec.group(1) if rec else "替代工程师"
    orig = re.search(r"指定工程师([一-龥]{2,4})上门", text)
    orig_name = orig.group(1) if orig else "原工程师"
    prod = re.search(r"用户报修(.+?)[，,]指定", text)
    product = prod.group(1) if prod else "该产品"
    return (f"抱歉，{orig_name}工程师在您期望的时间段没有档期。不过{rec_name}工程师同样擅长{product}维修"
            f"且该时段可上门，请问是否为您安排{rec_name}工程师上门呢？")


def route(text: str) -> str:
    if "只输出纯JSON" in text:
        line = ""
        for raw in text.splitlines():
            if "用户输入：" in raw:
                line = raw.split("用户输入：", 1)[1].strip()
                break
        return json.dumps(parse_fields(line), ensure_ascii=False)
    if "可用的任务类别" in text:
        task_part = text
        if "任务内容：" in text:
            task_part = text.split("任务内容：", 1)[1].strip()
        return classify_task(task_part)
    if "只回答YES或NO" in text:
        return "YES"
    if "客户回访消息" in text:
        return ("王女士您好，感谢您选择安居家电。您的洗衣机保养期已到，建议安排一次深度清洗保养，"
                "可以延长机器使用寿命。工程师张建国明天上午或下午均有空闲时间，如需预约请回复确认，谢谢！")
    if "简洁专业的推荐话术" in text:
        return _recommend_reply(text)
    if "生成一段专业、友好的回复" in text:
        return "好的，我理解您的选择。您可以改约其他上门时间段，或重新指定工程师，请问您希望怎么安排？"
    return "这是演示环境（mock）自动回复：您的咨询已记录，客服稍后与您确认。"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, content: str, stream: bool):
        if stream:
            body = b"".join([
                b"data: " + json.dumps({
                    "id": "chatcmpl-demo", "object": "chat.completion.chunk", "model": "mock",
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}}],
                }, ensure_ascii=False).encode("utf-8") + b"\n\n",
                b"data: [DONE]\n\n",
            ])
        else:
            body = json.dumps({
                "id": "chatcmpl-demo", "object": "chat.completion", "model": "mock",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
            }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        messages = payload.get("messages", [])
        text = ""
        for msg in messages:
            if msg.get("role") == "user":
                text = str(msg.get("content", ""))
        stream = bool(payload.get("stream"))
        reply = route(text)
        self._send_json(reply, stream)


if __name__ == "__main__":
    print(f"mock LLM 服务已启动: http://{HOST}:{PORT}/v1/chat/completions")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
