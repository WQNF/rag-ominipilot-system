# -*- coding: utf-8 -*-
"""Milvus 重建完成后，一次性重建两套 BM25 索引并打印对比。

用法（在项目根目录）：
    python valuation/rebuild_all_indexes.py            # 单字段 + BM25F 全量重建
    python valuation/rebuild_all_indexes.py --skip-single

顺序很重要：
1) rebuild_bm25.py   从 Milvus 读干净正文，重建单字段索引（同时也是 BM25F 的输入）
2) build_bm25f.py    用 metadata 建标题字段 + 干净正文建 BM25F
"""
import os
import sys
import time
import argparse
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd):
    print(f"\n$ {' '.join(cmd)}", flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT)
    print(f"  -> exit {r.returncode}，用时 {time.time() - t0:.0f}s", flush=True)
    if r.returncode != 0:
        sys.exit(r.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-single", action="store_true", help="跳过单字段索引")
    a = ap.parse_args()
    py = sys.executable

    if not a.skip_single:
        run([py, "valuation/rebuild_bm25.py"])
    run([py, "valuation/build_bm25f.py", "--q", "300"])
    print("\n完成：bm25_index.pkl（单字段）与 bm25_index_f.pkl（BM25F）都已重建。")
    print("重启后端即可加载；检索器会优先使用 BM25F 索引。")


if __name__ == "__main__":
    main()
