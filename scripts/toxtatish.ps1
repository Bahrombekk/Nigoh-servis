# Nigoh — FAQAT SHU O'RNATMANING jarayonlarini to'xtatadi.
#
# Nima uchun kerak: ilgari ishga tushirish fayli `taskkill /IM mediamtx.exe /F`
# qilardi va bu mashinadagi HAR QANDAY MediaMTX'ni o'ldirardi. Bitta
# mashinada ikkita Nigoh o'rnatmasi bo'lsa (masalan eski monolit va yangi
# mikroservis), biri ikkinchisining MediaMTX'ini o'ldirib, o'rniga o'zinikini
# ko'tarardi. Shundan keyin ikkala backend bitta API portiga qarab qolardi va
# har 30 soniyada bir-birining yo'llarini o'chirardi — tomoshabin uchun bu
# "kamera uzilyapti, qotib qolyapti" bo'lib ko'rinardi.
#
#     powershell -NoProfile -File scripts\toxtatish.ps1
#
# Tanlash mezoni — jarayon SHU papkadan ishga tushganmi:
#   * mediamtx.exe / python.exe  — bajariluvchi fayl yo'li yoki buyruq satri;
#   * ffmpeg.exe                 — ota-jarayoni yuqoridagilardan biri
#                                  (ffmpeg tizimdan, PATH orqali chaqiriladi,
#                                  shuning uchun uni yo'l bo'yicha topib
#                                  bo'lmaydi).

param([string]$Root = (Split-Path -Parent $PSScriptRoot))

$Root = (Resolve-Path $Root).Path
$prefix = $Root.TrimEnd('\') + '\'

$all = Get-CimInstance Win32_Process -Filter "Name='mediamtx.exe' or Name='python.exe' or Name='ffmpeg.exe' or Name='ffmpeg.EXE'"

# DIQQAT: taqqoslash turi ANIQ berilishi kerak. `'OrdinalIgnoreCase'` satr
# ko'rinishida berilsa Windows PowerShell 5.1 uni `IndexOf(string, int)`
# overload'iga bog'lashga urinadi va har bir jarayonda xato beradi —
# natijada funksiya hech narsa qaytarmaydi, ya'ni HECH KIM to'xtatilmaydi.
$cmp = [System.StringComparison]::OrdinalIgnoreCase

function Bizniki($p) {
    if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith($prefix, $cmp)) { return $true }
    if ($p.CommandLine -and $p.CommandLine.IndexOf($prefix, $cmp) -ge 0) { return $true }
    return $false
}

# 1-bosqich: yo'l bo'yicha aniq bizniki bo'lganlar.
$oz = @($all | Where-Object { $_.Name -ne 'ffmpeg.exe' -and $_.Name -ne 'ffmpeg.EXE' -and (Bizniki $_) })
$ozPid = [System.Collections.Generic.HashSet[int]]::new()
foreach ($p in $oz) { [void]$ozPid.Add([int]$p.ProcessId) }

# 2-bosqich: ota-jarayoni bizniki bo'lgan ffmpeg'lar. Zanjir ikki pog'onali
# bo'lishi mumkin (main.py -> stream_launcher.py -> ffmpeg), shuning uchun
# to'plam o'zgarmay qolguncha kengaytiriladi.
do {
    $oldin = $ozPid.Count
    foreach ($p in $all) {
        if ($ozPid.Contains([int]$p.ParentProcessId)) { [void]$ozPid.Add([int]$p.ProcessId) }
    }
} while ($ozPid.Count -ne $oldin)

if ($ozPid.Count -eq 0) {
    Write-Output "  [i] Shu papkada ishlab turgan jarayon topilmadi."
    exit 0
}

# Bolalar avval: ota o'lganda yetim ffmpeg qolib ketmasin.
foreach ($id in ($ozPid | Sort-Object -Descending)) {
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
}
Write-Output ("  [*] To'xtatildi: {0} ta jarayon ({1})" -f $ozPid.Count, $Root)
