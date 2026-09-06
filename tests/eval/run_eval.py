"""
EDD 评测主程序（M16）：单步 / 组件 / 端到端三段评测 + 度量报表

隔离策略（零业务代码改动）：
- 把进程 CWD 切入临时沙箱并预建 data/ 目录 —— 各 Service 默认库路径
  'sqlite:///data/smart_appointment.db' 全部相对解析到同一沙箱库，用例互不污染生产；
- 先种入默认工程师与演示订单（王芳 3 单）再评测，保证派单/保修口径有数据；
- config.model_provider.create_embedding_model 钉死抛错 → 全链路语义降级
  （召回/排序退时效+重要度，匹配退原序），显式验证无 Key 环境行为；
- 三个 Agent 模块的 create_chat_model 由 ModelHub 换成脚本替身，LLM 调用
  次数 = 步数代理、输入字符/2 ≈ token 成本（无 Key 可复跑）。

用法：python tests/eval/run_eval.py   （离线；全部通过退出码 0）
"""

import asyncio
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import config.model_provider as model_provider  # noqa: E402  先于业务导入钉死 embedding


def _no_embedding(*_a, **_k):
    raise RuntimeError("EDD 离线评测：禁止调用 embedding 服务（验证语义降级路径）")


model_provider.create_embedding_model = _no_embedding

from tests.eval.base import ModelHub  # noqa: E402
from tests.eval import cases_component, cases_step, cases_e2e  # noqa: E402
from services.engineer_service import EngineerService  # noqa: E402
from services.order_service import OrderService  # noqa: E402


def _seed(sandbox: Path):
    (sandbox / "data").mkdir(parents=True, exist_ok=True)
    os.chdir(sandbox)
    EngineerService().initialize_default_engineers()
    OrderService().initialize_default_orders()


def _p95(sorted_ms) -> float:
    if not sorted_ms:
        return 0.0
    idx = min(len(sorted_ms) - 1, int(round(len(sorted_ms) * 0.95)) - 1)
    return round(sorted_ms[idx], 1)


def _run_cases(cases, desc, sandbox: Path, hub=None):
    """同步跑单步/组件用例；返回 (通过数, 失败明细, 耗时ms列表)"""
    passed, failures, times = 0, [], []
    for name, fn in cases:
        t0 = time.perf_counter()
        try:
            fn(sandbox)
        except Exception as e:
            import traceback
            failures.append((name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))
            times.append((time.perf_counter() - t0) * 1000)
            continue
        times.append((time.perf_counter() - t0) * 1000)
        passed += 1
        print(f"  [PASS] {name}  {times[-1]:7.1f} ms")
    return passed, failures, times


async def _run_e2e(hub, conn):
    """顺序跑端到端场景（每场景重置计数器，独立会话 id 互不影响）"""
    passed, failures, times = 0, [], []
    total_calls = total_chars = 0
    for name, fn in cases_e2e.E2E_CASES:
        hub.reset_counter()
        t0 = time.perf_counter()
        try:
            await fn(hub, conn)
        except Exception as e:
            import traceback
            failures.append((name, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))
            times.append((time.perf_counter() - t0) * 1000)
            print(f"  [FAIL] {name}  （见下方明细）")
            continue
        ms = (time.perf_counter() - t0) * 1000
        calls = hub.counter.calls
        chars = hub.counter.chars
        total_calls += calls
        total_chars += chars
        times.append(ms)
        passed += 1
        print(f"  [PASS] {name}  {ms:7.1f} ms  | LLM调用(≈步数): {calls}  "
              f"| 输入字符: {chars}（≈token {chars // 2}）")
    return passed, failures, times, total_calls, total_chars


def main() -> int:
    sandbox = Path(tempfile.mkdtemp(prefix="edd_eval_"))
    print(f"评测沙箱: {sandbox}（CWD 重定向，默认库相对路径全部指向此处）")
    _seed(sandbox)

    hub = ModelHub()
    hub.install()

    grand = {"passed": 0, "failed": 0}
    failures: list[tuple] = []
    all_rows = []

    def report(group, passed, fails, times, extra=""):
        grand["passed"] += passed
        grand["failed"] += len(fails)
        failures.extend(fails)
        total = passed + len(fails)
        rate = 100.0 * passed / total if total else 0.0
        print(f"\n===== {group}：{passed}/{total} 通过（{rate:.0f}%） {extra} =====")
        for f in fails:
            print(f"\n----- FAIL: {f[0]} -----\n{f[1]}")
        if times:
            times.sort()
            print(f"单例耗时: min {times[0]:.1f} ms | P95 {_p95(times):.1f} ms | max {times[-1]:.1f} ms")

    print("\n########## 一、单步评测（纯函数 / 单一服务确定性断言） ##########")
    p, f, t = _run_cases(cases_step.STEP_CASES, "step", sandbox)
    report("STEP", p, f, t)

    print("\n########## 二、组件评测（多模块真实协作，独立临时库） ##########")
    p, f, t = _run_cases(cases_component.COMPONENT_CASES, "component", sandbox)
    report("COMPONENT", p, f, t)

    print("\n########## 三、端到端评测（真实 Agent 图 + 会话链路，脚本替身 LLM） ##########")
    conn = sqlite3.connect(sandbox / "data" / "smart_appointment.db")
    e2e_times, e2e_calls, e2e_chars = [], 0, 0
    try:
        p, f, t, calls, chars = asyncio.run(_run_e2e(hub, conn))
        report("E2E", p, f, t)
        e2e_times, e2e_calls, e2e_chars = t, calls, chars
    finally:
        conn.close()
        hub.restore()

    # 汇总
    total = grand["passed"] + grand["failed"]
    rate = 100.0 * grand["passed"] / total if total else 0.0
    print(f"\n########## 汇总：{grand['passed']}/{total} 通过（成功率 {rate:.0f}%）##########")
    if e2e_times:
        print(f"端到端耗时: min {min(e2e_times):.1f} ms | "
              f"P95 {_p95(e2e_times):.1f} ms | max {max(e2e_times):.1f} ms")
    print(f"端到端 LLM 调用（步数代理）合计: {e2e_calls} | 输入字符合计: {e2e_chars}"
          f"（≈token {e2e_chars // 2}，离线估算）")

    shutil.rmtree(sandbox, ignore_errors=True)
    return 0 if grand["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
