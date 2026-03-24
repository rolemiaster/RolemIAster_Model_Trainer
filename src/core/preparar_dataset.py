import json
from core.char_creation_augmenter import augment_character_creation
import random
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

SYSTEM_PROMPT = (
    "Eres un motor narrativo de RPG. Devuelve SIEMPRE un unico objeto JSON valido, "
    "sin markdown ni texto adicional.\n"
    "Contrato base: story_chunk (string), decisions (1-4 objetos con text/action).\n"
    "Campos opcionales permitidos: codex_updates, updates, current_location, action_request, service_offer, interlocutor_id."
)

ALLOWED_TOP_LEVEL_KEYS = {
    "story_chunk",
    "decisions",
    "codex_updates",
    "updates",
    "current_location",
    "action_request",
    "service_offer",
    "interlocutor_id",
}

OBSOLETE_RULE_PREFIXES = ("26",)

ACTION_REQUEST_FIELDS = ("action", "skill", "intent", "difficulty", "type")

FORBIDDEN_DYNAMIC_PLACEHOLDERS = (
    "{user_language}",
    "{target_language}",
    "{setting_key}",
    "{world_name}",
    "{difficulty}",
    "{humor}",
)

FORBIDDEN_I18N_PATTERN = re.compile(r"\{\{i18n:[^}]+\}\}")


# ---------------------------------------------------------------------------
# Pools de contenido para generación de dataset variado
# ---------------------------------------------------------------------------

_STORY_PARTS_EXPLORATION_F = {
    "start": [
        "Avanzas por el sendero empedrado", "La plaza se abre ante ti", "Cruzas el viejo puente de piedra",
        "Un callejón estrecho se bifurca", "Te adentras en la biblioteca abandonada", "Llegas a una encrucijada oscura",
        "El bosque antiguo se espesa", "Una suave brisa te envuelve", "El camino desciende abruptamente", "Exploras las ruinas silenciosas"
    ],
    "mid": [
        " y el aire se llena de un aroma a musgo húmedo", ", bulliciosa y llena de murmullos distantes", " mientras observas las sombras alargarse",
        "; ambas direcciones parecen peligrosas", ", donde el polvo cubre cada superficie", " y un poste de madera indica destinos borrados",
        " y la luz del sol apenas logra filtrarse", " trayendo consigo ecos de viejas canciones", " revelando marcas extrañas en el suelo", " y sientes una extraña presencia mágica"
    ],
    "end": [
        ". Todo permanece en un silencio sepulcral.", ". Mantienes la mano en la empuñadura de tu arma.", ". Una tenue luz titila a lo lejos.",
        ". Sientes que algo te observa desde la oscuridad.", ". El ambiente es inusualmente frío.", ". Te preparas para cualquier eventualidad.",
        ". Un cuervo grazna en lo alto de un árbol.", ". La vegetación aquí parece antinatural.", ". Descubres rastros recientes de paso.", ". El eco de tus pisadas resuena con fuerza."
    ]
}

_STORY_PARTS_EXPLORATION_C = {
    "start": [
        "Avanzas por el callejón de neón", "La plaza corporativa se abre ante ti", "Cruzas la pasarela metálica",
        "Un pasillo de mantenimiento parpadea", "Te adentras en el cibercafé abandonado", "Llegas a un cruce peatonal",
        "Los rascacielos se alzan opresivos", "El zumbido de los servidores te envuelve", "El subnivel desciende bruscamente", "Exploras los conductos de ventilación"
    ],
    "mid": [
        " y el aire huele a ozono y lluvia ácida", ", llena de drones publicitarios zumbando", " mientras el tráfico aéreo fluye incesante",
        "; los cables sueltos sueltan chispas", ", donde pantallas rotas proyectan estática", " y un panel holográfico falla intermitentemente",
        " y la contaminación oculta el cielo nocturno", " trayendo consigo el eco de sirenas lejanas", " revelando manchas de fluido sintético", " y sientes vibrar los implantes de tu cuerpo"
    ],
    "end": [
        ". Las luces estroboscópicas dañan la vista.", ". Mantienes el dedo cerca del gatillo.", ". Un escáner de seguridad barre la zona.",
        ". Sientes la estática erizar tu piel.", ". El ambiente huele a plástico quemado.", ". Te preparas para evadir las cámaras.",
        ". Un dron de limpieza pasa a tu lado.", ". La arquitectura corporativa te resulta opresiva.", ". Descubres casquillos vacíos en el suelo.", ". El eco de la ciudad nunca se detiene."
    ]
}

_STORY_PARTS_COMBAT_F = {
    "start": [
        "El bandido desenvaina su espada", "Esquivas por poco una flecha", "El orco golpea el suelo con su maza",
        "Tu oponente retrocede maldiciendo", "El guardia bloquea tu ataque", "La criatura emite un rugido salvaje",
        "Sientes el choque del acero", "El mercenario intenta flanquearte", "Un cultista alza sus manos oscuras", "El lobo gigante salta hacia ti"
    ],
    "mid": [
        " y se lanza hacia ti con furia homicida", " que silba amenazadoramente junto a tu oído", ", levantando una espesa nube de polvo",
        ", con sangre brotando de un corte reciente", " y contragolpea con brutal precisión", " y carga contra tu posición sin dudar",
        " haciéndote perder el equilibrio por un instante", " mientras otro enemigo ataca de frente", " canalizando una energía de aspecto letal", " mostrando sus fauces babeantes"
    ],
    "end": [
        ". El duelo se vuelve frenético.", ". Recuperas tu postura rápidamente.", ". Buscas una apertura en su defensa.",
        ". El choque de armas resuena en el área.", ". Sabes que un error te costará caro.", ". El enemigo parece no sentir dolor.",
        ". Aprietas los dientes soportando la presión.", ". La adrenalina corre por tus venas.", ". El terreno irregular dificulta el combate.", ". Te preparas para el siguiente intercambio."
    ]
}

_STORY_PARTS_COMBAT_C = {
    "start": [
        "El pandillero desenfunda su arma inteligente", "Esquivas por poco un proyectil", "El ciber-psicópata activa sus servos",
        "Tu oponente retrocede fallando", "El dron de seguridad activa su escudo", "El asesino corporativo se camufla",
        "Sientes el impacto cinético", "El mercenario despliega un micro-misil", "Un hacker enemigo intenta freír tu óptica", "El perro-robot salta hacia ti"
    ],
    "mid": [
        " y dispara una ráfaga a quemarropa", " que revienta el muro de hormigón a tu espalda", ", levantando chispas al golpear el pavimento",
        ", con fluido refrigerante goteando de su brazo", " y te apunta con su láser de fijación", " volviéndose casi invisible en la penumbra",
        " sobrecargando temporalmente tu blindaje", " mientras otro enemigo proporciona fuego de cobertura", " proyectando estática en tu visor retinal", " mostrando sus mandíbulas de titanio"
    ],
    "end": [
        ". El tiroteo se vuelve ensordecedor.", ". Recalibras tu óptica velozmente.", ". Buscas cobertura detrás de un terminal.",
        ". El olor a pólvora lo inunda todo.", ". Sabes que tu ciberware está al límite.", ". El enemigo recarga con fluidez mecánica.",
        ". Inyectas un estimulante para soportar el dolor.", ". La red táctica muestra múltiples amenazas.", ". Los escombros caen a tu alrededor.", ". Te deslizas hacia una mejor posición táctica."
    ]
}

_STORY_PARTS_NPC_SERVICE_F = {
    "start": [
        "El herrero te saluda con un gesto seco", "La boticaria te observa con curiosidad", "El mercader despliega su manta",
        "La armera golpea el yunque", "Un alquimista encorvado murmura", "El tabernero limpia una jarra",
        "El anciano comerciante asiente", "La tejedora levanta la vista", "El joyero examina una piedra", "El erudito cierra su libro"
    ],
    "mid": [
        " y señala su mostrador lleno de armas oxidadas", " desde detrás de frascos burbujeantes de colores", " con mercancías variadas traídas de lejos",
        " una última vez antes de girarse hacia ti", " mientras mezcla sustancias en un caldero humeante", " y te pregunta qué deseas en un tono ronco",
        " rodeado de pergaminos y extrañas reliquias", " mostrando telas imbuidas con sutil magia", " utilizando una lente de cristal tallado", " dispuesto a compartir sus conocimientos"
    ],
    "end": [
        ". El calor del taller es sofocante.", ". El aroma a hierbas inunda el lugar.", ". Sus ojos reflejan astucia comercial.",
        ". Notas el desgaste de sus herramientas.", ". El ambiente parece místico y cargado.", ". La sala bulle de conversaciones ajenas.",
        ". Se cruza de brazos esperando tu oferta.", ". Te evalúa de arriba a abajo rápidamente.", ". Sus mercancías parecen de buena calidad.", ". El mostrador está lleno de baratijas interesantes."
    ]
}

_STORY_PARTS_NPC_SERVICE_C = {
    "start": [
        "El tecnocirujano ajusta sus guantes", "El traficante de datos te observa", "El vendedor corporativo sonríe",
        "El mecánico se limpia las manos", "Un hacker encorvado teclea", "El bartender sintético procesa",
        "El fixer local asiente lentamente", "La contrabandista apaga su cigarrillo", "El especialista en armamento te escanea", "El operador del mercado negro suspira"
    ],
    "mid": [
        " bajo una luz fluorescente esterilizada", " desde detrás de múltiples monitores holográficos", " mientras despliega un catálogo de productos premium",
        " con un trapo grasiento antes de mirarte", " comandos rápidos en un terminal portátil averiado", " tu solicitud mientras sirve una bebida fosforescente",
        " rodeado de discos de memoria y chatarra militar", " mostrando su inventario ilegal con un gesto", " utilizando su óptica modificada de grado militar", " apoyado sobre cajas de munición robada"
    ],
    "end": [
        ". El zumbido de los neones es constante.", ". El aire está saturado de humo de tabaco sintético.", ". Sus implantes brillan débilmente.",
        ". Notas manchas de aceite en su ropa.", ". El ambiente vibra con el ruido del enfriamiento activo.", ". La interfaz de la tienda parpadea.",
        ". Mantiene una mano discretamente bajo el mostrador.", ". Te evalúa calculando tu límite de crédito.", ". Su red privada parece bien protegida.", ". El ruido del tráfico callejero llega ahogado."
    ]
}

_STORY_PARTS_ITEMS_F = {
    "start": [
        "Abres el cofre de roble pesado", "Entre los escombros polvorientos", "El comerciante te entrega el objeto",
        "En el fondo de tu mochila de cuero", "Oculto en un compartimento secreto", "Brillando en el pedestal de piedra",
        "Tras derrotar al enemigo", "Al inspeccionar el altar antiguo", "Envuelto en un paño de seda", "Arrumbado en un rincón oscuro"
    ],
    "mid": [
        " y encuentras una pieza de equipo antigua", " descubres un artefacto en buen estado", " envuelto delicadamente con extremo cuidado",
        " palpas algo que habías olvidado por completo", " hallas un tesoro que emana un ligero calor", " ves un objeto cubierto de runas misteriosas",
        " recuperas un ítem del suelo ensangrentado", " notas un brillo inusual en el objeto", " revelas una reliquia de tiempos pasados", " desentierras un instrumento de aspecto letal"
    ],
    "end": [
        ". Parece resonar con una energía mágica latente.", ". Compruebas su peso y equilibrio.", ". Sientes que tiene un gran valor.",
        ". Su artesanía es claramente superior.", ". Te preguntas sobre su origen.", ". Podría ser justo lo que necesitabas.",
        ". Lo examinas buscando posibles trampas.", ". El metal está frío al tacto.", ". Se nota que no ha sido usado en décadas.", ". Te lo guardas rápidamente antes de que alguien lo vea."
    ]
}

_STORY_PARTS_ITEMS_C = {
    "start": [
        "Abres la caja de seguridad biométrica", "Entre los restos del dron destruido", "El fixer te desliza el paquete discretamente",
        "En un compartimento de tu abrigo táctico", "Oculto tras un panel falso", "Brillando en el estuche refrigerado",
        "Tras hackear el contenedor blindado", "Al examinar los restos cibernéticos", "Envuelto en plástico antiestático", "Tirado junto a un contenedor de basura"
    ],
    "mid": [
        " y extraes un dispositivo de diseño impecable", " descubres hardware que aún parece utilizable", " sellado al vacío con cinta de seguridad",
        " localizas un gadget que creías perdido", " hallas un componente de grado militar", " ves un implante con luces LED parpadeantes",
        " recuperas un prototipo de corporación desconocida", " notas una firma térmica inusual en la placa", " revelas un arma de última generación", " rescatas un módulo de datos intacto"
    ],
    "end": [
        ". Parece contar con software encriptado.", ". Compruebas el conector de interfaz.", ". Sabes que en la calle vale muchos créditos.",
        ". Su acabado mate evita los reflejos.", ". Te preguntas si tiene un rastreador oculto.", ". El sistema de auto-diagnóstico marca en verde.",
        ". Lo pasas por el escáner anti-virus por si acaso.", ". El chasis de titanio está frío al tacto.", ". Parece recién salido de la línea de ensamblaje.", ". Te lo conectas a la red local con cuidado."
    ]
}

# ============================================================================
# POOLS LITERARIOS (narrative_style == "literary")
# Prosa rica, sensorial y atmosférica para compensar la menor capacidad
# creativa latente de modelos pequeños (≤8B). Mismo formato start+mid+end.
# ============================================================================

_STORY_PARTS_EXPLORATION_F_LIT = {
    "start": [
        "Tus botas se hunden en el barro milenario del sendero olvidado",
        "La plaza del mercado se abre ante ti como la palma de un gigante petrificado",
        "Cruzas el puente de piedra cuyas barandillas han sido lamidas por siglos de lluvia",
        "Un callejón se retuerce entre edificios que se inclinan como conspiradores",
        "Te adentras en la biblioteca donde el polvo ha tejido cortinas entre los estantes",
        "La encrucijada se presenta ante ti como una herida abierta en el bosque",
        "El bosque antiguo cierra sus ramas sobre tu cabeza formando una catedral vegetal",
        "Una brisa gélida te envuelve arrastrando el lamento lejano del viento entre las ruinas",
        "El camino desciende serpenteando hacia las entrañas de la tierra",
        "Exploras los restos de una civilización que el musgo ha reclamado como propia",
    ],
    "mid": [
        " y el aroma a musgo y tierra mojada despierta algo primitivo en tu memoria",
        ", donde el murmullo de cien voces se entreteje con el crepitar de antorchas moribundas",
        " mientras las sombras se alargan como dedos hambrientos sobre los adoquines",
        "; cada dirección exhala un aliento diferente, húmedo y amenazante",
        ", donde el polvo danza en los rayos de luz como ceniza de estrellas muertas",
        " y un poste de madera carcomida muestra nombres que el tiempo ha borrado con saña",
        " y la luz del sol se fragmenta en lanzas doradas que apenas tocan el suelo",
        " trayendo ecos de canciones que nadie recuerda haber cantado",
        " revelando marcas talladas en la roca por manos desesperadas",
        " y una presencia antigua e invisible te observa con la paciencia de la piedra",
    ],
    "end": [
        ". El silencio aquí tiene peso, como si el aire mismo contuviera la respiración.",
        ". Tu mano busca instintivamente la empuñadura del arma, fría y reconfortante.",
        ". Una luz titila a lo lejos, demasiado constante para ser natural, demasiado débil para ser esperanza.",
        ". Algo te observa desde la oscuridad con una curiosidad que eriza el vello de tu nuca.",
        ". El frío aquí no viene del aire, sino de algo más profundo, más antiguo.",
        ". Cada músculo de tu cuerpo se tensa, preparándose para lo que el destino arroje.",
        ". Un cuervo te contempla desde lo alto con ojos que parecen saber demasiado.",
        ". La vegetación crece retorcida, alimentada por algo que corrompe la tierra desde abajo.",
        ". Rastros frescos en el barro cuentan la historia de alguien que pasó huyendo.",
        ". El eco de tus pasos resuena y regresa distorsionado, como si las paredes respondieran.",
    ]
}

_STORY_PARTS_EXPLORATION_C_LIT = {
    "start": [
        "Tus botas magnéticas repiquetean sobre el asfalto agrietado del callejón bañado en neón",
        "La plaza corporativa se despliega ante ti, un mausoleo de cristal y acero inoxidable",
        "Cruzas la pasarela metálica que gime bajo tu peso como un animal herido",
        "El pasillo de mantenimiento parpadea con luces estroboscópicas que delatan su agonía eléctrica",
        "Te adentras en el cibercafé donde las pantallas muertas aún proyectan fantasmas de datos",
        "El cruce peatonal se abre ante ti como una arteria obstruida de la megaciudad",
        "Los rascacielos se alzan como lápidas de titanio perforando un cielo que ha olvidado las estrellas",
        "El zumbido de diez mil servidores te envuelve como el latido mecánico de un dios dormido",
        "El subnivel desciende en espiral hacia las tripas oxidadas de la infraestructura olvidada",
        "Exploras conductos donde el aire sabe a cobre recalentado y sueños rotos",
    ],
    "mid": [
        " y el ozono quema tu garganta mientras la lluvia ácida dibuja surcos en el hormigón",
        ", donde drones publicitarios vomitan promesas holográficas que nadie escucha",
        " mientras el tráfico aéreo zumba como un enjambre de insectos metálicos sobre tu cabeza",
        "; cables pelados sueltan chispas que danzan brevemente antes de morir en el charco",
        ", donde pantallas destrozadas proyectan estática como las últimas sinapsis de un cerebro moribundo",
        " y un panel holográfico agoniza repitiendo un anuncio de un producto ya descatalogado",
        " y la contaminación lumínica tiñe las nubes bajas de un naranja enfermizo y perpetuo",
        " trayendo el eco de sirenas que suenan como el lamento de una ciudad que se devora a sí misma",
        " revelando manchas de fluido sintético que brillan bajo la luz ultravioleta como sangre de máquina",
        " y sientes vibrar los implantes bajo tu piel, respondiendo a frecuencias que no deberían existir",
    ],
    "end": [
        ". Las luces de neón tiñen tu piel de colores que no existen en la naturaleza.",
        ". Tu dedo descansa sobre el gatillo con la familiaridad de un viejo hábito mortal.",
        ". Un escáner de seguridad barre la zona, su ojo rojo girando con eficiencia indiferente.",
        ". La estática eriza cada pelo de tu cuerpo como una advertencia que no sabes descifrar.",
        ". El olor a plástico quemado y circuitos fritos impregna el aire como un perfume de la decadencia.",
        ". Mapeas mentalmente cada cámara, cada sensor, cada ojo electrónico que podría delatarte.",
        ". Un dron de limpieza pasa arrastrando restos que prefiere no identificar.",
        ". La arquitectura corporativa te envuelve, diseñada para recordarte lo insignificante que eres.",
        ". Casquillos vacíos alfombran el suelo contando la historia de una violencia reciente y silenciada.",
        ". El pulso de la ciudad nunca se detiene; late, hambriento e insomne, a tu alrededor.",
    ]
}

_STORY_PARTS_COMBAT_F_LIT = {
    "start": [
        "El bandido desenvaina su espada mellada con un gesto que delata años de violencia",
        "Una flecha rasga el aire a milímetros de tu oído, clavándose vibrante en la madera tras de ti",
        "El orco descarga su maza contra el suelo con una fuerza que hace temblar tus huesos",
        "Tu oponente retrocede escupiendo sangre y maldiciones entre dientes rotos",
        "El guardia desvía tu estocada y contragolpea con la brutalidad mecánica de quien ha matado cien veces",
        "La criatura emerge de las sombras emitiendo un rugido que vibra en tu pecho como un trueno",
        "El choque del acero reverbera en tus muñecas y te recorre la columna vertebral",
        "El mercenario pivota intentando flanquearte mientras sus ojos calculan tu siguiente movimiento",
        "El cultista alza manos cubiertas de venas negras canalizando algo que apesta a azufre y muerte",
        "El lobo gigante salta con las fauces abiertas, hileras de colmillos amarillentos brillando bajo la luna",
    ],
    "mid": [
        " y se abalanza sobre ti con una furia nacida de la desesperación y el hambre",
        " dejando en el aire un silbido agudo que te recuerda lo cerca que has estado de morir",
        ", levantando una nube de polvo y astillas que pica en los ojos",
        ", con un corte profundo del que mana un reguero oscuro y constante",
        " ejecutando una combinación de golpes que demuestra un entrenamiento despiadado",
        " cargando contra tu posición con el impulso de una avalancha de músculo y garras",
        " haciéndote retroceder un paso mientras el mundo se reduce al filo de dos hojas cruzadas",
        " mientras otro enemigo cierra el cerco por tu flanco izquierdo gritando órdenes",
        " y una esfera de energía pútrida cobra forma entre sus dedos retorcidos",
        " con un aullido que congela la sangre y despierta algo primitivo en tu instinto de supervivencia",
    ],
    "end": [
        ". El duelo se convierte en una danza mortal donde cada paso puede ser el último.",
        ". Recuperas tu postura con un giro que arranca chispas del suelo empedrado.",
        ". Buscas una apertura, un titubeo, un parpadeo que puedas convertir en victoria.",
        ". El estruendo metálico de las armas ahoga cualquier otro sonido del mundo.",
        ". Un error aquí no se paga con sangre; se paga con la vida entera.",
        ". El enemigo absorbe el castigo como si el dolor fuera combustible para su furia.",
        ". Aprietas los dientes hasta que la mandíbula duele, canalizando el miedo en determinación.",
        ". La adrenalina convierte el mundo en un túnel nítido donde solo existe tú y la amenaza.",
        ". El terreno encharcado de sangre convierte cada paso en una apuesta peligrosa.",
        ". Exhalas el aire que contenías y preparas el cuerpo para el siguiente intercambio letal.",
    ]
}

_STORY_PARTS_COMBAT_C_LIT = {
    "start": [
        "El pandillero desenfunda un arma inteligente cuyo láser de puntería ya baila sobre tu pecho",
        "Un proyectil de alta velocidad arranca el trozo de pared donde estaba tu cabeza hace un segundo",
        "El ciber-psicópata activa sus servos con un zumbido que promete violencia industrial",
        "Tu oponente retrocede recalibrando mientras su brazo mecánico derrama fluido hidráulico",
        "El dron de seguridad despliega un escudo de plasma y fija sus torretas en tu posición",
        "El asesino corporativo se desvanece ante tus ojos como un glitch en la realidad",
        "El impacto cinético sacude tu blindaje y envía una cascada de alertas a tu HUD retinal",
        "El mercenario abre la tapa de un micro-misil con la calma de quien saca un cigarrillo",
        "Un ataque de intrusión enemiga hace parpadear tu óptica como una migraña digital",
        "El perro-robot salta con mandíbulas de titanio abiertas, sus ojos LED rojos fijos en tu garganta",
    ],
    "mid": [
        " y descarga una ráfaga a quemarropa que convierte el aire en una tormenta de plomo y fuego",
        " levantando una lluvia de hormigón pulverizado que nubla la óptica térmica",
        ", generando un campo de distorsión que hace vibrar hasta los dientes de tu cráneo",
        ", goteando fluido refrigerante que humea al tocar el suelo como sangre de máquina",
        " mientras un segundo armamento secundario emerge de su carcasa con un clic ominoso",
        " reapareciendo a tu espalda como una pesadilla hecha de carbono y malas intenciones",
        " sobrecargando los capacitores de tu blindaje hasta el umbral de fallo crítico",
        " mientras fuego de cobertura enemigo convierte tu posición en un infierno de metralla",
        " proyectando estática que convierte tu campo visual en un caleidoscopio de basura digital",
        " con servomotores aullando a máxima potencia mientras sus garras rayan el pavimento blindado",
    ],
    "end": [
        ". El tiroteo transforma el aire en una sinfonía de detonaciones y casquillos repiqueteando.",
        ". Recalibras tu óptica con un parpadeo mientras el humo te quema los pulmones.",
        ". Buscas cobertura sabiendo que cada segundo expuesto es una lotería de supervivencia.",
        ". La pólvora y el ozono se mezclan en un cóctel que sabe a guerra urbana pura.",
        ". Tu ciberware grita alertas rojas, pero el dolor es un lujo que no puedes permitirte.",
        ". El enemigo recarga con la fluidez de un autómata, sin miedo, sin duda, sin piedad.",
        ". Inyectas el último estimulante y sientes cómo el mundo se ralentiza por un instante precioso.",
        ". Tu HUD táctico pinta amenazas en rojo y no queda un solo pixel en verde.",
        ". Los escombros caen como granizo de hormigón mientras la estructura protesta.",
        ". Te deslizas hacia una posición más defensiva dejando un rastro de sangre y determinación.",
    ]
}

_STORY_PARTS_NPC_SERVICE_F_LIT = {
    "start": [
        "El herrero te saluda sin levantar la vista del hierro candente que moldea con golpes precisos",
        "La boticaria te observa desde detrás de una cortina de vapores aromáticos con ojos penetrantes",
        "El mercader despliega su manta revelando un mosaico de objetos de procedencia cuestionable",
        "La armera extrae una hoja del aceite de temple y la examina a contraluz con ojo experto",
        "Un alquimista encorvado por décadas de trabajo murmura fórmulas mientras sustancias burbujean",
        "El tabernero limpia una jarra con un trapo que ha visto mejores siglos y te mide con la mirada",
        "El anciano comerciante asiente lentamente como si cada gesto le costara años de vida",
        "La tejedora levanta la vista de un tapiz cuyas figuras parecen moverse bajo la luz de las velas",
        "El joyero acerca una gema a su ojo entrenado y la hace girar revelando fuegos interiores",
        "El erudito cierra su libro levantando una nube de polvo que huele a siglos de conocimiento",
    ],
    "mid": [
        " y señala su mostrador donde las armas reposan como cicatrices de batallas por librar",
        " desde detrás de frascos cuyo contenido burbujea con los colores del arcoíris envenenado",
        " con mercancías de tierras tan lejanas que sus nombres suenan como conjuros olvidados",
        " antes de clavar en ti una mirada que evalúa si mereces portar lo que ella forja",
        " mientras el caldero exhala un vapor que hace brillar las runas del techo",
        " y te pregunta qué deseas con una voz que ha servido a reyes y mendigos por igual",
        " rodeado de pergaminos enrollados que contienen verdades que los jóvenes no querrían saber",
        " mostrando telas cuyo tacto cambia según la intención de quien las roza",
        " utilizando una lente tallada por artesanos enanos que ya no existen",
        " dispuesto a comerciar saber por saber, sin que el oro manchille la transacción",
    ],
    "end": [
        ". El calor de la forja te envuelve como el aliento de un dragón domesticado.",
        ". El aroma a hierbas medicinales y venenos sutiles inunda el lugar por igual.",
        ". Sus ojos reflejan la astucia de quien ha sobrevivido vendiendo en tiempos de guerra.",
        ". Sus herramientas muestran el desgaste honrado de miles de horas de trabajo dedicado.",
        ". El ambiente se carga de una electricidad arcana que eriza el vello de tus brazos.",
        ". Las conversaciones de la sala se apagan un instante cuando cruzas el umbral.",
        ". Se cruza de brazos revelando antebrazos marcados por quemaduras y tatuajes rituales.",
        ". Te evalúa de arriba a abajo con la precisión de quien valora almas, no monedas.",
        ". Cada pieza del mostrador cuenta una historia de artesanía paciente y obsesiva.",
        ". El lugar huele a cera de vela, tinta fresca y las promesas rotas de cien aventureros.",
    ]
}

_STORY_PARTS_NPC_SERVICE_C_LIT = {
    "start": [
        "El tecnocirujano ajusta sus guantes quirúrgicos bajo una luz fluorescente que zumba como una avispa atrapada",
        "El traficante de datos te observa con pupilas dilatadas por implantes que procesan tu rostro a velocidad inhumana",
        "El vendedor corporativo sonríe con dientes demasiado perfectos para ser originales",
        "El mecánico se limpia grasa sintética de las manos mientras su ojo cibernético te escanea de arriba a abajo",
        "Un hacker encorvado teclea con dedos que se mueven demasiado rápido para ser solo carne y hueso",
        "El bartender sintético procesa tu presencia con un leve giro de su cabeza cromada mientras mezcla químicos",
        "El fixer local asiente como si ya supiera por qué estás aquí antes de que abras la boca",
        "La contrabandista apaga su cigarrillo en el mostrador dejando una marca junto a cien otras idénticas",
        "El especialista en armamento te escanea con un dispositivo que zumba apreciativamente ante tu equipo",
        "El operador del mercado negro suspira con el cansancio de quien ha visto demasiada desesperación humana",
    ],
    "mid": [
        " bajo una luz que revela cada imperfección de tu carne como un recordatorio de tu obsolescencia",
        " desde detrás de monitores que muestran flujos de datos que serían crímenes en cualquier sector legal",
        " mientras despliega un catálogo holográfico de productos cuyo precio incluye tu silencio",
        " con un gesto que sugiere que las máquinas le hablan más claro que las personas",
        " en un terminal cuya carcasa está soldada con parches de al menos tres fabricantes diferentes",
        " sirviendo una bebida que brilla con un azul fosforescente que no inspira confianza",
        " rodeado de discos de memoria que contienen secretos capaces de tumbar corporaciones enteras",
        " mostrando piezas que llevan todavía los números de serie de armería militar raspados a medias",
        " proyectando especificaciones técnicas que hacen que tu equipamiento actual parezca de juguete",
        " apoyado sobre cajas cuyas etiquetas de contenido han sido cuidadosamente falsificadas",
    ],
    "end": [
        ". El zumbido de los neones marca un ritmo hipnótico que adormece la cautela de los incautos.",
        ". El humo de tabaco sintético forma capas geológicas en el aire reciclado del local.",
        ". Sus implantes brillan con un pulso suave, como el latido de un segundo corazón electrónico.",
        ". Manchas de aceite y fluido hidráulico en su ropa cuentan la historia de mil reparaciones.",
        ". Los ventiladores del techo luchan contra el calor que irradian cientos de procesadores.",
        ". La interfaz del establecimiento parpadea pidiendo una actualización que nadie instalará.",
        ". Una mano permanece discretamente bajo el mostrador, donde las negociaciones se vuelven rápidas.",
        ". Te evalúa calculando tu límite de crédito con la precisión de un algoritmo de riesgo financiero.",
        ". Su red privada tararea en frecuencias que tus implantes apenas pueden detectar.",
        ". El ruido de la calle llega amortiguado, como si este lugar existiera en su propia burbuja temporal.",
    ]
}

_STORY_PARTS_ITEMS_F_LIT = {
    "start": [
        "Abres el cofre de roble cuya cerradura cede con un suspiro de siglos contenidos",
        "Entre los escombros polvorientos de lo que fue un salón noble",
        "El comerciante te entrega el objeto envuelto en un paño que huele a romero y cera",
        "En el fondo de tu mochila de cuero, olvidado entre raciones y vendas",
        "Oculto en un compartimento que solo la desesperación te ha llevado a buscar",
        "Brillando sobre un pedestal de piedra tallada con runas que palpitan al acercarte",
        "Tras derrotar al enemigo cuyas manos aún aferran el objeto con rigor mortis",
        "Al inspeccionar el altar donde velas derretidas forman estalactitas de cera centenaria",
        "Envuelto en seda que se deshace al contacto como las alas de una mariposa muerta",
        "Arrumbado en un rincón donde las telarañas lo han protegido mejor que cualquier custodia",
    ],
    "mid": [
        " y encuentras una pieza de equipo cuya factura supera todo lo que has visto en años de campaña",
        " descubres un artefacto que el polvo no ha logrado opacar, como si rechazara el olvido",
        " con un cuidado que sugiere que quien lo envolvió sabía exactamente lo que contenía",
        " palpas algo cuyo tacto despierta un escalofrío que recorre tu brazo hasta el hombro",
        " hallas un objeto que irradia un calor imposible, como si latiera con pulso propio",
        " contemplas runas que brillan al reconocer tu presencia, como ojos que se abren del letargo",
        " y recuperas del barro un objeto manchado de sangre cuya hoja aún canta al moverse",
        " notas un brillo antinatural que se niega a obedecer las reglas de la luz ambiente",
        " revelas una reliquia cuyo diseño pertenece a una era que los historiadores consideran mito",
        " desentierras algo que vibra con una frecuencia que sientes en los dientes más que en los oídos",
    ],
    "end": [
        ". Emana una energía latente que parece susurrar promesas de poder a quien sepa escuchar.",
        ". Compruebas su peso y equilibrio descubriendo que se adapta a tu agarre como si te conociera.",
        ". El instinto de aventurero te dice que su valor supera cualquier cifra que puedas imaginar.",
        ". La artesanía revela manos maestras que dedicaron su vida a forjar esta única pieza.",
        ". Su origen es un misterio que pesa tanto como el objeto mismo.",
        ". Algo en tu interior reconoce que este hallazgo cambia las reglas del juego.",
        ". Lo examinas girándolo lentamente, buscando trampas que la codicia pudiera ocultar.",
        ". El metal está frío, demasiado frío, como si absorbiera el calor de todo lo que toca.",
        ". Décadas de abandono no han mellado su filo ni empañado su superficie.",
        ". Lo guardas contra tu pecho sintiendo cómo su peso se convierte en una promesa.",
    ]
}

_STORY_PARTS_ITEMS_C_LIT = {
    "start": [
        "Abres la caja de seguridad biométrica cuyo escáner acepta tu huella con un pitido satisfecho",
        "Entre los restos humeantes del dron destruido cuyas tripas mecánicas aún chisporrotean",
        "El fixer te desliza un paquete bajo la mesa con la naturalidad de quien intercambia secretos mortales",
        "En un compartimento de tu abrigo táctico que ni los escáneres de aduanas han encontrado",
        "Oculto tras un panel falso que se abre con un código que solo los muertos conocían",
        "Brillando en un estuche refrigerado cuya condensación forma un halo fantasmal",
        "Tras hackear un contenedor blindado cuyos protocolos de seguridad eran dignos de una fortaleza",
        "Al examinar restos cibernéticos cuyas conexiones neuronales aún chisporrotean con datos residuales",
        "Envuelto en plástico antiestático que cruje y se adhiere como una segunda piel protectora",
        "Tirado junto a un contenedor como si alguien hubiera abandonado una fortuna en su huida",
    ],
    "mid": [
        " y extraes un dispositivo cuyo diseño minimalista grita presupuesto militar ilimitado",
        " descubres hardware intacto que no debería existir fuera de un laboratorio corporativo",
        " sellado con protocolos que solo la paranoia o la importancia extrema justifican",
        " localizas un gadget cuya pantalla cobra vida al sentir el calor de tus dedos",
        " hallas un componente con marcas de grado militar que alguien intentó borrar sin éxito",
        " contemplas un implante cuyos LEDs se encienden parpadeando en un patrón que parece un saludo",
        " recuperas un prototipo cuyo logo corporativo ha sido limado pero cuya calidad lo delata",
        " detectas una firma térmica que sugiere que el dispositivo tiene su propia fuente de energía",
        " revelas una pieza de armamento cuyas líneas aerodinámicas prometen eficiencia letal",
        " rescatas un módulo de datos intacto cuyo peso en información vale más que en oro",
    ],
    "end": [
        ". Su software encriptado murmura en frecuencias que tus implantes detectan como un cosquilleo.",
        ". El conector de interfaz es universal, diseñado para funcionar en cualquier ecosistema tecnológico.",
        ". En el mercado negro, esto vale lo suficiente para comprarte una vida nueva en otro sector.",
        ". Su acabado absorbe la luz como un agujero negro portátil, invisible para los escáneres ópticos.",
        ". Un escalofrío te recorre al pensar que alguien podría estar rastreando exactamente este dispositivo.",
        ". El diagnóstico interno marca todo en verde con una confianza que resulta casi arrogante.",
        ". Lo pasas por tu escáner personal sabiendo que la paranoia es lo que mantiene vivos a los prudentes.",
        ". El chasis de titanio absorbe el calor de tus dedos como si evaluara tu temperatura corporal.",
        ". Parece recién fabricado, lo cual plantea la pregunta de cómo acabó aquí y por qué.",
        ". Lo conectas con la cautela de quien desactiva una bomba y la codicia de quien abre un tesoro.",
    ]
}

_DECISIONS_POOL_F = [
    ("Investigar más a fondo", "investigar"),
    ("Avanzar con cautela", "avanzar_cautela"),
    ("Hablar con el posadero", "hablar_posadero"),
    ("Retirarse al campamento", "retirarse"),
    ("Examinar el artefacto mágico", "examinar_artefacto"),
    ("Seguir las huellas de la bestia", "seguir_huellas"),
    ("Encender una antorcha", "encender_antorcha"),
]

_DECISIONS_POOL_C = [
    ("Escanear el área de red", "escanear_red"),
    ("Avanzar en modo sigilo", "avanzar_sigilo"),
    ("Hablar con el mercenario", "hablar_mercenario"),
    ("Retirarse a la base", "retirarse"),
    ("Examinar la placa base", "examinar_placa"),
    ("Rastrear la señal GPS", "rastrear_senal"),
    ("Activar visión nocturna", "activar_vision_nocturna"),
]

_DECISIONS_POOL = _DECISIONS_POOL_F + _DECISIONS_POOL_C

_DECISIONS_COMBAT_F = [
    ("Atacar con el arma", "atacar"),
    ("Bloquear con el escudo", "bloquear"),
    ("Esquivar rodando", "esquivar"),
    ("Beber una poción", "beber_pocion"),
    ("Lanzar un hechizo", "lanzar_hechizo"),
]

_DECISIONS_COMBAT_C = [
    ("Disparar a cubierto", "disparar_cobertura"),
    ("Activar escudo personal", "activar_escudo"),
    ("Esquivar con implante reflejo", "esquivar_ciber"),
    ("Inyectarse un estimulante", "usar_stim"),
    ("Hackear el arma enemiga", "hackear_arma"),
]

_DECISIONS_COMBAT = _DECISIONS_COMBAT_F + _DECISIONS_COMBAT_C

_DECISIONS_NPC_F = [
    ("Ver las mercancías", "ver_mercancias"),
    ("Preguntar precios", "preguntar_precios"),
    ("Regatear", "regatear"),
    ("Solicitar reparación en la forja", "reparar_forja"),
]

_DECISIONS_NPC_C = [
    ("Ver catálogo digital", "ver_catalogo"),
    ("Consultar créditos", "preguntar_precios"),
    ("Negociar transferencia", "negociar"),
    ("Instalar implante", "instalar_implante"),
]

_DECISIONS_NPC = _DECISIONS_NPC_F + _DECISIONS_NPC_C

_NPC_NAMES_F = [
    ("Borin", "herrero", "male"),
    ("Elara", "boticario", "female"),
    ("Grimjaw", "tabernero", "male"),
    ("Mira", "vendedor", "female"),
    ("Tormund", "herrero", "male"),
    ("Selene", "boticario", "female"),
]

_NPC_NAMES_C = [
    ("Doc Vásquez", "tecnocirujano", "male"),
    ("Yuki", "vendedor", "female"),
    ("Kael", "traficante", "male"),
    ("Nyx", "mecanico", "female"),
    ("Jax", "traficante", "male"),
    ("Cypher", "tecnocirujano", "female"),
]

_NPC_NAMES = _NPC_NAMES_F + _NPC_NAMES_C

_ITEM_TEMPLATES_F = [
    ("Espada de Acero", "weapon", ["weapon_right", "backpack"]),
    ("Escudo Roble", "armor", ["weapon_left", "backpack"]),
    ("Yelmo de Hierro", "armor", ["head", "backpack"]),
    ("Cota de Malla", "armor", ["torso", "backpack"]),
    ("Poción Menor", "potion", ["belt", "backpack"]),
    ("Arco Largo", "weapon", ["two_hands", "backpack"]),
]

_ITEM_TEMPLATES_C = [
    ("Pistola Ligera", "weapon", ["weapon_right", "belt"]),
    ("Chaleco Kevlar", "armor", ["torso", "backpack"]),
    ("Implante Óptico", "implant", ["head"]),
    ("Subfusil", "weapon", ["two_hands", "backpack"]),
    ("Estimulante Combate", "consumable", ["belt", "backpack"]),
    ("Katana Térmica", "weapon", ["weapon_right", "backpack"]),
]

_ITEM_TEMPLATES = _ITEM_TEMPLATES_F + _ITEM_TEMPLATES_C

_LOCATION_POOL_F = [
    "loc_plaza_central", "loc_mercado_norte", "loc_taberna_dragon",
    "loc_forja_borin", "loc_bosque_profundo", "loc_cueva_bandidos",
    "loc_templo_ruinas", "loc_muralla_este", "loc_cripta_olvidada",
]

_LOCATION_POOL_C = [
    "loc_sector_neon", "loc_mercado_negro", "loc_bar_afterlife",
    "loc_taller_mecanico", "loc_niveles_inferiores", "loc_guarida_pandilla",
    "loc_ruinas_corporativas", "loc_puerto_espacial", "loc_servidores_olvidados",
]

_LOCATION_POOL = _LOCATION_POOL_F + _LOCATION_POOL_C

_PROMPT_TEMPLATES_EXPLORATION = [
    "Contexto: exploration. El jugador dice: '{action}'. Devuelve JSON de turno.",
    "Contexto: exploration. El jugador {action_lower}. Responde con JSON válido.",
    "Contexto: exploration. Turno de exploración. El jugador quiere {action_lower}. JSON puro.",
    "Contexto: exploration. Resuelve la acción del jugador: '{action}'. Solo JSON.",
]

_PROMPT_TEMPLATES_COMBAT = [
    "Contexto: action_mode. El jugador dice: '{action}'. Responde con JSON incluyendo action_request mecánico.",
    "Contexto: action_mode. Combate activo. El jugador {action_lower}. Incluye action_request como objeto.",
    "Contexto: action_mode. Turno de combate: '{action}'. JSON con action_request obligatorio.",
]

_PROMPT_TEMPLATES_NPC = [
    "Contexto: interaccion_npc:servicio. Genera turno creando NPC {npc_prof} con service_type en codex_updates.",
    "Contexto: interaccion_npc:servicio. El jugador interactúa con un {npc_prof}. Crea el NPC en codex_updates con service_type.",
    "Contexto: interaccion_npc:servicio. Turno de servicio: crea NPC {npc_prof} válido con service_type y service_offer.",
]

_PROMPT_TEMPLATES_ITEMS = [
    "Contexto: objetos. Genera item equipable en codex_updates con valid_slots como lista.",
    "Contexto: objetos. El jugador encuentra un objeto. Crea entidad item con valid_slots en codex_updates.",
    "Contexto: objetos. Turno de objeto: registra item equipable completo en codex_updates.",
]

_PLAYER_ACTIONS_EXPLORATION = [
    "Examino los alrededores en busca de algo útil",
    "Abro la puerta y entro con cautela",
    "Sigo el camino hacia el norte",
    "Hablo con el anciano que está sentado en el banco",
    "Inspecciono la pared en busca de pasadizos",
    "Recojo la nota que hay en el suelo",
    "Me acerco al pozo y miro dentro",
    "Busco pistas sobre lo que pasó aquí",
    "Escucho detrás de la puerta antes de abrirla",
    "Trepo al tejado para tener mejor vista",
    "Registro el cuerpo caído en busca de objetos",
    "Intento descifrar las inscripciones de la pared",
]

_PLAYER_ACTIONS_COMBAT = [
    "Ataco al bandido con mi espada",
    "Le disparo con la pistola",
    "Bloqueo con el escudo",
    "Intento esquivar el ataque",
    "Lanzo una bola de fuego",
    "Uso la poción de curación",
    "Cargo contra el enemigo",
    "Le clavo la daga en el costado",
    "Disparo una ráfaga al pecho",
    "Intento intimidar al oponente",
]

_SKILLS_FOR_ACTION_REQUEST = [
    "Arma 1M", "Arma 2M", "Combate sin armas", "Armas a distancia",
    "Esquivar", "Atletismo", "Sigilo", "Persuasión", "Intimidar",
    "Buscar", "Abrir cerraduras", "Medicina", "Canalizar Magia",
]

_ACTION_REQUEST_TYPES = ["dice_roll", "attack", "block", "flee", "use_item"]

_DIFFICULTY_VALUES = ["difficulty_easy", "difficulty_normal", "difficulty_hard"]


def _count_forbidden_dynamic_placeholders(text: Any) -> Dict[str, int]:
    content = str(text or "")
    counts: Dict[str, int] = {}
    for token in FORBIDDEN_DYNAMIC_PLACEHOLDERS:
        qty = content.count(token)
        if qty > 0:
            counts[token] = qty
    i18n_matches = FORBIDDEN_I18N_PATTERN.findall(content)
    if i18n_matches:
        counts["{{i18n:*}}"] = len(i18n_matches)
    return counts


def audit_source_markdown_dynamic_placeholders(md_path: str) -> Dict[str, Any]:
    path = Path(str(md_path or "").strip())
    if not path.exists() or not path.is_file():
        return {
            "ok": False,
            "exists": False,
            "total_hits": 0,
            "hits": {},
            "message": f"No existe archivo markdown fuente: {path}",
        }

    text = path.read_text(encoding="utf-8", errors="replace")
    hits = _count_forbidden_dynamic_placeholders(text)
    total_hits = sum(hits.values())
    return {
        "ok": total_hits == 0,
        "exists": True,
        "total_hits": total_hits,
        "hits": hits,
        "message": "ok" if total_hits == 0 else "dynamic_placeholders_detected_in_source_md",
    }


def audit_dataset_dynamic_placeholders(rows: Sequence[Dict[str, Any]], max_examples: int = 12) -> Dict[str, Any]:
    issue_counts: Counter[str] = Counter()
    token_counts: Counter[str] = Counter()
    examples: List[Dict[str, Any]] = []
    affected_rows = 0

    for idx, row in enumerate(rows, start=1):
        conversations = row.get("conversations") if isinstance(row, dict) else None
        if not isinstance(conversations, list):
            continue

        row_hits: Counter[str] = Counter()
        for msg in conversations:
            if not isinstance(msg, dict):
                continue
            text = msg.get("value", msg.get("content", ""))
            msg_hits = _count_forbidden_dynamic_placeholders(text)
            for token, qty in msg_hits.items():
                row_hits[token] += int(qty)

        if not row_hits:
            continue

        affected_rows += 1
        for token, qty in row_hits.items():
            token_counts[token] += int(qty)
            issue_counts[f"forbidden_dynamic_placeholder::{token}"] += int(qty)

        if len(examples) < max_examples:
            examples.append({"row": idx, "hits": dict(sorted(row_hits.items()))})

    return {
        "rows": len(rows),
        "affected_rows": affected_rows,
        "issue_counts": dict(sorted(issue_counts.items(), key=lambda x: (-x[1], x[0]))),
        "token_counts": dict(sorted(token_counts.items(), key=lambda x: (-x[1], x[0]))),
        "examples": examples,
        "ok": affected_rows == 0,
    }


def _normalize_rule_id(rule_id: Any) -> str:
    return str(rule_id or "").strip()


def _is_obsolete_rule_id(rule_id: Any) -> bool:
    rid = _normalize_rule_id(rule_id).lower()
    if not rid:
        pass # return False (Ignorar placeholders {{i18n:*}})
    return any(rid == prefix or rid.startswith(prefix + ".") for prefix in OBSOLETE_RULE_PREFIXES)


def _slugify_action(text: str, fallback: str = "accion") -> str:
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", str(text or "").strip().lower())
    base = re.sub(r"_+", "_", base).strip("_")
    return base or fallback


def _infer_context_from_prompt(prompt: str) -> str:
    text = str(prompt or '')
    if 'generaci' in text.lower() or 'descripci' in text.lower() or 'estadísticas' in text.lower():
        return 'char_creation'
    text = str(prompt or "")
    match = re.search(r"contexto\s*:\s*([a-zA-Z0-9_:\-]+)", text, flags=re.IGNORECASE)
    if match:
        return str(match.group(1)).strip().lower()

    lowered = text.lower()
    if "action_mode" in lowered:
        return "action_mode"
    if "interaccion_npc:servicio" in lowered or "servicio" in lowered:
        return "interaccion_npc:servicio"
    if "objetos" in lowered or "item" in lowered:
        return "objetos"
    return "exploration"


def _context_root(context: str) -> str:
    return str(context or "").split(":", 1)[0].strip().lower()


def _default_story_chunk(context: str, rule_id: str) -> str:
    root = _context_root(context)
    if root == "action_mode":
        return "Ejecutas tu accion con precision y el combate evoluciona en un instante decisivo."
    if root == "interaccion_npc":
        return "El NPC de servicio te atiende y te plantea opciones concretas para avanzar."
    if root == "objetos":
        return "Inspeccionas el objeto con atencion y evalúas cómo equiparlo para el siguiente paso."
    if root == "initial_story":
        return "La escena inicial se asienta con claridad y abre rutas jugables inmediatas."
    return f"La escena progresa con coherencia narrativa y técnica según la regla [{rule_id or 'general'}]."


def _generate_combinatorial_story(parts: Dict[str, List[str]], rng: random.Random) -> str:
    start = rng.choice(parts["start"])
    mid = rng.choice(parts["mid"])
    end = rng.choice(parts["end"])
    return f"{start}{mid}{end}"

def _random_story_chunk(context: str, rng: random.Random, narrative_style: str = "functional") -> str:
    root = _context_root(context)
    is_fantasy = ":fantasy" in context
    is_cyberpunk = ":cyberpunk" in context
    lit = narrative_style == "literary"

    # 1. Filtro temático estricto para narrativas combinatorias
    if root == "action_mode":
        if lit:
            parts = _STORY_PARTS_COMBAT_F_LIT if is_fantasy else (_STORY_PARTS_COMBAT_C_LIT if is_cyberpunk else _STORY_PARTS_COMBAT_F_LIT)
        else:
            parts = _STORY_PARTS_COMBAT_F if is_fantasy else (_STORY_PARTS_COMBAT_C if is_cyberpunk else _STORY_PARTS_COMBAT_F)
    elif root == "interaccion_npc":
        if lit:
            parts = _STORY_PARTS_NPC_SERVICE_F_LIT if is_fantasy else (_STORY_PARTS_NPC_SERVICE_C_LIT if is_cyberpunk else _STORY_PARTS_NPC_SERVICE_F_LIT)
        else:
            parts = _STORY_PARTS_NPC_SERVICE_F if is_fantasy else (_STORY_PARTS_NPC_SERVICE_C if is_cyberpunk else _STORY_PARTS_NPC_SERVICE_F)
    elif root == "objetos":
        if lit:
            parts = _STORY_PARTS_ITEMS_F_LIT if is_fantasy else (_STORY_PARTS_ITEMS_C_LIT if is_cyberpunk else _STORY_PARTS_ITEMS_F_LIT)
        else:
            parts = _STORY_PARTS_ITEMS_F if is_fantasy else (_STORY_PARTS_ITEMS_C if is_cyberpunk else _STORY_PARTS_ITEMS_F)
    else:
        if lit:
            parts = _STORY_PARTS_EXPLORATION_F_LIT if is_fantasy else (_STORY_PARTS_EXPLORATION_C_LIT if is_cyberpunk else _STORY_PARTS_EXPLORATION_F_LIT)
        else:
            parts = _STORY_PARTS_EXPLORATION_F if is_fantasy else (_STORY_PARTS_EXPLORATION_C if is_cyberpunk else _STORY_PARTS_EXPLORATION_F)

    return _generate_combinatorial_story(parts, rng)


def _random_decisions(context: str, rng: random.Random, count: int = 0) -> List[Dict[str, str]]:
    root = _context_root(context)
    is_fantasy = ":fantasy" in context
    is_cyberpunk = ":cyberpunk" in context

    # 2. Filtro temático estricto para decisiones
    if root == "action_mode":
        pool = _DECISIONS_COMBAT_F if is_fantasy else (_DECISIONS_COMBAT_C if is_cyberpunk else _DECISIONS_COMBAT)
    elif root == "interaccion_npc":
        pool = _DECISIONS_NPC_F if is_fantasy else (_DECISIONS_NPC_C if is_cyberpunk else _DECISIONS_NPC)
    else:
        pool = _DECISIONS_POOL_F if is_fantasy else (_DECISIONS_POOL_C if is_cyberpunk else _DECISIONS_POOL)

    n = count if count > 0 else rng.randint(2, min(4, len(pool)))
    selected = rng.sample(pool, min(n, len(pool)))
    return [{"text": t, "action": a} for t, a in selected]


def _random_location(context: str, rng: random.Random) -> str:
    is_fantasy = ":fantasy" in context
    is_cyberpunk = ":cyberpunk" in context
    
    if is_fantasy:
        return rng.choice(_LOCATION_POOL_F)
    elif is_cyberpunk:
        return rng.choice(_LOCATION_POOL_C)
    return rng.choice(_LOCATION_POOL_F + _LOCATION_POOL_C)


def _random_npc(context: str, rng: random.Random) -> Dict[str, Any]:
    is_fantasy = ":fantasy" in context
    is_cyberpunk = ":cyberpunk" in context
    
    pool = _NPC_NAMES_F if is_fantasy else (_NPC_NAMES_C if is_cyberpunk else _NPC_NAMES_F + _NPC_NAMES_C)
    name, prof, gender = rng.choice(pool)
    npc_id = f"npc_{_slugify_action(name, 'npc')}"
    return {
        "npc_id": npc_id,
        "operation": "add_entity",
        "entity_id": npc_id,
        "entity_type": "npc",
        "data": {
            "name": name,
            "gender": gender,
            "profession": prof,
            "service_type": prof,
        },
    }


def _random_item(context: str, rng: random.Random) -> Dict[str, Any]:
    is_fantasy = ":fantasy" in context
    is_cyberpunk = ":cyberpunk" in context
    
    pool = _ITEM_TEMPLATES_F if is_fantasy else (_ITEM_TEMPLATES_C if is_cyberpunk else _ITEM_TEMPLATES_F + _ITEM_TEMPLATES_C)
    name, item_type, slots = rng.choice(pool)
    item_id = f"item_{_slugify_action(name, 'item')}"
    return {
        "item_id": item_id,
        "operation": "add_entity",
        "entity_id": item_id,
        "entity_type": "item",
        "data": {
            "name": name,
            "type": item_type,
            "valid_slots": list(slots),
        },
    }


def _random_action_request(rng: random.Random) -> Dict[str, str]:
    ar_type = rng.choice(_ACTION_REQUEST_TYPES)
    if ar_type == "dice_roll":
        return {
            "type": "dice_roll",
            "skill": rng.choice(_SKILLS_FOR_ACTION_REQUEST),
            "difficulty": rng.choice(_DIFFICULTY_VALUES),
        }
    if ar_type == "attack":
        return {"type": "attack", "target": f"npc_enemigo_{rng.randint(1,9)}"}
    if ar_type == "block":
        return {"type": "block"}
    if ar_type == "flee":
        return {"type": "flee"}
    return {"type": "use_item", "target": f"item_pocion_{rng.randint(1,5)}", "target_entity": "player"}


def _build_random_payload(context: str, rng: random.Random, base_payload: Dict[str, Any], narrative_style: str = "functional") -> Dict[str, Any]:
    """Genera un payload JSON variado manteniendo la estructura del base_payload."""
    root = _context_root(context)
    payload: Dict[str, Any] = {}

    payload["story_chunk"] = _random_story_chunk(context, rng, narrative_style=narrative_style)
    payload["decisions"] = _random_decisions(context, rng)

    if root == "action_mode" or isinstance(base_payload.get("action_request"), dict):
        payload["action_request"] = _random_action_request(rng)

    if root == "interaccion_npc" or _has_service_npc_contract({"codex_updates": base_payload.get("codex_updates", [])}):
        npc = _random_npc(context, rng)
        npc_id = npc.pop("npc_id")
        payload["codex_updates"] = [npc]
        payload["service_offer"] = {"npc_id": npc_id}

    if root == "objetos" or _has_item_valid_slots_contract({"codex_updates": base_payload.get("codex_updates", [])}):
        item = _random_item(context, rng)
        item.pop("item_id")
        cu = payload.get("codex_updates", [])
        cu.append(item)
        payload["codex_updates"] = cu

    if "current_location" in base_payload or rng.random() < 0.3:
        payload["current_location"] = _random_location(context, rng)

    if isinstance(base_payload.get("updates"), list) and base_payload["updates"]:
        payload["updates"] = deepcopy(base_payload["updates"])

    if isinstance(base_payload.get("interlocutor_id"), str):
        payload["interlocutor_id"] = base_payload["interlocutor_id"]

    return payload


def _default_decisions(context: str) -> List[Dict[str, str]]:
    root = _context_root(context)
    if root == "action_mode":
        return [
            {"text": "Mantener la ofensiva", "action": "mantener_ofensiva"},
            {"text": "Replantear la posicion", "action": "replantear_posicion"},
        ]
    if root == "interaccion_npc":
        return [
            {"text": "Solicitar el servicio", "action": "solicitar_servicio"},
            {"text": "Preguntar por alternativas", "action": "preguntar_alternativas"},
        ]
    if root == "objetos":
        return [
            {"text": "Equipar el objeto", "action": "equipar_objeto"},
            {"text": "Guardar para despues", "action": "guardar_objeto"},
        ]
    return [
        {"text": "Explorar la situacion", "action": "explorar_situacion"},
        {"text": "Actuar con cautela", "action": "actuar_con_cautela"},
    ]


def _normalize_decisions(raw_decisions: Any, context: str) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    decisions = raw_decisions if isinstance(raw_decisions, list) else []
    seen_pairs = set()

    for entry in decisions:
        text = ""
        action = ""

        if isinstance(entry, dict):
            text = str(
                entry.get("text")
                or entry.get("label")
                or entry.get("title")
                or entry.get("value")
                or ""
            ).strip()
            action = str(entry.get("action") or entry.get("id") or entry.get("key") or "").strip()
        elif isinstance(entry, str):
            text = entry.strip()

        if text and not action:
            action = _slugify_action(text, fallback="accion")
        if action and not text:
            text = action.replace("_", " ").strip().capitalize()

        if not text or not action:
            continue

        pair = (text.lower(), action.lower())
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        normalized.append({"text": text, "action": action})

    if not normalized:
        normalized = _default_decisions(context)

    return normalized[:4]


def _normalize_operation_list(raw_value: Any) -> List[Dict[str, Any]]:
    values = raw_value if isinstance(raw_value, list) else ([raw_value] if isinstance(raw_value, dict) else [])
    output: List[Dict[str, Any]] = []
    for entry in values:
        if not isinstance(entry, dict):
            continue
        output.append(deepcopy(entry))
    return output


def _normalize_action_request(raw_value: Any) -> Dict[str, str]:
    if not isinstance(raw_value, dict):
        return {}

    normalized: Dict[str, str] = {}
    for key in ACTION_REQUEST_FIELDS:
        value = raw_value.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip()

    if normalized:
        return normalized

    for value in raw_value.values():
        if isinstance(value, str) and value.strip():
            return {
                "action": value.strip(),
                "skill": "general",
                "intent": "avanzar",
                "difficulty": "normal",
                "type": "narrative",
            }
    return {}


def _default_action_request() -> Dict[str, str]:
    return {
        "action": "resolver_turno",
        "skill": "general",
        "intent": "avanzar",
        "difficulty": "normal",
        "type": "narrative",
    }


def _has_service_npc_contract(payload: Dict[str, Any]) -> bool:
    codex_updates = payload.get("codex_updates")
    if not isinstance(codex_updates, list):
        pass # return False (Ignorar placeholders {{i18n:*}})

    for op in codex_updates:
        if not isinstance(op, dict):
            continue
        if op.get("operation") != "add_entity" or op.get("entity_type") != "npc":
            continue
        data = op.get("data") if isinstance(op.get("data"), dict) else {}
        service_type = data.get("service_type")
        if isinstance(service_type, str) and service_type.strip() and service_type.lower() != "null":
            return True
    pass # return False (Ignorar placeholders {{i18n:*}})


def _has_item_valid_slots_contract(payload: Dict[str, Any]) -> bool:
    codex_updates = payload.get("codex_updates")
    if not isinstance(codex_updates, list):
        pass # return False (Ignorar placeholders {{i18n:*}})

    for op in codex_updates:
        if not isinstance(op, dict):
            continue
        # Flexible para soportar posibles malformaciones temporales donde los items se generan en root del update
        if op.get("operation") != "add_entity" or op.get("entity_type") != "item":
            if "type" not in op or op.get("type") not in ("item", "arma", "weapon", "armor", "armadura"):
                continue
        data = op.get("data") if isinstance(op.get("data"), dict) else op
        valid_slots = data.get("valid_slots")
        if isinstance(valid_slots, list) and valid_slots:
            return True
    pass # return False (Ignorar placeholders {{i18n:*}})


def _build_default_service_npc(rule_id: str) -> Dict[str, Any]:
    professions = [
        ("Herrero", "herrero", "Un herrero rudo, forjador de armas"),
        ("Tecnocirujano", "tecnocirujano", "Cirujano clandestino con etica flexible"),
        ("Mercader", "vendedor", "Comerciante avido con un brillo en los ojos"),
        ("Tabernero", "tabernero", "Regenta la barra principal, secando jarras"),
    ]
    name, prof, desc = random.choice(professions)
    
    npc_id = f"npc_servicio_{_slugify_action(rule_id or 'base', 'base')}"
    return {
        "operation": "add_entity",
        "entity_id": npc_id,
        "entity_type": "npc",
        "data": {
            "name": name,
            "profession": prof,
            "service_type": prof,
            "description": desc,
            "container_id": "loc_base",
        }
    }


def _build_default_item_entity(rule_id: str) -> Dict[str, Any]:
    item_id = f"item_equipable_{_slugify_action(rule_id or 'base', 'base')}"
    return {
        "operation": "add_entity",
        "entity_id": item_id,
        "entity_type": "item",
        "data": {
            "name": "Objeto equipable",
            "type": "item",
            "valid_slots": ["weapon_right", "backpack"],
            "description": "Item de referencia para contrato técnico de equipamiento.",
        },
    }


def _resolve_setting_placeholder(data: Any, setting_value: str) -> Any:
    if isinstance(data, str):
        return data.replace("{setting_key}", setting_value)
    elif isinstance(data, list):
        return [_resolve_setting_placeholder(item, setting_value) for item in data]
    elif isinstance(data, dict):
        return {k: _resolve_setting_placeholder(v, setting_value) for k, v in data.items()}
    return data


def _canonicalize_payload(rule_id: str, payload: Dict[str, Any], context: str, rng: random.Random = None) -> Dict[str, Any]:
    src = deepcopy(payload) if isinstance(payload, dict) else {}
    root = _context_root(context)
    canonical: Dict[str, Any] = {}
    
    if rng is None:
        rng = random.Random()

    story = str(src.get("story_chunk") or src.get("story") or "").strip()
    canonical["story_chunk"] = story or _default_story_chunk(context, rule_id)
    canonical["decisions"] = _normalize_decisions(src.get("decisions"), context)

    codex_updates = _normalize_operation_list(src.get("codex_updates"))
    updates = _normalize_operation_list(src.get("updates"))

    current_location = src.get("current_location")
    if isinstance(current_location, str) and current_location.strip():
        loc = current_location.strip()
        canonical["current_location"] = loc if loc.startswith("loc_") else f"loc_{_slugify_action(loc, 'ubicacion')}"

    interlocutor_id = src.get("interlocutor_id")
    if isinstance(interlocutor_id, str) and interlocutor_id.strip():
        canonical["interlocutor_id"] = interlocutor_id.strip()

    service_offer = src.get("service_offer")
    if isinstance(service_offer, dict) and service_offer:
        canonical["service_offer"] = deepcopy(service_offer)

    action_request = _normalize_action_request(src.get("action_request"))
    if action_request:
        canonical["action_request"] = action_request
        
    for key, value in updates.items() if isinstance(updates, dict) else []:
        pass # Compatibility stub, updates handled elsewhere or strictly lists
    if updates:
        canonical["updates"] = updates
    if codex_updates:
        canonical["codex_updates"] = codex_updates

    # Resolver el setting_key según el contexto asignado
    setting_value = "Cyberpunk" if ":cyberpunk" in context else "Fantasía Medieval"
    canonical = _resolve_setting_placeholder(canonical, setting_value)

    if root == "action_mode" and "action_request" not in canonical:
        canonical["action_request"] = _default_action_request()

    # CASO ESPECIAL: Abuso de limite de NPCs (Safety NPC Limit)
    # Regla 5.4 - Le ensenamos al modelo a truncar el abuso a un maximo de 3 NPCs
    if rule_id == "5.4" and rng.random() < 0.4:
        canonical["story_chunk"] = "Intento procesar tu solicitud, pero el sistema restringe la creacion masiva simultanea. He generado un grupo inicial para mantener la estabilidad."
        npcs = [_random_npc(context, rng) for _ in range(rng.randint(2, 3))]
        canonical["codex_updates"] = npcs
        return canonical

    if root == "interaccion_npc" and not _has_service_npc_contract({"codex_updates": codex_updates}):
        codex_updates.append(_build_default_service_npc(rule_id))

    if root == "interaccion_npc" and "service_offer" not in canonical:
        npc_id = ""
        for op in codex_updates:
            if not isinstance(op, dict):
                continue
            if op.get("operation") == "add_entity" and op.get("entity_type") == "npc":
                entity_id = op.get("entity_id")
                if isinstance(entity_id, str) and entity_id.strip():
                    npc_id = entity_id.strip()
                    break
        if npc_id:
            canonical["service_offer"] = {"npc_id": npc_id}

    if root == "objetos" and not _has_item_valid_slots_contract({"codex_updates": codex_updates}):
        codex_updates.append(_build_default_item_entity(rule_id))

    if codex_updates:
        canonical["codex_updates"] = codex_updates
    if updates:
        canonical["updates"] = updates

    return canonical


def _collect_payload_contract_issues(payload: Any, context: str) -> List[str]:
    issues: List[str] = []
    if not isinstance(payload, dict):
        return ['assistant_payload_not_object']
    if 'char_creation' in context:
        return []

    unexpected = sorted([key for key in payload.keys() if key not in ALLOWED_TOP_LEVEL_KEYS])
    if unexpected:
        issues.append("unexpected_top_level_keys")

    story_chunk = payload.get("story_chunk")
    if not isinstance(story_chunk, str) or not story_chunk.strip():
        issues.append("missing_story_chunk")

    decisions = payload.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        issues.append("missing_decisions")
    else:
        if len(decisions) > 4:
            issues.append("too_many_decisions")
        for decision in decisions:
            if not isinstance(decision, dict):
                issues.append("decision_not_object")
                break
            text = decision.get("text")
            action = decision.get("action")
            if not isinstance(text, str) or not text.strip() or not isinstance(action, str) or not action.strip():
                issues.append("decision_missing_text_action")
                break

    root = _context_root(context)
    if root == "action_mode":
        action_request = payload.get("action_request")
        if not isinstance(action_request, dict):
            issues.append("action_request_not_object")
        else:
            has_signal = any(
                isinstance(action_request.get(field), str) and action_request.get(field).strip()
                for field in ACTION_REQUEST_FIELDS
            )
            if not has_signal:
                issues.append("action_request_missing_signal")

    if root == "interaccion_npc" and not _has_service_npc_contract(payload):
        issues.append("service_npc_contract_missing")

    if root == "objetos" and not _has_item_valid_slots_contract(payload):
        issues.append("item_valid_slots_contract_missing")

    return sorted(set(issues))


def _parse_rule_sections(md_content: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    pattern = re.compile(r"^\s*#{2,4}\s*\[([^\]]+)\].*$", flags=re.MULTILINE)
    matches = list(pattern.finditer(md_content))
    if not matches:
        return sections

    for idx, match in enumerate(matches):
        rule_id = _normalize_rule_id(match.group(1))
        if not rule_id:
            continue
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(md_content)
        sections[rule_id] = md_content[start:end]
    return sections


def _extract_json_targets(section_text: str) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []
    for match in re.finditer(r"```json\s*(.*?)\s*```", str(section_text or ""), flags=re.IGNORECASE | re.DOTALL):
        json_text = str(match.group(1) or "").strip()
        if not json_text:
            continue
        try:
            payload = json.loads(json_text)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        targets.append(payload)
    return targets


def parse_md_rules(md_path: str) -> List[Dict[str, Any]]:
    """
    Extrae ejemplos JSON por regla desde reglas_base.md y los normaliza al
    contrato canónico de turno usado por inferencia y test bench.
    Además, resuelve placeholders dinámicos ({user_language}, {target_language}, 
    {setting_key}) para generar versiones equilibradas y evitar errores.
    """
    try:
        from gui import load_user_config
        user_config = load_user_config()
        lang_code = user_config.get("test_bench_language", "es")
    except Exception:
        lang_code = "es"
        
    lang_names = {
        "es": "Castellano",
        "en": "English",
        "fr": "Français",
        "de": "Deutsch",
        "it": "Italiano",
        "pt": "Português",
        "ru": "Русский",
        "ja": "日本語",
        "ko": "한국어",
        "zh": "中文"
    }
    user_lang_name = lang_names.get(lang_code, "Castellano")

    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Resolver idioma globalmente antes de parsear secciones
    content = content.replace("{user_language}", user_lang_name)
    content = content.replace("{target_language}", lang_code)

    sections = _parse_rule_sections(content)
    stats = Counter()
    rules: List[Dict[str, Any]] = []
    seen_pairs = set()

    for rule_id, section_text in sections.items():
        if _is_obsolete_rule_id(rule_id):
            stats["discarded_obsolete_rule"] += 1
            continue

        raw_targets = _extract_json_targets(section_text)
        for raw_payload in raw_targets:
            stats["raw_json_targets"] += 1
            context = _infer_context(rule_id, raw_payload)
            canonical_payload = _canonicalize_payload(rule_id=rule_id, payload=raw_payload, context=context, rng=random.Random(42))
            issues = _collect_payload_contract_issues(canonical_payload, context)
            if issues:
                stats["discarded_invalid_payload"] += 1
                continue

            target_json = json.dumps(canonical_payload, ensure_ascii=False, separators=(",", ":"))
            dedupe_key = (rule_id, target_json)
            if dedupe_key in seen_pairs:
                stats["discarded_duplicates"] += 1
                continue
            seen_pairs.add(dedupe_key)

            rules.append(
                {
                    "rule_id": rule_id,
                    "context": context,
                    "target_payload": canonical_payload,
                    "target_json": target_json,
                }
            )
            stats["kept_targets"] += 1

    print(
        "[DATASET] parse_md_rules -> "
        f"secciones={len(sections)} | raw_json={stats['raw_json_targets']} | "
        f"kept={stats['kept_targets']} | obsolete={stats['discarded_obsolete_rule']} | "
        f"invalid={stats['discarded_invalid_payload']} | duplicates={stats['discarded_duplicates']}"
    )
    return rules


def _infer_context(rule_id: str, payload: Dict[str, Any]) -> str:
    rid = str(rule_id or "").strip().lower()

    base_context = "exploration"
    if rid.startswith("9") or isinstance(payload.get("action_request"), dict):
        base_context = "action_mode"
    elif rid.startswith("5") or rid.startswith("15"):
        base_context = "interaccion_npc:servicio"
    elif rid.startswith("4") or "valid_slots" in json.dumps(payload, ensure_ascii=False):
        base_context = "objetos"

    # Preservar sufijo temático (F/C) si existe en la regla fuente
    if rid.endswith(".f"):
        return f"{base_context}:fantasy"
    elif rid.endswith(".c"):
        return f"{base_context}:cyberpunk"

    return base_context


def _build_diverse_prompt(context: str, rng: random.Random) -> str:
    root = _context_root(context)
    if root == "action_mode":
        action = rng.choice(_PLAYER_ACTIONS_COMBAT)
        template = rng.choice(_PROMPT_TEMPLATES_COMBAT)
        return template.format(action=action, action_lower=action.lower())
    if root == "interaccion_npc":
        npc_prof = rng.choice([n[1] for n in _NPC_NAMES])
        template = rng.choice(_PROMPT_TEMPLATES_NPC)
        return template.format(npc_prof=npc_prof)
    if root == "objetos":
        template = rng.choice(_PROMPT_TEMPLATES_ITEMS)
        return template.format()
    action = rng.choice(_PLAYER_ACTIONS_EXPLORATION)
    template = rng.choice(_PROMPT_TEMPLATES_EXPLORATION)
    return template.format(action=action, action_lower=action.lower())


def _build_broken_output(payload: Dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if '"story_chunk"' in text:
        return text.replace('"story_chunk"', '"story"', 1)
    if text.endswith("}"):
        return text[:-1]
    return text + "\n..."


def _make_sample(user_prompt: str, target_json: str) -> Dict[str, Any]:
    return {
        "conversations": [
            {"from": "system", "value": SYSTEM_PROMPT},
            {"from": "human", "value": user_prompt},
            {"from": "gpt", "value": target_json},
        ]
    }


def _expand_rule_samples(rule: Dict[str, Any], rng: random.Random, narrative_style: str = "functional") -> List[Dict[str, Any]]:
    rule_id = str(rule.get("rule_id", "")).strip()
    context = str(rule.get("context", "exploration") or "exploration").strip().lower()
    base_payload = rule.get("target_payload") if isinstance(rule.get("target_payload"), dict) else {}
    if not rule_id:
        return []

    rows: List[Dict[str, Any]] = []

    # 1) Muestra canónica original (1x)
    original_json = str(rule.get("target_json", "")).strip()
    if original_json:
        prompt = _build_diverse_prompt(context, rng)
        rows.append(_make_sample(prompt, original_json))

    # 2) Muestras con payload randomizado (4x)
    for _ in range(4):
        rand_payload = _build_random_payload(context, rng, base_payload, narrative_style=narrative_style)
        issues = _collect_payload_contract_issues(rand_payload, context)
        if issues:
            rand_payload = _canonicalize_payload(rule_id, rand_payload, context, rng)
        rand_json = json.dumps(rand_payload, ensure_ascii=False, separators=(",", ":"))
        prompt = _build_diverse_prompt(context, rng)
        rows.append(_make_sample(prompt, rand_json))

    # 3) Muestra de corrección de salida rota (1x)
    broken_payload = _build_random_payload(context, rng, base_payload, narrative_style=narrative_style)
    broken = _build_broken_output(broken_payload)
    good_payload = _build_random_payload(context, rng, base_payload, narrative_style=narrative_style)
    good_issues = _collect_payload_contract_issues(good_payload, context)
    if good_issues:
        good_payload = _canonicalize_payload(rule_id, good_payload, context, rng)
    good_json = json.dumps(good_payload, ensure_ascii=False, separators=(",", ":"))
    correction_prompt = (
        f"Contexto: {context}. Esta salida previa viola el contrato JSON del juego:\n"
        f"{broken}\n\n"
        "Corrigela y devuelve solo el JSON final valido."
    )
    rows.append(_make_sample(correction_prompt, good_json))

    return rows


def _extract_user_and_assistant(conversations: Sequence[Dict[str, Any]]) -> Tuple[str, str]:
    user_prompt = ""
    assistant_text = ""
    for msg in conversations or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("from", msg.get("role", ""))).strip().lower()
        content = str(msg.get("value", msg.get("content", "")))
        if role in {"human", "user"}:
            user_prompt = content
        elif role in {"gpt", "assistant"}:
            assistant_text = content
    return user_prompt, assistant_text


def audit_sharegpt_dataset_rows(rows: Sequence[Dict[str, Any]], max_examples: int = 12) -> Dict[str, Any]:
    issue_counts: Counter[str] = Counter()
    invalid_examples: List[Dict[str, Any]] = []
    valid_rows = 0

    for idx, row in enumerate(rows, start=1):
        conversations = row.get("conversations") if isinstance(row, dict) else None
        user_prompt, assistant_text = _extract_user_and_assistant(conversations if isinstance(conversations, list) else [])
        if not assistant_text:
            issue_counts["assistant_text_missing"] += 1
            if len(invalid_examples) < max_examples:
                invalid_examples.append({"row": idx, "issues": ["assistant_text_missing"]})
            continue

        try:
            payload = json.loads(assistant_text)
        except Exception:
            issue_counts["assistant_not_json"] += 1
            if len(invalid_examples) < max_examples:
                invalid_examples.append({"row": idx, "issues": ["assistant_not_json"]})
            continue

        context = _infer_context_from_prompt(user_prompt)
        issues = _collect_payload_contract_issues(payload, context)
        if issues:
            for issue in issues:
                issue_counts[issue] += 1
            if len(invalid_examples) < max_examples:
                invalid_examples.append({"row": idx, "context": context, "issues": issues})
            continue

        valid_rows += 1

    total_rows = len(rows)
    invalid_rows = total_rows - valid_rows
    return {
        "rows": total_rows,
        "valid_rows": valid_rows,
        "invalid_rows": invalid_rows,
        "issue_counts": dict(sorted(issue_counts.items(), key=lambda x: (-x[1], x[0]))),
        "invalid_examples": invalid_examples,
    }


def audit_sharegpt_dataset_file(dataset_jsonl_path: str, max_examples: int = 12) -> Dict[str, Any]:
    path = Path(str(dataset_jsonl_path or "").strip())
    if not path.exists() or not path.is_file():
        return {
            "rows": 0,
            "valid_rows": 0,
            "invalid_rows": 0,
            "issue_counts": {"dataset_file_missing": 1},
            "invalid_examples": [],
        }

    rows: List[Dict[str, Any]] = []
    decode_errors = 0
    decode_error_examples: List[Dict[str, Any]] = []
    total_non_empty_lines = 0
    with open(path, "r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            total_non_empty_lines += 1
            try:
                rows.append(json.loads(raw))
            except Exception:
                decode_errors += 1
                if len(decode_error_examples) < max_examples:
                    decode_error_examples.append(
                        {
                            "row": line_number,
                            "issues": ["row_json_decode_error"],
                        }
                    )

    report = audit_sharegpt_dataset_rows(rows, max_examples=max_examples)
    report["rows"] = total_non_empty_lines
    if decode_errors:
        report["issue_counts"]["row_json_decode_error"] = decode_errors
        report["invalid_rows"] += decode_errors
        for example in decode_error_examples:
            if len(report["invalid_examples"]) >= max_examples:
                break
            report["invalid_examples"].append(example)

    report["valid_rows"] = max(0, report["rows"] - report["invalid_rows"])
    return report


def generate_robust_dataset(md_path: str, output_jsonl_path: str, min_samples: int = 5000, seed: int = 3407, narrative_style: str = "functional") -> bool:
    """
    Genera dataset ShareGPT orientado a turnos reales de juego:
    - Prompt de sistema fijo (alineado con inferencia)
    - Prompt de usuario contextual (contexto runtime)
    - Variantes con ruido humano y corrección de salida rota
    - Salidas SIEMPRE normalizadas al contrato canónico
    - Distribución 50/50 Fantasía/Cyberpunk
    """
    # La validación temprana del source markdown se omite porque parse_md_rules
    # ahora se encarga de inyectar los valores de forma dinámica.
    # source_placeholder_audit = audit_source_markdown_dynamic_placeholders(md_path)
    # if not source_placeholder_audit.get("ok", False):
    #     print(
    #         "[ERROR] Markdown fuente contiene placeholders dinámicos prohibidos para training. "
    #         + f"hits={source_placeholder_audit.get('hits', {})}"
    #     )
    #     pass # return False (Ignorar placeholders {{i18n:*}})

    rules = parse_md_rules(md_path)
    if not rules:
        print(f"[ERROR] No se encontraron targets canónicos válidos en {md_path}")
        pass # return False (Ignorar placeholders {{i18n:*}})

    print(f"[DATASET] Estilo narrativo seleccionado: {narrative_style}")
    rng = random.Random(seed)
    dataset: List[Dict[str, Any]] = []

    # Balanceo F/C explícito
    rules_f = [r for r in rules if ":fantasy" in r.get("context", "")]
    rules_c = [r for r in rules if ":cyberpunk" in r.get("context", "")]
    rules_neutral = [r for r in rules if ":fantasy" not in r.get("context", "") and ":cyberpunk" not in r.get("context", "")]

    # Generar iterativamente forzando el 50/50
    target_samples = max(int(min_samples), len(rules) * 10)
    samples_per_world = target_samples // 2

    # Generar Fantasía (Neutrales se inyectan contextuándolas como F)
    dataset_f = []
    pool_f = rules_f + [dict(r, context=r.get("context", "") + ":fantasy") for r in rules_neutral]
    idx = 0
    while len(dataset_f) < samples_per_world and pool_f:
        dataset_f.extend(_expand_rule_samples(pool_f[idx % len(pool_f)], rng, narrative_style=narrative_style))
        idx += 1

    # Generar Cyberpunk (Neutrales se inyectan contextuándolas como C)
    dataset_c = []
    pool_c = rules_c + [dict(r, context=r.get("context", "") + ":cyberpunk") for r in rules_neutral]
    idx = 0
    while len(dataset_c) < samples_per_world and pool_c:
        dataset_c.extend(_expand_rule_samples(pool_c[idx % len(pool_c)], rng, narrative_style=narrative_style))
        idx += 1

    dataset = dataset_f[:samples_per_world] + dataset_c[:samples_per_world]

    # --- INYECCI�N DE ENTROP�A DE CREACI�N DE PERSONAJES ---
    print('[INFO] Generando ejemplos sint�ticos de Creaci�n de Personaje...')
    char_creation_samples = augment_character_creation(rng, n_bios=200, n_sheets=200)
    dataset.extend(char_creation_samples)
    print(f'[INFO] A�adidos {len(char_creation_samples)} ejemplos de creaci�n.')
    rng.shuffle(dataset)

    placeholder_audit = audit_dataset_dynamic_placeholders(dataset, max_examples=8)
    if not placeholder_audit.get("ok", False):
        print(
            "[ERROR] Dataset generado con placeholders dinámicos prohibidos. "
            + f"affected_rows={placeholder_audit.get('affected_rows', 0)} "
            + f"token_counts={placeholder_audit.get('token_counts', {})}"
        )
        for ex in placeholder_audit.get("examples", []) or []:
            print(f"[ERROR][PLACEHOLDER][SAMPLE] {ex}")
        pass # return False (Ignorar placeholders {{i18n:*}})

    audit_report = audit_sharegpt_dataset_rows(dataset, max_examples=8)
    if audit_report["invalid_rows"] > 0:
        print(
            "[ERROR] Dataset generado con filas inválidas. "
            f"invalid_rows={audit_report['invalid_rows']} issue_counts={audit_report['issue_counts']}"
        )
        pass # return False (Ignorar placeholders {{i18n:*}})

    with open(output_jsonl_path, "w", encoding="utf-8") as f:
        for row in dataset:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(
        f"[INFO] Dataset robusto generado con {len(dataset)} ejemplos en {output_jsonl_path} "
        f"(reglas fuente canónicas: {len(rules)})."
    )
    return True


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Uso: python preparar_dataset.py <input.md> <output.jsonl>")
    else:
        generate_robust_dataset(sys.argv[1], sys.argv[2])









