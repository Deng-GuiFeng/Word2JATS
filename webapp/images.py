"""预览专用 WMF 解码；可选的系统解码器不可用时不改变原始下载文件。"""
from __future__ import annotations
import ctypes
import ctypes.util
import hashlib
import io
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading


_EMF_LOCK = threading.Lock()


def emf_preview_png(blob, cache_dir):
    """用独立 LibreOffice 进程栅格化 EMF；按内容缓存，不覆盖出版原图。

    解码失败仍由调用方提供原图下载。固定格式、独立用户目录、输入体积、
    输出像素和运行时间限制使该可选预览不会占住转换服务。
    """
    if (len(blob)<88 or len(blob)>16*1024*1024
            or blob[:4]!=b'\x01\x00\x00\x00' or blob[40:44]!=b' EMF'):
        return None
    key=hashlib.sha256(blob).hexdigest()
    cache_dir=Path(cache_dir)
    target=cache_dir/(key+'.png')
    executable=shutil.which('libreoffice')
    with _EMF_LOCK:
        if target.is_file():
            return target.read_bytes()
        if not executable:
            return None
        cache_dir.mkdir(parents=True,exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix='emf-',dir=cache_dir) as tmp:
                folder=Path(tmp)
                original=folder/'source.emf'
                original.write_bytes(blob)
                command=[executable,'-env:UserInstallation='+(folder/'profile').as_uri(),
                    '--headless','--convert-to',
                    'png:draw_png_Export:{"PixelWidth":{"type":"long","value":"1800"}}',
                    '--outdir',str(folder),str(original)]
                process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                         start_new_session=True)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL)
                    process.wait()
                    return None
                png=folder/'source.png'
                if process.returncode or not png.is_file() or png.stat().st_size>16*1024*1024:
                    return None
                from PIL import Image, ImageChops
                with Image.open(png) as image:
                    if image.width*image.height>10_000_000:
                        return None
                    rgba=image.convert('RGBA')
                    white=Image.new('RGBA',rgba.size,'white')
                    white.alpha_composite(rgba)
                    rgb=white.convert('RGB')
                    bounds=ImageChops.difference(rgb,Image.new('RGB',rgb.size,'white')).getbbox()
                    if bounds is None:
                        return None
                    # 只裁去完全空白的画布，保留全部可见像素及边缘余量。
                    a,b,c,d=bounds
                    rgb=rgb.crop((max(0,a-12),max(0,b-12),min(rgb.width,c+12),min(rgb.height,d+12)))
                    buffer=io.BytesIO(); rgb.save(buffer,format='PNG')
                output=buffer.getvalue()
                staged=folder/'ready.png'; staged.write_bytes(output)
                os.replace(staged,target)
                return output
        except (OSError,ValueError):
            return None


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
