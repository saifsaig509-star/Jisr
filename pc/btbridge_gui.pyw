# Double-click to start Jisr without a console window (also the entry point of the .exe build).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from btbridge.__main__ import main

main()
