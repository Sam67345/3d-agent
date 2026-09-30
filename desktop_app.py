"""桌面壳：用 pywebview 把 web_app 的 Gradio 网页内核包成独立桌面窗口。

运行：python desktop_app.py
复用 web_app.build_app()（web_app.py 也可单独 python web_app.py 跑浏览器版）。
"""

import os

# WebView2 黑屏的常见解法：关闭 GPU 硬件合成（必须在启动 webview 前设置）
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--disable-gpu")

import webview

from web_app import OUTPUT_DIR, demo


def main():
    # 1. 启动 Gradio（不阻塞线程），拿到本地地址
    _, url, _ = demo.queue().launch(
        prevent_thread_lock=True,
        allowed_paths=[str(OUTPUT_DIR)],
        inbrowser=False,
    )
    print(f"Gradio 已启动: {url}")

    try:
        # 2. 打开桌面窗口（不是浏览器标签页）
        window = webview.create_window("3D组织重构智能体", url, width=1400, height=900)
        window.events.loaded += lambda: print("[webview] 页面加载完成")
        # 3. 阻塞直到用户关闭窗口
        webview.start()
    finally:
        # 收尾：关掉 Gradio 服务，不留在后台
        demo.close()
        print("已关闭 Gradio 服务")


if __name__ == "__main__":
    main()
