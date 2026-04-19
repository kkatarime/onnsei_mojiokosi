import subprocess
import sys
import os

pyw = sys.executable.replace("python.exe", "pythonw.exe")
app = os.path.abspath(os.path.join(os.path.dirname(__file__), "launch.pyw"))
dest = os.path.join(os.path.expanduser("~"), "Desktop", "音声文字起こし.lnk")

ps = f"""
$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut('{dest}')
$lnk.TargetPath       = '{pyw}'
$lnk.Arguments        = '"{app}"'
$lnk.WorkingDirectory = '{os.path.dirname(app)}'
$lnk.Description      = 'ローカル音声文字起こしアプリ'
$lnk.Save()
"""

subprocess.run(["powershell", "-Command", ps], check=True)
print(f"デスクトップにショートカットを作成しました: {dest}")
