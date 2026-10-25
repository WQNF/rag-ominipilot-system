"""流式日志模块 — 零侵入捕获 print 输出"""
import queue
import builtins
import threading

_original_print = builtins.print
_log_queue: queue.Queue | None = None
_lock = threading.Lock()


def init_stream():
    global _log_queue
    with _lock:
        _log_queue = queue.Queue()
        builtins.print = _patched_print


def shutdown_stream():
    global _log_queue
    with _lock:
        builtins.print = _original_print
        _log_queue = None


def get_log_queue():
    return _log_queue


def _patched_print(*args, **kwargs):
    _original_print(*args, **kwargs)
    if _log_queue is not None:
        msg = " ".join(str(a) for a in args)
        try:
            _log_queue.put_nowait(msg + '\n')
        except queue.Full:
            pass