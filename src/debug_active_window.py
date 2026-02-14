import win32gui
import time

time.sleep(2)  
hwnd = win32gui.GetForegroundWindow()
title = win32gui.GetWindowText(hwnd)

print("HWND:", hwnd)
print("TITLE:", repr(title))
