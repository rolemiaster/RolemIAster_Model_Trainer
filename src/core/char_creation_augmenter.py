import json
import random
from typing import Any, Dict, List

# Constantes básicas para entropía narrativa mínima
NAMES_F = ["Kael", "Thael", "Vira", "Borin", "Elara", "Grom", "Sira", "Faelen"]
PROF_F = ["Mercenario", "Cazador", "Mago Errante", "Pícaro", "Caballero", "Herrero"]
WORLD_F = ["Hilidania", "Aeloria", "Doran", "Reinos de Ámbar", "Tierra de los Valles"]

NAMES_C = ["Jax", "Zed", "Nova", "Xon", "Kuro", "Vex", "Rin", "Cipher"]
PROF_C = ["Netrunner", "Mercenario Corporativo", "Tecnocirujano", "Fixer", "Nómada"]
WORLD_C = ["Neo-Veridia", "Ciudad de Cromo", "Sector 9", "Mega-Tokyo", "The Sprawl"]

# Habilidades por mundo
SKILLS_F = ["Arma 1M", "Esquivar", "Saber Popular", "Atletismo", "Buscar", "Arma 2M", "Magia", "Sigilo"]
SKILLS_C = ["Armas a distancia", "Hackear", "Ingeniería", "Conducir", "Sigilo", "Cibertecnología", "Pelea"]

# Atributos canónicos
CANONICAL_ATTRIBUTES = ["Fuerza", "Constitucion", "Tamano", "Destreza", "Inteligencia", "Poder", "Carisma"]

def get_random_item_f(rng: random.Random) -> Dict[str, Any]:
    types = ["weapon", "armor", "consumible"]
    item_type = rng.choice(types)
    item_id = f"item_{rng.randint(1000, 9999)}"
    
    if item_type == "weapon":
        name = rng.choice(["Espada Corta", "Hacha de Batalla", "Daga Rúnica"])
        icon = "⚔️" if "Espada" in name or "Daga" in name else ("🪓" if "Hacha" in name else "🗡️")
        return {
            "entity_id": item_id,
            "name": name,
            "icon": icon,
            "type": "weapon",
            "valid_slots": [rng.choice(["weapon_right", "weapon_left", "backpack"])],
            "modifiers": [
                {
                    "type": "damage",
                    "kind": rng.choice(["cortante", "contundente", "perforante"]),
                    "amount": rng.choice(["1d6", "1d8", "1d6+2"])
                }
            ],
            "container_id": "player",
            "equipped": rng.choice([True, False])
        }
    elif item_type == "armor":
        name = rng.choice(["Cota de Malla", "Peto de Cuero", "Túnica Mágica"])
        icon = "👕" if "Cota" in name or "Peto" in name else ("🛡️" if "Túnica" in name else "👕")
        return {
            "entity_id": item_id,
            "name": name,
            "icon": icon,
            "type": "armor",
            "valid_slots": [rng.choice(["torso", "head", "backpack"])],
            "modifiers": [
                {
                    "type": "armor",
                    "amount": rng.randint(1, 4)
                }
            ],
            "container_id": "player",
            "equipped": rng.choice([True, False])
        }
    else:
        name = rng.choice(["Poción de Vida", "Antídoto", "Elixir de Fuerza"])
        return {
            "entity_id": item_id,
            "name": name,
            "icon": "🧪",
            "type": "consumible",
            "valid_slots": ["belt", "backpack"],
            "modifiers": [
                {
                    "type": "modifier",
                    "target": "attribute",
                    "name": rng.choice(CANONICAL_ATTRIBUTES),
                    "amount": rng.randint(1, 3)
                }
            ],
            "container_id": "player",
            "equipped": False
        }

def get_random_item_c(rng: random.Random) -> Dict[str, Any]:
    types = ["weapon", "implant", "consumible"]
    item_type = rng.choice(types)
    item_id = f"item_{rng.randint(1000, 9999)}"
    
    if item_type == "weapon":
        name = rng.choice(["Pistola 9mm", "Rifle de Asalto", "Cuchillo Monofilar"])
        icon = "🔫" if "Pistola" in name or "Rifle" in name else "🔪"
        return {
            "entity_id": item_id,
            "name": name,
            "icon": icon,
            "type": "weapon",
            "valid_slots": [rng.choice(["weapon_right", "weapon_left", "backpack"])],
            "modifiers": [
                {
                    "type": "damage",
                    "kind": rng.choice(["balistico", "cortante", "energia"]),
                    "amount": rng.choice(["2d6", "1d10", "2d6+2"])
                }
            ],
            "container_id": "player",
            "equipped": rng.choice([True, False])
        }
    elif item_type == "implant":
        name = rng.choice(["Implante Óptico", "Brazo Cibernético", "Reflejos Subdermales"])
        icon = "👁️" if "Óptico" in name else ("🦾" if "Brazo" in name else "⚙️")
        return {
            "entity_id": item_id,
            "name": name,
            "icon": icon,
            "type": "implant",
            "valid_slots": [rng.choice(["implant_head", "implant_arm_right", "implant_nervous"])],
            "modifiers": [
                {
                    "type": "modifier",
                    "target": "attribute",
                    "name": rng.choice(CANONICAL_ATTRIBUTES),
                    "amount": rng.randint(1, 3)
                }
            ],
            "coste_de_humanidad": rng.randint(1, 5),
            "container_id": "player",
            "equipped": True
        }
    else:
        name = rng.choice(["Stim de Combate", "Parche de Curación", "Batería de Respaldo"])
        return {
            "entity_id": item_id,
            "name": name,
            "icon": "🧪",
            "type": "consumible",
            "valid_slots": ["belt", "backpack"],
            "modifiers": [
                {
                    "type": "modifier",
                    "target": "characteristic",
                    "name": "hp",
                    "amount": rng.randint(10, 30)
                }
            ],
            "container_id": "player",
            "equipped": False
        }

def generate_bio_sample(rng: random.Random, setting: str) -> Dict[str, Any]:
    is_fantasy = (setting == "Fantasía Medieval")
    
    world = rng.choice(WORLD_F) if is_fantasy else rng.choice(WORLD_C)
    prof = rng.choice(PROF_F) if is_fantasy else rng.choice(PROF_C)
    name = rng.choice(NAMES_F) if is_fantasy else rng.choice(NAMES_C)
    
    system_prompt = f"""ÍNDICE DE REGLAS ACTIVAS:
[11.4] Formato Biográfico (Fase 1 - Solo Descripción)

DIRECTIVA DE MUNDO: Entorno de tipo {setting}."""

    seed = rng.randint(100000, 999999)
    diff = rng.choice(["difficulty_easy", "difficulty_normal", "difficulty_hard"])
    timestamp = f"{rng.randint(0,23):02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d}.{rng.randint(0,999):03d}"
    
    user_prompt = f"""Por favor, genera la descripción completa del personaje (nombre, edad, profesión, físico, psicología y trasfondo) basándote en la siguiente información de su entorno y la ambientación seleccionada.

Mundo: {world}
Dificultad: {diff}
Semilla de Variabilidad: {seed}
Marca Temporal (Entropía): {timestamp}
(Narrate in Castellano)"""

    target_json = {
        "name": name,
        "age": rng.randint(18, 60),
        "profession": prof,
        "physical_description": f"Un {prof.lower()} de aspecto curtido." if is_fantasy else f"Un {prof.lower()} con modificaciones visibles.",
        "psychological_description": "Siempre alerta y desconfiado de los extraños.",
        "background": f"Nacido en {world}, aprendió a sobrevivir desde joven."
    }
    
    return {
        "conversations": [
            {"from": "system", "value": system_prompt},
            {"from": "human", "value": user_prompt},
            {"from": "gpt", "value": json.dumps(target_json, ensure_ascii=False, indent=2)}
        ]
    }

def generate_sheet_sample(rng: random.Random, setting: str, bio_json: Dict[str, Any]) -> Dict[str, Any]:
    is_fantasy = (setting == "Fantasía Medieval")
    world = rng.choice(WORLD_F) if is_fantasy else rng.choice(WORLD_C)
    diff = rng.choice(["difficulty_easy", "difficulty_normal", "difficulty_hard"])
    
    system_prompt = f"""ÍNDICE DE REGLAS ACTIVAS:
[11] Generación de Personajes (Base)
[11.1] Reglas de Atributos y Habilidades
[11.2] Reglas de Inventario Inicial y Economía
[1.2] Restricción de Extracción

DIRECTIVA DE MUNDO: Entorno de tipo {setting}."""

    skills_pool = SKILLS_F if is_fantasy else SKILLS_C
    skills_list_str = "\n".join([f"- `{s}`" for s in skills_pool])
    
    user_prompt = f"""Por favor, genera las estadísticas, habilidades e inventario inicial del personaje basándote en su biografía.

Dificultad: {diff}

Edad: {bio_json['age']}

Profesión: {bio_json['profession']}

Descripción Física:
{bio_json['physical_description']}

Descripción Psicológica:
{bio_json['psychological_description']}

Trasfondo:
{bio_json['background']}

---
**Dinero Inicial:**
Debes asignar entre 20 y 100 de dinero.

**Habilidades Permitidas en este Mundo:**
{skills_list_str}

**INSTRUCCIONES ESTRUCTURALES ESTRICTAS:**
- Solo puedes usar habilidades de la lista permitida anterior.
- NO INVENTES habilidades que no estén en la lista.
(Narrate in Castellano)"""

    attributes = {attr: {"value": rng.randint(8, 18), "experience": 0.0} for attr in CANONICAL_ATTRIBUTES}
    
    selected_skills = rng.sample(skills_pool, rng.randint(4, 6))
    skill_points = {s: rng.randint(10, 30) for s in selected_skills}
    
    num_items = rng.randint(5, 8)
    inventory = []
    for _ in range(num_items):
        inventory.append(get_random_item_f(rng) if is_fantasy else get_random_item_c(rng))
        
    target_json = {
        "attributes": attributes,
        "skill_points": skill_points,
        "initial_inventory": inventory,
        "currency": rng.randint(20, 100)
    }
    
    return {
        "conversations": [
            {"from": "system", "value": system_prompt},
            {"from": "human", "value": user_prompt},
            {"from": "gpt", "value": json.dumps(target_json, ensure_ascii=False, indent=2)}
        ]
    }

def augment_character_creation(rng: random.Random, n_bios: int = 150, n_sheets: int = 150) -> List[Dict[str, Any]]:
    rows = []
    
    # Generar Bios
    bios_f = []
    bios_c = []
    for _ in range(n_bios // 2):
        bio_f = generate_bio_sample(rng, "Fantasía Medieval")
        bio_c = generate_bio_sample(rng, "Cyberpunk")
        rows.append(bio_f)
        rows.append(bio_c)
        bios_f.append(json.loads(bio_f["conversations"][2]["value"]))
        bios_c.append(json.loads(bio_c["conversations"][2]["value"]))
        
    # Generar Sheets usando las Bios generadas
    for i in range(n_sheets // 2):
        bio_f = rng.choice(bios_f)
        bio_c = rng.choice(bios_c)
        rows.append(generate_sheet_sample(rng, "Fantasía Medieval", bio_f))
        rows.append(generate_sheet_sample(rng, "Cyberpunk", bio_c))
        
    return rows
