import win32gui

def find_hwnd_by_title_substring(substr: str):
    substr = (substr or "").lower()
    matches = []

    def enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd) or ""
            if substr and substr in title.lower():
                matches.append((hwnd, title))

    win32gui.EnumWindows(enum_handler, None)
    return matches[0] if matches else (None, None)
