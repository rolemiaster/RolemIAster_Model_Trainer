# MANUAL DE USO (ES)
## ABAFE Models Trainer

Bienvenido a ABAFE Models Trainer, una herramienta portátil para el entrenamiento fino (Fine-Tuning) de modelos de lenguaje utilizando adaptadores LoRA.

### ¿Qué es esto y para qué sirve?
**Fine-Tuning (Afinamiento):** Imagine que un Modelo de Lenguaje (LLM) es una persona muy culta que ha leído todo internet, pero no sabe realizar una tarea específica de su trabajo (por ejemplo, hablar como un pirata o seguir las reglas de un juego de rol complejo). El Fine-Tuning es el proceso de "entrenar" a esa persona en esa tarea específica sin que olvide todo lo que ya sabe.

**LoRA (Low-Rank Adaptation):** Entrenar un modelo completo es lentísimo y costoso. LoRA es una técnica eficiente que, en lugar de modificar todo el cerebro del modelo, añade "pequeñas capas de conocimiento" (adaptadores) sobre él. Es más rápido, requiere menos memoria gráfica y el resultado es un archivo pequeño que se carga junto al modelo original.

**¿Para qué sirve este programa (Caso RolemIAster)?**
- **Memorización de Reglas:** El objetivo principal es que el modelo "interiorice" el archivo `reglas_base.md` como conocimiento nativo.
- **Optimización de Contexto:** Al tener las reglas en sus pesos neuronales, no necesitamos enviarlas en el prompt del sistema. Esto libera espacio de contexto, reduce el coste por token y acelera la generación.
- **Estabilidad JSON:** Entrenamos para que el modelo nunca falle en la sintaxis de los comandos técnicos (`codex_updates`, `inventory`), garantizando que la lógica del juego no se rompa.

### Conceptos Clave (Parámetros)
No necesita ser ingeniero para usarlos, aquí tiene una guía rápida:
- **Rank (r):** La "capacidad" del adaptador. Un valor bajo (8, 16) es bueno para tareas simples. Un valor alto (64, 128) permite aprender matices complejos pero consume más memoria.
- **Alpha:** Cuánto "pesa" el nuevo aprendizaje sobre el conocimiento original. Generalmente se usa el doble del Rank (ej. Rank 32 -> Alpha 64).
- **Epochs (Épocas):** Cuántas veces verá el modelo sus datos.
  - *Pocas (1-3):* Aprendizaje sutil, evita memorizar respuestas.
  - *Muchas (5+):* Memorización fuerte, riesgo de "sobreajuste" (el modelo se vuelve tonto en todo lo demás).
- **Batch Size:** Cuántos ejemplos procesa a la vez. Mayor es más rápido pero requiere más VRAM.

### Formato del Archivo de Reglas (.md)
Para que el entrenador funcione, su archivo `.md` debe seguir una estructura específica. El programa busca ejemplos de "reglas" o "instrucciones" y su resultado esperado en formato JSON.

**Estructura requerida:**
1. Use cabeceras de nivel 3 con el ID de la regla entre corchetes: `### [NOMBRE_REGLA]`
2. Debajo, incluya un bloque de código con el JSON esperado: ` ```json ... ``` `

**Ejemplo de contenido:**
```markdown
### [ATAQUE_ESPADA]
El usuario realiza un ataque básico.
```json
{
  "accion": "ataque",
  "arma": "espada",
  "daño": 10
}
```

### [CURACION_POCION]
El usuario bebe una poción.
```json
{
  "accion": "curar",
  "item": "pocion_vida",
  "cantidad": 50
}
```
```

### Requisitos Previos
1. **Tarjeta Gráfica (GPU):** Se recomienda una GPU NVIDIA con al menos 8GB de VRAM para modelos pequeños (4B/7B).
2. **Drivers:** Tener los drivers de NVIDIA actualizados.
3. **Archivos:**
   - Un modelo base en formato **GGUF** (ej. `Qwen2.5-7B-Instruct.gguf`) o una carpeta de modelo **HuggingFace**.
   - Un archivo de reglas en formato **Markdown (.md)** que contenga los ejemplos de entrenamiento.

### Pasos para Entrenar
1. **Selección de Archivos y Descarga Automática:**
   - **Archivo de Reglas (.md):** Pulse el botón "Seleccionar" y busque su archivo `.md` con el dataset.
   - **Modelo Base:**
     - Puede seleccionar un archivo local (GGUF o carpeta de modelo HuggingFace descargada previamente).
     - **Descarga Directa desde HuggingFace:** Puede seleccionar uno de los presets del desplegable (ej. `unsloth/Qwen3-4B-Base-bnb-4bit`) o escribir el ID de cualquier repositorio público de HuggingFace. Luego, pulse el botón de **"Descargar"** para bajarlo automáticamente a la carpeta `input_model`.
2. **Configuración Inteligente de Hiperparámetros (Perfiles Auto):**
   - Al seleccionar un modelo (ya sea del desplegable o con el botón), el programa analizará su tamaño e intentará sugerir el perfil óptimo automáticamente:
     - **Micro (< 4B):** (ej. Llama 3.2 1B/3B, Qwen 1.5B). Optimizados con Rank bajo (16) pero Batch Size alto (8) y Learning Rate más agresivo (0.0003).
     - **Small (4B - 8B):** (ej. Qwen 4B/8B, Llama 8B). Equilibrio clásico (Rank 32, Batch 4, LR 0.0002).
     - **Medium (9B - 14B):** (ej. Qwen 14B, Mistral Nemo). Mayor capacidad de absorción de la matriz (Rank 64, LR más conservador 0.0001).
     - **Large (> 14B):** (ej. Qwen 32B). Batch reducido a 1 para evitar errores de memoria (OOM) y LR muy bajo (0.00005) para prevenir sobreajuste.
   - Si acepta la sugerencia en la ventana emergente, los parámetros se ajustarán solos. Si el modelo no es reconocido por el nombre, saltará un diálogo permitiéndole seleccionar la categoría manualmente.
3. **Modo Manual y Motor de Ejecución (Engine):** 
   - Siempre puede rechazar la sugerencia y ajustar los controles manualmente. 
   - **Motor de Ejecución (GGUF/Export):** Le permite elegir qué backend usar para empaquetar el modelo en .gguf final (Auto, CUDA, Vulkan). Útil si su tarjeta gráfica tiene problemas con CUDA.
4. **Cola de Entrenamiento (Nuevo):**
   - Configure los archivos y parámetros para un modelo.
   - Pulse **"Añadir a la Cola"**.
   - Repita el proceso para encolar varios trabajos (incluso con diferentes hiperparámetros).
   - Pulse **"Iniciar Cola"** para procesarlos secuencialmente. El programa gestionará uno tras otro sin requerir atención humana.
   - Si existe una ejecución previa del mismo modelo que haya fallado o se haya pausado (detecta la carpeta `lora_adapters`), el programa preguntará:
     - **Sí:** reutilizar entrenamiento previo (retoma el proceso de exportación a GGUF, ahorrando horas).
     - **No:** borrar lo anterior y entrenar de nuevo desde cero.
5. **Entrenamiento Directo:**
   - Si solo desea entrenar uno, puede pulsar **"Procesar y Entrenar (Directo)"**.
   - Al finalizar el entrenamiento y la conversión, el nuevo modelo .gguf se guardará en la carpeta `output_model`.

### El Banco de Pruebas (Test Bench)
El programa incluye una pestaña de "Banco de Pruebas" vital para comprobar si el modelo resultante ha aprendido bien o si se ha roto (sobreajuste). 
1. **Pestaña Banco de Pruebas:** Seleccione un modelo entrenado (generalmente el de la carpeta `output_model`) y el archivo de reglas (`reglas_base.md`).
2. **Configuración de Modos de Inyección (Prompt Mode):**
   - **Aislado:** Prueba la regla específica únicamente.
   - **Compacto / Completo:** Prueba inyectando todo el archivo de reglas en el prompt, emulando condiciones reales de juego donde el modelo recibe "ruido" o reglas no relacionadas. Puede forzar "Ejecutar todos los modos" para evaluar la robustez frente al contexto masivo.
3. **Umbrales (Thresholds):** Permite configurar el umbral de aprobado de Primera pasada (First pass), Segunda pasada (Final, tras reintento) y el porcentaje máximo tolerable de fallos con puerta de evaluación narrativa.
4. **Ejecución:** Seleccione qué reglas probar de la lista y pulse "Ejecutar". Al finalizar, obtendrá un **Veredicto (Aprobado/Suspendido)** y un informe JSON detallado exportable a la carpeta `output_model/test_bench_reports`.

### Solución de Problemas
- **Error de Memoria (OOM):** Reduzca el `Batch Size` o el `LoRA Rank`.
- **No encuentra Python:** Asegúrese de ejecutar `run_trainer.bat` y no el script `.py` directamente.
- **Error `No package metadata was found for unsloth_zoo`:** Instale la dependencia faltante en el entorno virtual:
  - `python -m pip install --upgrade unsloth_zoo`
  - Si usa este proyecto, lo recomendado es iniciar desde `run_trainer.bat` para que instale dependencias automáticamente.
- **Entrenamiento correcto pero GGUF fallida:**
  - Se reporta como **éxito parcial** y se conserva `lora_adapters` en la carpeta `run_*`.
  - Puede reintentar la exportación sin reentrenar aceptando la reutilización cuando el programa detecte ejecución previa.

---
# USER MANUAL (EN)
## ABAFE Models Trainer

Welcome to ABAFE Models Trainer, a portable tool for Fine-Tuning language models using LoRA adapters.

### What is this and what is it for?
**Fine-Tuning:** Imagine a Large Language Model (LLM) is a highly educated scholar who has read the entire internet but doesn't know how to perform a specific task for your job (e.g., talk like a pirate or follow complex RPG rules). Fine-Tuning is the process of "training" that person on that specific task without them forgetting everything else they know.

**LoRA (Low-Rank Adaptation):** Training a full model is extremely slow and expensive. LoRA is an efficient technique that, instead of modifying the model's entire "brain", adds "small layers of knowledge" (adapters) on top of it. It's faster, requires less video memory, and the result is a small file that loads alongside the original model.

**What is this tool for (Main Objective)?**
The primary goal of this tool is **Performance Optimization (Prompt Reduction)**:
- LLMs require large instruction manuals (like the `reglas_base.md` file) to be injected into every turn to know how to behave or format responses (JSON).
- This creates a **massive prompt** that hogs the VRAM of graphics cards, preventing users with modest hardware from playing the game.
- By training a model with this program, the LLM **"memorizes"** those rules into its neural network. Thus, we can avoid injecting that gigantic text file into the system, drastically reducing VRAM consumption and allowing the game to run on more accessible computers.

### Key Concepts (Parameters)
You don't need to be an engineer to use them, here is a quick guide:
- **Rank (r):** The "capacity" of the adapter. A low value (8, 16) is good for simple tasks. A high value (64, 128) allows learning complex nuances but consumes more memory.
- **Alpha:** How much the new learning "weighs" against original knowledge. Usually double the Rank (e.g., Rank 32 -> Alpha 64).
- **Epochs:** How many times the model will see your data.
  - *Few (1-3):* Subtle learning, prevents memorizing answers.
  - *Many (5+):* Strong memorization, risk of "overfitting" (the model becomes dumb at everything else).
- **Batch Size:** How many examples it processes at once. Higher is faster but requires more VRAM.

### Rules File Format (.md)
For the trainer to work, your `.md` file must follow a specific structure. The program looks for examples of "rules" or "instructions" and their expected result in JSON format.

**Required Structure:**
1. Use level 3 headers with the Rule ID in brackets: `### [RULE_NAME]`
2. Below, include a code block with the expected JSON: ` ```json ... ``` `

**Example content:**
```markdown
### [SWORD_ATTACK]
The user performs a basic attack.
```json
{
  "action": "attack",
  "weapon": "sword",
  "damage": 10
}
```

### [POTION_HEAL]
The user drinks a potion.
```json
{
  "action": "heal",
  "item": "life_potion",
  "amount": 50
}
```
```

### Prerequisites
1. **Graphics Card (GPU):** An NVIDIA GPU with at least 8GB VRAM is recommended for small models (4B/7B).
2. **Drivers:** Keep your NVIDIA drivers updated.
3. **Files:**
   - A base model in **GGUF** format (e.g., `Qwen2.5-7B-Instruct.gguf`) or a **HuggingFace** model folder.
   - A rules file in **Markdown (.md)** format containing the training examples.

### Training Steps
1. **File Selection & Auto-Download:**
   - **Rules File (.md):** Click "Select" and choose your `.md` dataset file.
   - **Base Model:**
     - You can select a local file (GGUF or a previously downloaded HuggingFace model folder).
     - **Direct HuggingFace Download:** You can select one of the presets from the dropdown (e.g. `unsloth/Qwen3-4B-Base-bnb-4bit`) or type the ID of any public HuggingFace repository. Then, click the **"Download"** button to automatically fetch it into the `input_model` folder.
2. **Smart Hyperparameter Configuration (Auto Profiles):**
   - When selecting a model (either from the dropdown or the file explorer), the tool will analyze its size and suggest an optimal profile automatically:
     - **Micro (< 4B):** (e.g. Llama 3.2 1B/3B, Qwen 1.5B). Optimized with low Rank (16) but higher Batch Size (8) and aggressive Learning Rate (0.0003).
     - **Small (4B - 8B):** (e.g. Qwen 4B/8B, Llama 8B). Classic balance (Rank 32, Batch 4, LR 0.0002).
     - **Medium (9B - 14B):** (e.g. Qwen 14B, Mistral Nemo). Higher matrix absorption capacity (Rank 64, conservative LR 0.0001).
     - **Large (> 14B):** (e.g. Qwen 32B). Batch reduced to 1 to avoid Out Of Memory (OOM) errors and very low LR (0.00005) to prevent overfitting.
   - If you accept the popup suggestion, parameters will auto-adjust. If the model isn't recognized by name, a fallback dialog will let you choose the category manually.
3. **Manual Mode & Execution Engine:** 
   - You can always decline the suggestion and adjust controls manually. 
   - **Execution Engine (GGUF/Export):** Allows you to choose which backend to use for packaging the final .gguf model (Auto, CUDA, Vulkan). Useful if your graphics card has issues with CUDA.
4. **Training Queue (New):**
   - Configure files and parameters for a model.
   - Click **"Add to Queue"**.
   - Repeat the process to queue multiple jobs (even with different hyperparameters).
   - Click **"Start Queue"** to process them sequentially without requiring human attention.
   - If a previous run for the same model crashed or paused (it detects the `lora_adapters` folder), the app will ask:
     - **Yes:** reuse previous training (resumes the GGUF export process, saving hours).
     - **No:** delete previous data and train again from scratch.
5. **Direct Training:**
   - If you only want to train one, you can click **"Process and Train (Direct)"**.
   - Upon completion, the new .gguf model will be saved in the `output_model` folder.

### The Test Bench
The program includes a vital "Test Bench" tab to check if the resulting model has learned properly or if it is broken (overfitted).
1. **Test Bench Tab:** Select a trained model (usually the one in the `output_model` folder) and the rules file (`reglas_base.md`).
2. **Prompt Mode Configuration:**
   - **Isolated:** Tests only the specific rule.
   - **Compact / Full:** Tests by injecting the entire rules file into the prompt, emulating real gaming conditions where the model receives "noise" or unrelated rules. You can force "Run all modes" to evaluate robustness against massive context.
3. **Thresholds:** Allows you to configure the passing threshold for First pass, Final pass (after retry), and the maximum tolerable failure percentage with narrative evaluation gate.
4. **Execution:** Select which rules to test from the list and click "Run". Upon completion, you will get a **Verdict (Pass/Fail)** and a detailed JSON report exportable to the `output_model/test_bench_reports` folder.

### Troubleshooting
- **Memory Error (OOM):** Reduce `Batch Size` or `LoRA Rank`.
- **Python not found:** Ensure you run `run_trainer.bat` and not the `.py` script directly.
- **Error `No package metadata was found for unsloth_zoo`:** Install the missing dependency in your virtual environment:
  - `python -m pip install --upgrade unsloth_zoo`
  - For this project, the recommended way is launching with `run_trainer.bat` so dependencies are installed automatically.
- **Training succeeded but GGUF export failed:**
  - This is reported as **partial success**, and `lora_adapters` are preserved in the `run_*` folder.
  - You can retry export without retraining by choosing reuse when a previous run is detected.
