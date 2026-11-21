' Luna Studio - liga a automacao SEM janela preta (21/11/2026).
' Roda o "Iniciar automacao.bat" escondido: nada aparece na tela nem na barra de
' tarefas. O que o robo faz fica em luna-worker.log. Para desligar: "Parar automacao.bat".
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
For Each f In fso.GetFolder(here).Files
  If LCase(Left(f.Name, 13)) = "iniciar autom" And LCase(fso.GetExtensionName(f.Name)) = "bat" Then
    shell.CurrentDirectory = here
    shell.Run """" & f.Path & """", 0, False
    WScript.Quit
  End If
Next
MsgBox "Nao achei o Iniciar automacao.bat nesta pasta.", 48, "Luna Studio"
