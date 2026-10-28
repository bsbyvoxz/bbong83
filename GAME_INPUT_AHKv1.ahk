#Requires AutoHotkey v1.1.37.02+
#NoEnv
#SingleInstance Ignore
#Persistent
SendMode Event
SetTitleMatchMode, 2
SetKeyDelay, 80, 80
SetMouseDelay, 85
CoordMode, Mouse, Screen
SetWorkingDir %A_ScriptDir%
bridgeFolder := A_ScriptDir "\bridge_queue"
ackFolder := A_ScriptDir "\bridge_acks"
blockedSession := ""
FileCreateDir, %bridgeFolder%
FileCreateDir, %ackFolder%
SetTimer, BridgeTick, 35
return

; Native stop works even while Python is busy or the foreground changed.
~F11::
~F12::
    FileRead, lease, %A_ScriptDir%\bridge_lease.txt
    fields := StrSplit(Trim(lease), " ")
    blockedSession := fields[1]
    Loop, Files, %bridgeFolder%\*.cmd, F
        FileDelete, %A_LoopFileFullPath%
return

EpochMs() {
    DllCall("GetSystemTimeAsFileTime", "Int64*", ticks)
    return Floor(ticks / 10000 - 11644473600000)
}
WriteAtomic(path, text) {
    tmp := path ".tmp"
    FileDelete, %tmp%
    FileAppend, %text%, %tmp%
    FileMove, %tmp%, %path%, 1
}
CanSend(session) {
    global blockedSession
    FileRead, lease, %A_ScriptDir%\bridge_lease.txt
    fields := StrSplit(Trim(lease, " `t`r`n"), " ")
    return (fields.MaxIndex() = 2 && fields[1] = session && session != blockedSession
        && fields[2] + 0 > EpochMs() && WinActive("Trickster Don Cavalier"))
}
SendChecked(key, session) {
    if !CanSend(session)
        return false
    ; Original game-confirmed SendEvent is preserved.
    SendEvent, % "{" key "}"
    return true
}
BridgeTick:
WriteAtomic(A_ScriptDir "\bridge_ready.txt", EpochMs())
files := ""
Loop, Files, %bridgeFolder%\*.cmd, F
    files .= A_LoopFileName "`n"
Sort, files
; Process ONE composite command; re-sort priorities at the next tick.
Loop, Parse, files, `n, `r
{
    if (A_LoopField = "")
        continue
    path := bridgeFolder "\" A_LoopField
    FileRead, command, %path%
    FileDelete, %path%
    p := StrSplit(Trim(command, " `t`r`n"), " ")
    if (p.MaxIndex() != 7)
        break
    token := p[1], session := p[2], expires := p[3]+0
    action := p[4], key := p[5], x := p[6]+0, y := p[7]+0
    status := "INVALID"
    if (expires <= EpochMs())
        status := "EXPIRED"
    else if !CanSend(session)
        status := "STOPPED"
    else if (action = "WHEEL" || action = "BUFF" || action = "POTION")
    {
        valid := (action = "WHEEL" || (action = "POTION" && (key = "F1" || key = "F2"))
            || (action = "BUFF" && (key = "F1" || key = "F2" || key = "F6" || key = "F7")))
        if (valid)
        {
            slot := (action = "BUFF") ? "2" : "1"
            if SendChecked(slot, session)
            {
                Sleep, 180
                if (action = "WHEEL")
                {
                    if CanSend(session)
                        MouseMove, %x%, %y%, 0
                    Sleep, 45
                    key := "F3"
                }
                if (EpochMs() < expires && SendChecked(key, session))
                {
                    status := "SENT"
                    if (action = "BUFF")
                    {
                        Sleep, 80
                        if !SendChecked("1", session)
                            status := "STOPPED"
                    }
                }
                else
                    status := "STOPPED"
            }
            else
                status := "STOPPED"
        }
    }
    else if (action = "PICK")
    {
        ; Exactly ONE Z. No mouse movement and no double click.
        status := SendChecked("z", session) ? "SENT" : "STOPPED"
    }
    else if (action = "MOVE")
    {
        if CanSend(session)
        {
            MouseMove, %x%, %y%, 0
            if CanSend(session)
            {
                Click
                status := "SENT"
            }
        }
    }
    stamp := EpochMs()
    WriteAtomic(ackFolder "\" session "_" token ".ack", token " " status " " stamp)
    logPath := A_ScriptDir "\bridge_status.txt"
    FileGetSize, logSize, %logPath%
    if (logSize > 1048576)
        FileMove, %logPath%, %logPath%.1, 1
    FileAppend, %stamp% %token% %action% %status%`n, %logPath%
    break
}
return
