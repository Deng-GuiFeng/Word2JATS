"""预览专用 WMF 解码；可选的系统解码器不可用时不改变原始下载文件。"""
from __future__ import annotations
import ctypes
import ctypes.util


def wmf_preview_png(path):
    libraries = [ctypes.util.find_library(name) for name in ("gdk_pixbuf-2.0", "gobject-2.0", "glib-2.0")]
    if not all(libraries):
        return None
    pixbuf, objects, glib = [ctypes.CDLL(name) for name in libraries]
    ptr = ctypes.c_void_p
    pixbuf.gdk_pixbuf_new_from_file_at_scale.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                                                       ctypes.c_int, ctypes.POINTER(ptr)]
    pixbuf.gdk_pixbuf_new_from_file_at_scale.restype = ptr
    pixbuf.gdk_pixbuf_save_to_bufferv.argtypes = [ptr, ctypes.POINTER(ptr), ctypes.POINTER(ctypes.c_size_t),
                                                ctypes.c_char_p, ptr, ptr, ctypes.POINTER(ptr)]
    pixbuf.gdk_pixbuf_save_to_bufferv.restype = ctypes.c_int
    objects.g_object_unref.argtypes = [ptr]
    glib.g_free.argtypes = [ptr]
    glib.g_error_free.argtypes = [ptr]
    error, buffer = ptr(), ptr()
    size = ctypes.c_size_t()
    # 预览尺寸有界；图中文字与图形来自 WMF 解码，不做识别或内容重写。
    handle = pixbuf.gdk_pixbuf_new_from_file_at_scale(str(path).encode(), 1800, 1200, 1, ctypes.byref(error))
    try:
        if not handle:
            return None
        success = pixbuf.gdk_pixbuf_save_to_bufferv(handle, ctypes.byref(buffer), ctypes.byref(size),
                                                   b"png", None, None, ctypes.byref(error))
        return ctypes.string_at(buffer, size.value) if success else None
    finally:
        if handle:
            objects.g_object_unref(handle)
        if buffer:
            glib.g_free(buffer)
        if error:
            glib.g_error_free(error)
