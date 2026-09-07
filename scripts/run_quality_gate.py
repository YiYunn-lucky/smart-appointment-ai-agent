"""
M16 EDD 质量门禁（本地执行；CI 只文档化，不引入第三方托管）

两道闸门，全部离线（无 LLM/Embedding Key、无网络）：
  1) pytest 单测（确定性断言 + 离线替身，fast 通道回退路径覆盖）
  2) tests/eval/run_eval.py EDD 评测（单步 / 组件 / 端到端 + 成功率/P95/步数/token 度量）

任一闸门失败即非零退出。重复执行结果确定（沙箱临时目录自建自清）。

用法：python scripts/run_quality_gate.py
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(name: str, cmd: list, cwd: Path) -> subprocess.CompletedProcess:
    print(f"\n===== [{name}] {' '.join(cmd)} =====")
    env = dict(__import__("os").environ)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True)
    out = result.stdout.decode("utf-8", "replace")
    err = result.stderr.decode("utf-8", "replace")
    # 只透出关键尾部（进度点阵等冗长输出不进屏幕）
    tail = [ln for ln in (out + err).splitlines() if ln.strip()][-6:]
    print("\n".join(tail))
    return result


def main() -> int:
    # 闸门一：pytest 单测（存量回归门禁）
    p1 = run("闸门一 pytest 单测", [sys.executable, "-m", "pytest", "-q"], ROOT)
    passed_match = re.search(r"(\d+) passed", p1.stdout.decode("utf-8", "replace"))
    unit_passed = int(passed_match.group(1)) if passed_match else 0
    gate1_ok = p1.returncode == 0 and unit_passed > 0
    print(f"[门禁一] pytest：{unit_passed} passed，returncode={p1.returncode}"
          f" → {'通过' if gate1_ok else '失败'}")

    # 闸门二：EDD 评测（单步/组件/端到端 + 度量）
    p2 = run("闸门二 EDD 评测", [sys.executable, "tests/eval/run_eval.py"], ROOT)
    stdout2 = p2.stdout.decode("utf-8", "replace")
    summary = re.search(r"汇总：(\d+)/(\d+) 通过（成功率 ([0-9.]+)%）", stdout2)
    if summary:
        total, success_rate = int(summary.group(2)), summary.group(3)
        print(f"[门禁二] EDD：{summary.group(1)}/{total} 通过"
              f"（成功率 {success_rate}%），returncode={p2.returncode}")
    else:
        print("[门禁二] EDD：未能解析汇总行，returncode=", p2.returncode)
        total, success_rate = 0, 0.0
    gate2_ok = p2.returncode == 0 and total > 0 and float(success_rate) >= 100.0

    print("\n========== 质量门禁结果 ==========")
    print(f"  单元测试  : {'PASS' if gate1_ok else 'FAIL'}（{unit_passed} passed，离线）")
    print(f"  EDD 评测  : {'PASS' if gate2_ok else 'FAIL'}（{total} 例，成功率 {success_rate}%，离线）")
    ok = gate1_ok and gate2_ok
    # 不用 emoji/特殊符号：Windows GBK 控制台会抛 UnicodeEncodeError 导致假红
    print(f"  最终判定  : {'全部通过' if ok else '存在失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
