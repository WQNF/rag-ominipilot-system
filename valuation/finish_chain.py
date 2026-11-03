# -*- coding: utf-8 -*-
"""评测链路：从 Milvus 重建单字段索引 → 重建 BM25F → 300 题端到端评测。

用于 `init_milvus.py` 跑完之后的收尾。之所以独立于 post_rebuild_pipeline.py：
后者假设 bm25_index.pkl 还在，而 init_milvus 结束时会把它删掉。

用法（detached，日志写 logs/finish_*.log）：
    python valuation/finish_chain.py
"""
import os
import sys
import time
import datetime
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def run(args, tag):
    log(f"$ {' '.join(args[1:])}")
    t0 = time.time()
    r = subprocess.run(args, cwd=ROOT)
    log(f"  [{tag}] exit={r.returncode}，用时 {time.time() - t0:.0f}s")
    if r.returncode != 0:
        log(f"  [FATAL] {tag} 失败，链路停止")
        sys.exit(r.returncode)


def main():
    log("开始收尾链路：单字段索引 → BM25F → 300 题评测")
    run([PY, "valuation/rebuild_bm25_from_milvus.py"], "rebuild_bm25")
    run([PY, "valuation/build_bm25f.py", "--q", "300"], "build_bm25f")
    log("开始 300 题端到端评测（预计 3 小时）")
    run([PY, "valuation/evaluate_all.py", "--start", "0", "--end", "300"], "evaluate_all")
    log("全部完成")
    rep = os.path.join(ROOT, "valuation", "reports", "test_300_report.md")
    if os.path.exists(rep):
        log(f"报告：{rep}（{os.path.getsize(rep)/1024:.0f} KB）")


if __name__ == "__main__":
    main()
