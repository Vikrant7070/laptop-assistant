"""3D Nova's window plumbing: a layered (see-through, always-on-top) Win32 window and drawing into it.

The ctypes structs and function signatures, the window styles, and the blit that puts a premultiplied BGRA image on
screen with UpdateLayeredWindow. Moved out of avatar.py (roadmap R6 step 3); the code is unchanged.
"""
import ctypes

from PIL import Image, ImageChops

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", ctypes.c_ushort), ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


VP = ctypes.c_void_p
user32.GetDC.restype = VP
user32.GetDC.argtypes = [VP]
user32.ReleaseDC.argtypes = [VP, VP]
user32.GetParent.restype = VP
user32.GetParent.argtypes = [VP]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [VP, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [VP, ctypes.c_int, ctypes.c_ssize_t]
user32.UpdateLayeredWindow.argtypes = [VP, VP, ctypes.POINTER(POINT), ctypes.POINTER(SIZE), VP,
                                       ctypes.POINTER(POINT), ctypes.c_uint32, ctypes.POINTER(BLENDFUNCTION), ctypes.c_uint32]
gdi32.CreateCompatibleDC.restype = VP
gdi32.CreateCompatibleDC.argtypes = [VP]
gdi32.CreateDIBSection.restype = VP
gdi32.CreateDIBSection.argtypes = [VP, ctypes.POINTER(BITMAPINFO), ctypes.c_uint, ctypes.POINTER(VP), VP, ctypes.c_uint32]
gdi32.SelectObject.restype = VP
gdi32.SelectObject.argtypes = [VP, VP]
gdi32.DeleteDC.argtypes = [VP]
gdi32.DeleteObject.argtypes = [VP]

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x80, 0x8, 0x08000000
BLEND = BLENDFUNCTION(0, 0, 255, 1)


def premultiplied_bgra(img):
    r, g, b, a = img.split()
    return Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g, a), ImageChops.multiply(r, a), a)).tobytes()


def make_layered(hwnd):
    """Make the window layered, a tool window (no taskbar button), topmost and never focused. Returns its style."""
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
    return style


def set_clickthrough(hwnd, base_style, on):
    """Let clicks pass through her (on) or not."""
    style = base_style | (WS_EX_TRANSPARENT if on else 0)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)


def blit(hwnd, dibs, data, w, h, x, y):
    """Show `data` (premultiplied BGRA, w x h) at screen x, y. `dibs` caches one bitmap per size between calls."""
    if (w, h) not in dibs:
        bmi = BITMAPINFO()
        bmi.bmiHeader = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = VP()
        hbmp = gdi32.CreateDIBSection(None, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if len(dibs) > 20:  # bubble sizes vary; don't hoard bitmaps
            for old_bmp, _ in dibs.values():
                gdi32.DeleteObject(old_bmp)
            dibs.clear()
        dibs[(w, h)] = (hbmp, bits)
    hbmp, bits = dibs[(w, h)]
    ctypes.memmove(bits, data, len(data))
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    old = gdi32.SelectObject(mem, hbmp)
    user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(POINT(x, y)), ctypes.byref(SIZE(w, h)), mem,
                               ctypes.byref(POINT(0, 0)), 0, ctypes.byref(BLEND), 2)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)
