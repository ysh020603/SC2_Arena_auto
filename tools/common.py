import time
import sys
import signal
import threading


def timer(func):
    def wrapper(*args, **kwargs):
        print(f"[{func.__name__}] running ...", end=" ", flush=True)
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        elapsed_time = end_time - start_time
        print(f"cost {int(elapsed_time)} seconds.")
        return result

    return wrapper


def pause_for_continue(seconds):
    # 设置信号处理函数，处理Ctrl+C
    def signal_handler(sig, frame):
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # 使用事件来通知主线程用户按下了Enter
    event = threading.Event()

    # 处理用户输入的线程函数
    def wait_for_enter():
        try:
            input()  # 阻塞等待输入，直到用户按下回车
            event.set()
        except:
            # 忽略可能的异常，例如线程被中断
            pass

    # 启动输入线程，并设置为守护线程以便主线程退出时自动结束
    input_thread = threading.Thread(target=wait_for_enter)
    input_thread.daemon = True
    input_thread.start()

    # 主线程等待事件触发或超时
    event.wait(seconds)

    # 函数返回
    return
