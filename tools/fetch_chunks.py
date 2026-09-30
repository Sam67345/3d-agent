"""分块并发下载器：应对 Zenodo 单连接限速（实测 6 路并发提速 10 倍）。

用法：python tools/fetch_chunks.py <url> <total_size> <chunk_dir> [时限秒]
可反复运行：已完成的块自动跳过。全部完成后手动按序拼接：
    cat <chunk_dir>/chunk_* > <目标文件>
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

CHUNK = 2 * 1024 * 1024
THREADS = 16


def main():
    url, total, chunk_dir = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    budget = float(sys.argv[4]) if len(sys.argv) > 4 else 250.0
    chunk_dir.mkdir(parents=True, exist_ok=True)

    n_chunks = (total + CHUNK - 1) // CHUNK

    def done(i):
        f = chunk_dir / f"chunk_{i:04d}"
        return f.exists() and f.stat().st_size == min(CHUNK, total - i * CHUNK)

    def fetch(i):
        if done(i):
            return True
        start, end = i * CHUNK, min((i + 1) * CHUNK, total) - 1
        tmp = chunk_dir / f"chunk_{i:04d}.part"
        try:
            with requests.get(url, headers={"Range": f"bytes={start}-{end}"},
                              stream=True, timeout=(20, 120)) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for block in r.iter_content(256 * 1024):
                        f.write(block)
            if tmp.stat().st_size == end - start + 1:
                tmp.rename(chunk_dir / f"chunk_{i:04d}")
                return True
        except requests.RequestException:
            pass
        tmp.unlink(missing_ok=True)
        return False

    t0 = time.time()
    completed = sum(done(i) for i in range(n_chunks))
    print(f"起始已完成 {completed}/{n_chunks} 块")
    pending = [i for i in range(n_chunks) if not done(i)]

    # 按波次提交：每波 THREADS 个块，波间检查时限（被杀时已落盘的块保留）
    while pending and time.time() - t0 < budget:
        wave, pending = pending[:THREADS], pending[THREADS:]
        with ThreadPoolExecutor(max_workers=THREADS) as ex:
            list(ex.map(fetch, wave))

    completed = sum(done(i) for i in range(n_chunks))
    pct = completed / n_chunks * 100
    print(f"进度 {completed}/{n_chunks} 块 ({pct:.1f}%), "
          f"共 {completed*CHUNK/1048576:.0f} MB / {total/1048576:.0f} MB")


if __name__ == "__main__":
    main()
