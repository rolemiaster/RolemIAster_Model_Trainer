****************************************************************************************************
18/03/2026 21:58 - Fix QThread crash y mejoras UI Test Bench - Beta_v005
****************************************************************************************************
- Description:
  Corregido el crash silencioso 'QThread: Destroyed while thread is still running' al finalizar secuencias de Auto-Tuning. Reestructurado el manejo de señales del QThread para usar la señal built-in 'finished' en lugar de emisión personalizada desde run(). Solucionado NameError 'original_text is not defined' en TestBenchEngine. Mejorada la UI para mostrar comparativas A/B/C mediante botón y diálogo con tabla estructurada en lugar de texto largo.

- Changes:
  - Fix QThread crash en Auto-Tuning
  - Fix NameError 'original_text' en test_bench_engine
  - Mejora UI: botón 'Ver Comparativa A/B/C' con tabla estructurada
  - Fix NameError 'running' en update_test_bench_status

****************************************************************************************************
10/03/2026 04:54 - Integración Qwen3.5 Fast Path - Beta_v004
****************************************************************************************************
- Description:
  Compilación exitosa de kernels nativos de Flash Linear Attention en Windows, aumentando drásticamente la velocidad.

****************************************************************************************************
10/03/2026 04:30 - Integración Qwen3.5 Fast Path - Beta_v003
****************************************************************************************************
- Description:
  Compilación exitosa de kernels nativos de Flash Linear Attention en Windows, aumentando drásticamente la velocidad.

****************************************************************************************************
10/03/2026 04:22 - Integración Qwen3.5 Fast Path - Beta_v002
****************************************************************************************************
- Description:
  Compilación exitosa de kernels nativos de Flash Linear Attention en Windows, aumentando drásticamente la velocidad.

****************************************************************************************************
10/03/2026 04:14 - Integración Qwen3.5 Fast Path - Beta_v001
****************************************************************************************************
- Description:
  Compilación exitosa de kernels nativos de Flash Linear Attention en Windows, aumentando drásticamente la velocidad.

