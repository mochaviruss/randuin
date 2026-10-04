# Randuin · detección de fraude para Kipu Pagos

ITY1102 Arquitectura de Sistemas IA · EP2 · Equipo 07 (Ian Villalobos, Maximiliano Rodríguez, George Castillo)

Sistema que evalúa cada transacción y devuelve **aprobar**, **revisar** o **bloquear** con las razones que la
explican, a partir del diseño arc42 de la EP1 (caso 3, Kipu Pagos SpA).

## Cómo trabajamos

- `main` protegida: solo recibe PR desde `dev`.
- `dev`: integración. Cada pieza llega con un PR desde `feat/<pieza>`, revisado por otro integrante.
- El pipeline (`.github/workflows/ci.yml`) prueba y construye en cada PR y publica en GHCR al integrar en `main`.

## Levantar el sistema

```bash
docker compose -p equipo07 pull
docker compose -p equipo07 up -d
```
