# Genera carga con un bucle de peticiones (D8, pregunta IE2), para mirar "docker stats" mientras corre.
#
# Uso, con el sistema levantado:
#   Terminal 1:  .\scripts\carga.ps1                      # 60 s, 4 bucles en paralelo, decisiones completas
#   Terminal 2:  docker stats --no-stream                 # repetir varias veces mientras corre la carga
#
# Con el pico de memoria de cada contenedor se calcula su limite: mem_limit = pico x 1,5 (capitulo 7.2).
param(
    [int] $Segundos = 60,
    [int] $Paralelo = 4,
    [ValidateSet("decision", "ia")] [string] $Ruta = "decision"
)

$fin = (Get-Date).AddSeconds($Segundos)
$bucle = {
    param($fin, $ruta)
    $n = 0
    $usuarios = "KP303245", "KP300267", "KP304739", "KP300833", "KP301367"
    $archivo = [IO.Path]::GetTempFileName()      # el JSON va en un archivo: PowerShell 5 rompe las comillas al pasarlo a curl
    while ((Get-Date) -lt $fin) {
        if ($ruta -eq "ia") {
            $cuerpo = '{"monto_usd":' + (Get-Random -Minimum 1 -Maximum 300) + ',"hora_local":' + (Get-Random -Maximum 24) + ',"canal":"web"}'
            Set-Content -Path $archivo -Value $cuerpo -NoNewline
            curl.exe -s -o NUL -X POST http://localhost:8080/api/ia/predict -H "Content-Type: application/json" `
                -H "X-API-Key: randuin-local" -d "@$archivo"
        } else {
            $cuerpo = '{"usuario_id":"' + (Get-Random -InputObject $usuarios) + '","monto":' + (Get-Random -Minimum 1000 -Maximum 200000) + '}'
            Set-Content -Path $archivo -Value $cuerpo -NoNewline
            curl.exe -s -o NUL -X POST http://localhost:8080/api/decision/evaluar -H "Content-Type: application/json" -d "@$archivo"
        }
        $n++
    }
    Remove-Item $archivo
    $n
}

Write-Host "Generando carga durante $Segundos s con $Paralelo bucles... (mira docker stats en otra terminal)"
$trabajos = 1..$Paralelo | ForEach-Object { Start-Job -ScriptBlock $bucle -ArgumentList $fin, $Ruta }
$total = ($trabajos | Wait-Job | Receive-Job | Measure-Object -Sum).Sum
$trabajos | Remove-Job
"{0} peticiones en {1} s = {2:N1} peticiones por segundo" -f $total, $Segundos, ($total / $Segundos)
