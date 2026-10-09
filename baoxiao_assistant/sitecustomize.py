"""便携版 Python 启动钩子：自动把 D:\\Lib\\site-packages 加进 sys.path
放在项目根目录，运行 python main.py 时会被自动 import（因为 CWD 在 sys.path 里）。"""
import sys, os
_sp = r"D:\Lib\site-packages"
if _sp not in sys.path and os.path.isdir(_sp):
    sys.path.insert(0, _sp)