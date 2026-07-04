Option Explicit
' baslat.bat ile ayni sira: py -3, yoksa pythonw, yoksa python — CMD gizli (0).
' py -3w kullanilmiyor; bazi kurulumlarda yoktur, VBS sessizce basarisiz olurdu.

Dim sh, fs, dir, dq, comspec, inner
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
dq = Chr(34)

dir = fs.GetParentFolderName(WScript.ScriptFullName)
If Not fs.FileExists(fs.BuildPath(dir, "tribal_wars_bot.py")) Then
    MsgBox "tribal_wars_bot.py bulunamadi:" & vbCrLf & dir, vbCritical, "Baslat"
    WScript.Quit 1
End If

comspec = sh.ExpandEnvironmentStrings("%ComSpec%")
' /c + Run(..., 0): cmd kisa sure gizli; start "" /B: alt isi arka planda baslatir
inner = "cd /d " & dq & dir & dq & " && start " & dq & dq & " /B py -3 tribal_wars_bot.py || start " & dq & dq & " /B pythonw tribal_wars_bot.py || start " & dq & dq & " /B python tribal_wars_bot.py"
sh.Run comspec & " /c " & dq & inner & dq, 0, False
