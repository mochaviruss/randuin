# Latencia de una prediccion medida desde fuera, pasando por el gateway (D8, pregunta IE8).
# Es el bucle de curl de la clase D8, escrito para PowerShell. Informa el p50 y el p95 (no el promedio).
#
# Uso (con el sistema levantado):
#   .\scripts\latencia.ps1                    # 50 peticiones a POST /api/ia/predict
#   .\scripts\latencia.ps1 -Ruta decision     # 50 decisiones completas: POST /api/decision/evaluar
param(
    [ValidateSet("ia", "decision")] [string] $Ruta = "ia",
    [int] $N = 50
)

$entradaIa = '{"monto_usd":52,"hora_local":3,"intentos_previos_1h":5,"dispositivo_usuarios_distintos":4,"pais_extranjero":true,"canal":"web"}'
$entradaDecision = '{"usuario_id":"KP303245","dispositivo_id":"D-5d47ee1b2e46","comercio_id":"CM40413","monto":12000}'
Set-Content -Path entrada.json -Value ($(if ($Ruta -eq "ia") { $entradaIa } else { $entradaDecision })) -NoNewline

$tiempos = for ($i = 1; $i -le $N; $i++) {
    if ($Ruta -eq "ia") {
        curl.exe -s -o NUL -w "%{time_total}" -X POST http://localhost:8080/api/ia/predict `
            -H "Content-Type: application/json" -H "X-API-Key: randuin-local" -d "@entrada.json"
    } else {
        curl.exe -s -o NUL -w "%{time_total}" -X POST http://localhost:8080/api/decision/evaluar `
            -H "Content-Type: application/json" -d "@entrada.json"
    }
}
Remove-Item entrada.json

$ms = $tiempos | ForEach-Object { [double]::Parse($_, [Globalization.CultureInfo]::InvariantCulture) * 1000 } | Sort-Object
$p50 = $ms[[int][math]::Floor($ms.Count * 0.50)]
$p95 = $ms[[int][math]::Min($ms.Count - 1, [math]::Floor($ms.Count * 0.95))]
"{0} peticiones a /{1}: p50 {2:N1} ms · p95 {3:N1} ms" -f $ms.Count, $Ruta, $p50, $p95
