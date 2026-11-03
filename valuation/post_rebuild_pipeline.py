# -*- coding: utf-8 -*-
"""重建后置流水线：等 Milvus 重建结束 → 重建两套 BM25 → 跑 300 题端到端评测。

用法（detached 运行，日志写到 logs/pipeline_*.log）：
    python valuation/post_rebuild_pipeline.py --wait-pid 42804

步骤
----
1. 轮询：等 init_milvus 进程（--wait-pid）退出；再用实体数做二次确认；
2. 备份旧评测报告到 valuation/reports/preheader_<日期>/（不覆盖历史证据）；
3. 依次执行 rebuild_all_indexes.py（单字段 + BM25F）；
4. 执行 evaluate_all.py --start 0 --end 300，生成新的 test_300_report.md。
"""
import os
import sys
import time
import ctypes
import glob
import shutil
import argparse
import datetime
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
REPORTS = os.path.join(ROOT, "valuation", "reports")
PY = sys.executable

STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def pid_alive(pid):
    """用 OpenProcess + GetExitCodeProcess 判断进程是否还活着（比 tasklist 可靠）。"""
    if not pid:
        return False
    try:
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    except Exception:
        return False


def latest_milvus_log_idle_seconds():
    files = sorted(glob.glob(os.path.join(LOG_DIR, "init_milvus_*.log")), key=os.path.getmtime)
    if not files:
        return 0.0
    return time.time() - os.path.getmtime(files[-1])


def entity_count():
    try:
        from pymilvus import connections, Collection
        connections.connect(alias="pipeline", host="localhost", port="19530")
        return Collection("knowledge_base", using="pipeline").num_entities
    except Exception as e:
        log(f"  查询实体数失败：{e}")
        return -1


def run(cmd, tag):
    log(f"$ {' '.join(cmd)}")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT)
    log(f"  [{tag}] exit={r.returncode}，用时 {time.time() - t0:.0f}s")
    if r.returncode != 0:
        log(f"  [FATAL] {tag} 失败，流水线停止")
        sys.exit(r.returncode)


def backup_reports():
    day = datetime.datetime.now().strftime("%Y%m%d")
    dst = os.path.join(REPORTS, f"preheader_{day}")
    os.makedirs(dst, exist_ok=True)
    moved = 0
    for name in ("test_300_result.json", "test_300_report.md"):
        src = os.path.join(REPORTS, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, name))
            moved += 1
    log(f"旧报告已备份到 {dst}（{moved} 个文件）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-pid", type=int, default=0)
    ap.add_argument("--max-wait-hours", type=float, default=6.0)
    ap.add_argument("--skip-single", action="store_true")
    a = ap.parse_args()

    log(f"流水线启动：等待重建进程 pid={a.wait_pid}")
    deadline = time.time() + a.max_wait_hours * 3600
    last = -1
    stable = 0
    while time.time() < deadline:
        alive = pid_alive(a.wait_pid)
        n = entity_count()
        idle = latest_milvus_log_idle_seconds()
        if n != last:
            log(f"  Milvus 实体数 {n}（进程 {'在跑' if alive else '已退出'}，日志空闲 {idle:.0f}s）")
            last = n
            stable = 0
        elif not alive:
            stable += 1
        # 三重确认：进程已退出 + 实体数连续稳定 + 重建日志 5 分钟没更新
        if not alive and stable >= 3 and idle > 300:
            log(f"重建进程已退出，实体数稳定在 {n}，日志空闲 {idle:.0f}s → 进入索引重建")
            break
        time.sleep(60)
    else:
        log("等待超时，按当前状态继续（请人工确认重建是否完成）")

    backup_reports()

    if not a.skip_single:
        run([PY, "valuation/rebuild_bm25.py"], "rebuild_bm25")
    run([PY, "valuation/build_bm25f.py", "--q", "300"], "build_bm25f")

    log("开始 300 题端到端评测（预计 3 小时）")
    run([PY, "valuation/evaluate_all.py", "--start", "0", "--end", "300"], "evaluate_all")

    log("全部完成：")
    for name in ("test_300_report.md", "test_300_result.json"):
        p = os.path.join(REPORTS, name)
        if os.path.exists(p):
            log(f"  {p}  ({os.path.getsize(p)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
