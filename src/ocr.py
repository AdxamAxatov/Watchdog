import cv2
import numpy as np
import pytesseract
import os
import sys


def runtime_root():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

tesseract_path = os.path.join(
    runtime_root(),
    "third_party",
    "Tesseract-OCR",
    "tesseract.exe"
)

if os.path.exists(tesseract_path):
    pytesseract.pytesseract.tesseract_cmd = tesseract_path
else:
    raise FileNotFoundError(f"Tesseract not found at: {tesseract_path}")



def preprocess_for_log(img_bgr: np.ndarray, debug_dir: str | None = None) -> np.ndarray:
    """
    Preprocess a dark UI logbox image for OCR.
    Returns a binary image suitable for Tesseract.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    if debug_dir:
        cv2.imwrite(f"{debug_dir}/step1_gray.png", gray)

    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    if debug_dir:
        cv2.imwrite(f"{debug_dir}/step2_upscaled.png", gray)

    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    if debug_dir:
        cv2.imwrite(f"{debug_dir}/step3_blur.png", gray)

    th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    if debug_dir:
        cv2.imwrite(f"{debug_dir}/step4_thresh.png", th)

    return th


def ocr_log_text(img_bgr: np.ndarray, debug_dir: str | None = None) -> str:
    processed = preprocess_for_log(img_bgr, debug_dir=debug_dir)

    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        cv2.imwrite(f"{debug_dir}/ocr_debug.png", processed)

    text = pytesseract.image_to_string(
        processed,
        config="--psm 6 -c preserve_interword_spaces=1"
    )
    return text.strip()

