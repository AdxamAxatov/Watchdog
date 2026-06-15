from winops import set_dpi_awareness
set_dpi_awareness()
from utils import disable_quick_edit
disable_quick_edit()  # stop a stray console click from freezing the loop

import sys
import os
from watchdog import run_watchdog
from utils import setup_logger

def main():
    logger = setup_logger()
    run_watchdog()

if __name__ == "__main__":
    main()