"""M14 React 版桌面壳：用 pywebview 把React 前端包成独立桌面窗口。

与 desktop_app.py（Gradio 版）并列存在，互不影响：
  desktop_app.py    → Gradio 版窗口（7860）
  desktop_react.py  → 本文件，React 版窗口（7861）

【为什么用方案 B（build 产物 + FastAPI 托管静态文件）】
方案 A 是「npm run dev + 窗口加载 3001」，在 pywebview 里实测失败：WebView2 加载页面后
JS 执行会卡死（Vite 的 HMR WebSocket 在 WebView2 里连不稳，出现
Chrome_WidgetWin_0 unregister 错误），窗口白屏。方案 B 直接用 `npm run build` 的产物，
没有 dev server、没有 HMR、没有 WebSocket，而且**只起一个进程**（本文件自己），
关窗口即全部退出，进程管理最干净。
静态文件挂载写在本文件里（不动 api_server.py 一个字），用它的 app 对象追加路由。

职责（只做编排，不含业务逻辑）：
  1. 检测端口占用（7861 / 3001），被占就明确中文提示并退出，绝不静默起第二套
  2. 首次运行自动 `npm run build`（dist 缺失时）
  3. 在本进程内起 uvicorn 托管 API（复用 api_server.app）+ 前端静态文件
  4. pywebview 开 1400×900 窗口加载 http://127.0.0.1:7861
  5. 窗口关闭 → 停 uvicorn，进程干净退出（无 node / pythonw 残留）

运行：
  python desktop_react.py            # 正常开窗口
  python desktop_react.py --probe    # 自检：开窗 + 验证界面渲染 + 自动关窗
  python desktop_react.py --rebuild  # 强制重新构建前端后再开窗
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
OUTPUT_DIR = ROOT / "output"
LOG_FILE = OUTPUT_DIR / "api_server.log"

API_PORT = 7861
WEB_PORT = 3001          # 方案 B 不用，仅按要求做占用检测
WINDOW_TITLE = "SpatialZ 3D 组织重构智能体"
WINDOW_W, WINDOW_H = 1400, 900

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if os.name == "nt" else 0


# ---------------- 端口 / HTTP 工具 ----------------

def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_busy(port):
            return True
        time.sleep(0.5)
    return False


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def precheck_ports() -> None:
    """启动前检测端口；被占就明确提示并退出，不静默起第二套。"""
    busy = [p for p in (API_PORT, WEB_PORT) if port_busy(p)]
    if not busy:
        return
    detail = "、".join(
        f"{p}（{'API / 界面服务' if p == API_PORT else '前端 dev server'}）"
        for p in busy)
    print("=" * 64, flush=True)
    print(f"✗ 端口已被占用：{detail}", flush=True)
    print("  是不是已经有一个实例在运行了？", flush=True)
    print("  请先关闭已打开的桌面端窗口；若找不到窗口，"
          "在任务管理器里结束 python.exe / node.exe 再试。", flush=True)
    print("=" * 64, flush=True)
    sys.exit(2)


# ---------------- 前端构建 ----------------

def build_frontend() -> bool:
    """dist 缺失时自动构建前端（方案 B 用 build 产物，不用 dev server）。"""
    npm = "npm.cmd" if os.name == "nt" else "npm"
    env = dict(os.environ)
    env["FORCE_COLOR"] = "0"
    print("[构建] 正在执行 npm run build（首次约需十几秒）…", flush=True)
    try:
        p = subprocess.run([npm, "run", "build"], cwd=str(FRONTEND), env=env,
                           capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=420)
    except subprocess.TimeoutExpired:
        print("✗ npm run build 超时", flush=True)
        return False
    if p.returncode != 0:
        print("✗ 前端构建失败，错误输出：", flush=True)
        print((p.stdout or "")[-1500:], flush=True)
        print((p.stderr or "")[-800:], flush=True)
        return False
    print(f"[构建] 完成，产物在 {DIST}", flush=True)
    return True


def ensure_dist(force: bool = False) -> bool:
    if force or not (DIST / "index.html").exists():
        return build_frontend()
    src = FRONTEND / "src"
    if src.exists():                      # 源码比产物新也要重建
        try:
            newest_src = max(p.stat().st_mtime for p in src.rglob("*.ts*"))
            if newest_src > (DIST / "index.html").stat().st_mtime:
                return build_frontend()
        except OSError:
            pass
    return True


# ---------------- 服务（本进程内） ----------------

def serve_api_and_static():
    """复用 api_server.app，追加静态文件挂载，用 uvicorn 在后台线程启动。

    只在**本文件**里追加路由，api_server.py 保持原样。
    """
    import uvicorn
    from fastapi.staticfiles import StaticFiles
    import api_server  # noqa: F401  复用它的 app（它自己 import web_app 起Agent）

    app = api_server.app
    # 追加在 API 路由之后，因此 /api/* 仍然优先匹配
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")

    config = uvicorn.Config(app, host="127.0.0.1", port=API_PORT,
                            log_level="warning")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    t.start()
    return server, t


# ---------------- 自检 ----------------

def run_probe() -> int:
    import json
    import webview

    win = webview.create_window(WINDOW_TITLE, f"http://127.0.0.1:{API_PORT}/",
                                width=WINDOW_W, height=WINDOW_H)
    result: dict = {}

    def probe() -> None:
        for _ in range(15):
            time.sleep(2)
            try:
                n = win.evaluate_js("document.querySelectorAll('button').length")
            except Exception:  # noqa: BLE001
                continue
            if n and n > 3:
                result.update({
                    "title": win.evaluate_js("document.title"),
                    "按钮数": n,
                    "有新建会话按钮": win.evaluate_js(
                        "(document.body.innerText||'').includes('新会话')"),
                    "有3D视图按钮": win.evaluate_js(
                        "(document.body.innerText||'').includes('3D 视图')"),
                    "左栏会话条目": win.evaluate_js(
                        "document.querySelectorAll('aside button').length"),
                })
                break
        try:
            win.destroy()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=probe, daemon=True).start()
    webview.start()

    print("---- 自检结果 ----", flush=True)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    else:
        print("✗ 未能确认界面渲染（evaluate_js 拿不到按钮）", flush=True)
    return 0 if result else 1


# ---------------- 主流程 ----------------

def main() -> int:
    probe_mode = "--probe" in sys.argv
    force_build = "--rebuild" in sys.argv
    precheck_ports()

    server = None
    try:
        if not ensure_dist(force=force_build):
            return 3

        print("[1/2] 启动服务（API + 前端静态文件，同一进程）…", flush=True)
        server, _thread = serve_api_and_static()
        if not wait_port(API_PORT, 40):
            print("✗ 服务 40 秒内没就绪", flush=True)
            return 4
        if not http_ok(f"http://127.0.0.1:{API_PORT}/api/health"):
            print("⚠ /api/health 无响应（若Agent 未配置 LLM，界面会提示）", flush=True)
        print(f"[2/2] 已就绪 → http://127.0.0.1:{API_PORT}/", flush=True)

        if probe_mode:
            return run_probe()

        import webview
        window = webview.create_window(
            WINDOW_TITLE, f"http://127.0.0.1:{API_PORT}/",
            width=WINDOW_W, height=WINDOW_H)
        window.events.loaded += lambda: print("[窗口] 界面已加载完成", flush=True)
        print(f"窗口已打开：{WINDOW_TITLE}（{WINDOW_W}×{WINDOW_H}）", flush=True)
        print("关闭该窗口即退出（没有额外进程会被留下）。", flush=True)
        webview.start()
        return 0
    except KeyboardInterrupt:
        print("\n已中断，正在关闭…", flush=True)
        return 130
    finally:
        if server is not None:
            server.should_exit = True
            print("[清理] 正在停止服务…", flush=True)
            time.sleep(1.2)
        if port_busy(API_PORT):
            print(f"[警告] 端口 {API_PORT} 仍被占用，请检查是否有残留进程", flush=True)
        else:
            print("[清理] 端口已释放，进程即将退出。", flush=True)
        try:
            if OUTPUT_DIR.exists():
                (OUTPUT_DIR / "desktop_react.log").write_text(
                    f"桌面端启动于 {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                    encoding="utf-8")
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
