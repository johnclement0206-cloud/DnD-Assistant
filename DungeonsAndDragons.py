import json
import math
import uuid
import random
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple

API_BASE = "https://www.dnd5eapi.co"
API_TIMEOUT = 5  # seconds

HAVE_REQUESTS = False
try:
    import requests  # type: ignore
    from requests.exceptions import RequestException  # type: ignore
    HAVE_REQUESTS = True
except Exception:
    requests = None  # type: ignore
    RequestException = Exception  # type: ignore

import urllib.request
import urllib.error

SESSION = requests.Session() if HAVE_REQUESTS else None # type: ignore

def api_get(url: str):
    if HAVE_REQUESTS and SESSION is not None:
        return SESSION.get(url, timeout=API_TIMEOUT)

    class _SimpleResponse:
        def __init__(self, content: bytes, code: int = 200):
            self._content = content
            self.status_code = code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RequestException(f"HTTP {self.status_code}")

        def json(self):
            return json.loads(self._content.decode("utf-8"))

    try:
        with urllib.request.urlopen(url, timeout=API_TIMEOUT) as resp:
            content = resp.read()
            code = getattr(resp, "getcode", lambda: 200)()
            return _SimpleResponse(content, code)
    except urllib.error.HTTPError as e:
        raise RequestException(f"HTTPError: {e.code} {e.reason}")
    except Exception as e:
        raise RequestException(e)


def ability_mod(score: int) -> int:
    return (score - 10) // 2

def proficiency_bonus(level: int) -> int:
    return 2 + (max(0, level - 1) // 4)

XP_LEVELS = [0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000,
             85000, 100000, 120000, 140000, 165000, 195000, 225000, 265000, 305000, 355000]

CONDITIONS = {
    "blinded", "charmed", "deafened", "exhaustion", "frightened",
    "grappled", "incapacitated", "invisible", "paralyzed", "petrified",
    "poisoned", "prone", "restrained", "stunned", "unconscious"
}

SPELL_INDEX: Dict[str, str] = {}      # maps normalized name -> api index
CLASS_INDEX: Dict[str, str] = {}      # maps class name lower -> index
SPELL_LIBRARY: Dict[str, "Spell"] = {}  # cached detailed Spell objects

@dataclass
class Item:
    name: str
    description: str = ""
    quantity: int = 1
    consumable: bool = False
    weight: float = 0.0
    properties: Dict[str, Any] = field(default_factory=dict)

    def use(self) -> bool:
        if self.quantity <= 0:
            return False
        if self.consumable:
            self.quantity -= 1
        return True

@dataclass
class Spell:
    name: str
    level: int
    school: str = ""
    cast_time: str = "1 action"
    range: str = "Self"
    duration: str = "Instantaneous"
    components: Dict[str, bool] = field(default_factory=lambda: {"V": True, "S": True, "M": False})
    concentration: bool = False
    description: str = ""
    damage_expr: Optional[str] = None     # e.g. "8d6"
    damage_type: Optional[str] = None     # e.g. "fire"
    save: Optional[str] = None            # e.g. "DEX" or "CON"
    save_half: bool = False               # whether successful save halves damage

def normalize_key(s: str) -> str:
    return s.strip().lower().replace(" ", "-").replace("'", "").replace(",", "")

def init_api_indexes():
    global SPELL_INDEX, CLASS_INDEX
    try:
        r = api_get(f"{API_BASE}/api/spells")
        r.raise_for_status()
        data = r.json()
        spells = data.get("results", [])
        SPELL_INDEX = {}
        for s in spells:
            name = s.get("name", "")
            idx = s.get("index", "")
            if name and idx:
                SPELL_INDEX[name.lower()] = idx
                SPELL_INDEX[normalize_key(name)] = idx

        r = api_get(f"{API_BASE}/api/classes")
        r.raise_for_status()
        data = r.json()
        classes = data.get("results", [])
        CLASS_INDEX = {}
        for c in classes:
            name = c.get("name", "")
            idx = c.get("index", "")
            if name and idx:
                CLASS_INDEX[name.lower()] = idx
                CLASS_INDEX[normalize_key(name)] = idx
    except RequestException:
        return
    except Exception:
        return

def fetch_spell_details_from_api(index: str) -> Optional[Spell]:
    try:
        r = api_get(f"{API_BASE}/api/spells/{index}")
        r.raise_for_status()
        data = r.json()
        name = data.get("name", "Unknown Spell")
        level = int(data.get("level", 0))
        school = (data.get("school") or {}).get("name", "")
        cast_time = data.get("casting_time", "1 action")
        rng = data.get("range", "Self")
        duration = data.get("duration", "Instantaneous")
        components_list = data.get("components", [])
        components = {"V": False, "S": False, "M": False}
        for c in components_list:
            if c in components:
                components[c] = True
        concentration = bool(data.get("concentration", False))
        desc = data.get("desc", [])
        desc_text = "\n\n".join(desc) if isinstance(desc, list) else str(desc or "")
        material = data.get("material")
        if material:
            desc_text = (desc_text + f"\n\nMaterial: {material}").strip()
        # damage: try to extract a damage expression from damage_at_slot_level or damage_at_character_level
        damage_expr = None
        damage_type = None
        dmg = data.get("damage")
        if isinstance(dmg, dict):
            dt = dmg.get("damage_type")
            if isinstance(dt, dict):
                damage_type = dt.get("name")
            dal = dmg.get("damage_at_slot_level") or dmg.get("damage_at_character_level")
            if isinstance(dal, dict):
                key = str(level) if str(level) in dal else next(iter(dal.keys()), None)
                if key:
                    damage_expr = dal.get(key)
        save = None
        dc = data.get("dc")
        if isinstance(dc, dict):
            dc_type = dc.get("dc_type")
            if isinstance(dc_type, dict):
                name = dc_type.get("name", "")  # e.g. "Constitution"
                save = name[:3].upper() if name else None
        save_half = "half" in desc_text.lower()
        sp = Spell(
            name=name,
            level=level,
            school=school,
            cast_time=cast_time,
            range=rng,
            duration=duration,
            components=components,
            concentration=concentration,
            description=desc_text,
            damage_expr=damage_expr,
            damage_type=damage_type,
            save=save,
            save_half=save_half
        )
        return sp
    except RequestException:
        return None
    except Exception:
        return None

def get_spell_by_name(name: str) -> Optional[Spell]:
    """
    Lookup a spell by name, using local cache first, then API (lazy).
    Normalizes name and supports space/hyphen variants.
    """
    if not name:
        return None
    key = name.strip().lower()
    if key in SPELL_LIBRARY:
        return SPELL_LIBRARY[key]
    norm = normalize_key(name)
    idx = SPELL_INDEX.get(key) or SPELL_INDEX.get(norm)
    if not idx:
        for k, v in SPELL_INDEX.items():
            if k == key or k == norm:
                idx = v
                break
        if not idx:
            candidate = name.lower().replace(" ", "-")
            idx = SPELL_INDEX.get(candidate)
    if idx:
        sp = fetch_spell_details_from_api(idx)
        if sp:
            SPELL_LIBRARY[sp.name.lower()] = sp
            SPELL_LIBRARY[normalize_key(sp.name)] = sp
            return sp
    return SPELL_LIBRARY.get(key) or SPELL_LIBRARY.get(norm)

def _parse_components_field(comp_field: Any) -> Dict[str, bool]:
    comps = {"V": False, "S": False, "M": False}
    if not comp_field:
        return comps
    if isinstance(comp_field, str):
        s = comp_field.upper()
        comps["V"] = "V" in s
        comps["S"] = "S" in s
        comps["M"] = "M" in s or "M(" in s or " M" in s
    elif isinstance(comp_field, (list, tuple, set)):
        for c in comp_field:
            k = str(c).strip().upper()
            if k in comps:
                comps[k] = True
    return comps

def load_spell_library(path: str) -> None:
    global SPELL_LIBRARY, SPELL_INDEX
    SPELL_LIBRARY = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            for name, entry in data.items():
                if not isinstance(entry, dict):
                    entry = {}
                try:
                    level = int(entry.get("level", 0)) if entry.get("level") is not None else 0
                except Exception:
                    level = 0
                school = (entry.get("school") or "") if isinstance(entry, dict) else ""
                cast_time = entry.get("casting_time") or entry.get("casting time") or "1 action"
                rng = entry.get("range", "Self")
                duration = entry.get("duration", "Instantaneous")
                components = _parse_components_field(entry.get("components"))
                concentration = bool(entry.get("duration") and "concentration" in str(entry.get("duration", "")).lower())
                desc = entry.get("description") or entry.get("desc") or ""
                sp = Spell(
                    name=name,
                    level=level,
                    school=school,
                    cast_time=cast_time,
                    range=rng,
                    duration=duration,
                    components=components,
                    concentration=concentration,
                    description=str(desc),
                    damage_expr=None,
                    damage_type=None,
                    save=None,
                    save_half=("half" in str(desc).lower())
                )
                SPELL_LIBRARY[name.lower()] = sp
                SPELL_LIBRARY[normalize_key(name)] = sp

        elif isinstance(data, list):
            for entry in data:
                name = entry.get("name") or entry.get("spell") or ""
                if not name:
                    continue
                try:
                    level = int(entry.get("level", 0))
                except Exception:
                    level = 0
                school = entry.get("school", "") or ""
                cast_time = entry.get("cast_time", "1 action")
                rng = entry.get("range", "Self")
                duration = entry.get("duration", "Instantaneous")
                components = entry.get("components", {"V": True, "S": True, "M": False})
                if isinstance(components, str):
                    components = _parse_components_field(components)
                concentration = bool(entry.get("concentration", False))
                desc = entry.get("description", "") or entry.get("desc", "")
                sp = Spell(
                    name=name,
                    level=level,
                    school=school,
                    cast_time=cast_time,
                    range=rng,
                    duration=duration,
                    components=components,
                    concentration=concentration,
                    description=str(desc),
                    damage_expr=entry.get("damage_expr"),
                    damage_type=entry.get("damage_type"),
                    save=entry.get("save"),
                    save_half=bool(entry.get("save_half", False)),
                )
                SPELL_LIBRARY[name.lower()] = sp
                SPELL_LIBRARY[normalize_key(name)] = sp

        else:
            SPELL_LIBRARY = {}
    except FileNotFoundError:
        SPELL_LIBRARY = {}
    except Exception:
        SPELL_LIBRARY = {}

    SPELL_INDEX = {}
    for key in list(SPELL_LIBRARY.keys()):
        if key == normalize_key(key):
            SPELL_INDEX[key] = key
        else:
            SPELL_INDEX[key] = normalize_key(key)

def _roll_damage_expr(expr: Optional[str]) -> Tuple[int, str]:
    if not expr:
        return 0, "no damage"
    total, detail = roll_expression(expr)
    return total, detail

@dataclass
class Character:
    name: str
    player_name: Optional[str] = None
    char_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    level: int = 1
    race: str = ""
    char_class: str = ""
    max_hp: int = 8
    current_hp: int = 8
    temp_hp: int = 0
    hit_die: int = 8
    hit_die_total: int = 1
    armor_class: int = 10
    speed: int = 30
    abilities: Dict[str, int] = field(default_factory=lambda: {
        "STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10
    })
    saves_proficiency: Dict[str, bool] = field(default_factory=lambda: {
        "STR": False, "DEX": False, "CON": False, "INT": False, "WIS": False, "CHA": False
    })
    skill_proficiency: Dict[str, bool] = field(default_factory=dict)
    inspiration: bool = False
    conditions: Dict[str, int] = field(default_factory=dict)
    inventory: List[Item] = field(default_factory=list)
    spells: List[Spell] = field(default_factory=list)
    spell_slots: Dict[int, Dict[str, int]] = field(default_factory=dict)
    concentration: Optional[Dict[str, Any]] = None
    xp: int = 0

    def ability_mod(self, ability: str) -> int:
        return ability_mod(self.abilities.get(ability.upper(), 10))

    def prof_bonus(self) -> int:
        return proficiency_bonus(self.level)

    def saving_throw_modifier(self, ability: str) -> int:
        base = self.ability_mod(ability)
        if self.saves_proficiency.get(ability.upper(), False):
            base += self.prof_bonus()
        return base

    def skill_modifier(self, skill: str, associated_ability: str, expertise: bool = False) -> int:
        mod = self.ability_mod(associated_ability)
        if self.skill_proficiency.get(skill.lower(), False):
            mod += self.prof_bonus()
        if expertise:
            mod += self.prof_bonus()
        return mod

    def apply_damage(self, amount: int, damage_round: Optional[int] = None, con_save_roll: Optional[int] = None) -> Dict[str, Any]:
        remaining = amount
        if self.temp_hp > 0:
            used = min(self.temp_hp, remaining)
            self.temp_hp -= used
            remaining -= used
        self.current_hp = max(0, self.current_hp - remaining)
        concentration_broken = False
        if self.concentration:
            dc = max(10, math.ceil(amount / 2))
            if con_save_roll is not None and con_save_roll < dc:
                concentration_broken = True
                self.concentration = None
        return {"current_hp": self.current_hp, "temp_hp": self.temp_hp, "concentration_broken": concentration_broken}

    def heal(self, amount: int) -> Dict[str, Any]:
        self.current_hp = min(self.max_hp, self.current_hp + amount)
        return {"current_hp": self.current_hp}

    def short_rest(self, hit_die_rolls: List[int], recovered_hp_per_roll: Optional[List[int]] = None) -> None:
        con_mod = self.ability_mod("CON")
        for i, r in enumerate(hit_die_rolls):
            if self.hit_die_total <= 0:
                break
            self.hit_die_total -= 1
            recovered = recovered_hp_per_roll[i] if recovered_hp_per_roll and i < len(recovered_hp_per_roll) else r + con_mod
            self.heal(max(0, recovered))

    def long_rest(self, restore_full_hp: bool = True, restore_spell_slots: bool = True) -> None:
        if restore_full_hp:
            self.current_hp = self.max_hp
        if restore_spell_slots:
            for _, val in self.spell_slots.items():
                val["current"] = val.get("max", 0)
        self.temp_hp = 0
        self.concentration = None
        self.conditions.clear()

    def add_condition(self, condition: str, rounds: int = -1) -> None:
        if condition in CONDITIONS:
            self.conditions[condition] = rounds

    def remove_condition(self, condition: str) -> None:
        self.conditions.pop(condition, None)

    def tick_conditions(self) -> None:
        remove = []
        for cond, rounds in list(self.conditions.items()):
            if rounds > 0:
                self.conditions[cond] = rounds - 1
                if self.conditions[cond] <= 0:
                    remove.append(cond)
        for c in remove:
            self.conditions.pop(c, None)

    def add_spell(self, spell: Spell) -> None:
        self.spells.append(spell)

    def use_spell_slot(self, level: int) -> bool:
        slot = self.spell_slots.get(level)
        if not slot or slot.get("current", 0) <= 0:
            return False
        slot["current"] -= 1
        return True

    def cast_spell(self, spell_name: str, encounter: "CombatEncounter", targets: List[str], slot_level: Optional[int] = None, caster_ability: str = "INT") -> Dict[str, Any]:
        return encounter.cast_spell(self.char_id, spell_name, slot_level, targets, caster_ability)

    def start_concentration(self, spell: Spell, save_dc: int, started_at_round: int) -> None:
        self.concentration = {"spell": spell, "save_dc": save_dc, "started_at_round": started_at_round}

    def try_level_up(self) -> bool:
        new_level = self.level
        for lvl in range(self.level + 1, 21):
            if self.xp >= XP_LEVELS[lvl - 1]:
                new_level = lvl
            else:
                break
        if new_level > self.level:
            old_level = self.level
            self.level = new_level
            avg = math.ceil(self.hit_die / 2) + 1
            self.max_hp += avg * (new_level - old_level)
            return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["inventory"] = [asdict(i) for i in self.inventory]
        d["spells"] = [asdict(s) for s in self.spells]
        return d

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Character":
        inv = [Item(**i) for i in data.get("inventory", [])]
        spells = [Spell(**s) for s in data.get("spells", [])]
        c = Character(
            name=data.get("name", ""),
            player_name=data.get("player_name"),
            char_id=data.get("char_id", str(uuid.uuid4())),
            level=data.get("level", 1),
            race=data.get("race", ""),
            char_class=data.get("char_class", ""),
            max_hp=data.get("max_hp", 8),
            current_hp=data.get("current_hp", 8),
            temp_hp=data.get("temp_hp", 0),
            hit_die=data.get("hit_die", 8),
            hit_die_total=data.get("hit_die_total", 1),
            armor_class=data.get("armor_class", 10),
            speed=data.get("speed", 30),
            abilities=data.get("abilities", {"STR":10,"DEX":10,"CON":10,"INT":10,"WIS":10,"CHA":10}),
            saves_proficiency=data.get("saves_proficiency", {}),
            skill_proficiency=data.get("skill_proficiency", {}),
            inspiration=data.get("inspiration", False),
            conditions=data.get("conditions", {}),
            inventory=inv,
            spells=spells,
            spell_slots={int(k):v for k,v in data.get("spell_slots", {}).items()},
            concentration=data.get("concentration"),
            xp=data.get("xp", 0),
        )
        return c

@dataclass
class Combatant:
    character: Character
    initiative: Optional[int] = None
    alive: bool = True
    is_npc: bool = False
    token_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"character": self.character.to_dict(), "initiative": self.initiative, "alive": self.alive, "is_npc": self.is_npc, "token_id": self.token_id}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Combatant":
        return Combatant(character=Character.from_dict(data["character"]), initiative=data.get("initiative"), alive=data.get("alive", True), is_npc=data.get("is_npc", False), token_id=data.get("token_id"))

class CombatEncounter:
    def __init__(self, name: str = "Encounter"):
        self.name = name
        self.combatants: List[Combatant] = []
        self.round: int = 0
        self.turn_index: int = 0

    def add_combatant(self, combatant: Combatant) -> None:
        self.combatants.append(combatant)

    def remove_combatant(self, char_id: str) -> None:
        self.combatants = [c for c in self.combatants if c.character.char_id != char_id]

    def start(self, initiatives: Dict[str, int]) -> None:
        for c in self.combatants:
            c.initiative = initiatives.get(c.character.char_id, 0)
        self.combatants.sort(key=lambda c: (c.initiative if c.initiative is not None else -999, c.character.ability_mod("DEX")), reverse=True)
        self.round = 1
        self.turn_index = 0

    def next_turn(self) -> Combatant:
        if not self.combatants:
            raise RuntimeError("No combatants in encounter")
        self.turn_index = (self.turn_index + 1) % len(self.combatants)
        if self.turn_index == 0:
            self.round += 1
            for c in self.combatants:
                c.character.tick_conditions()
        return self.current_combatant()

    def previous_turn(self) -> Combatant:
        if not self.combatants:
            raise RuntimeError("No combatants in encounter")
        self.turn_index = (self.turn_index - 1) % len(self.combatants)
        return self.current_combatant()

    def current_combatant(self) -> Combatant:
        if not self.combatants:
            raise RuntimeError("No combatants in encounter")
        if self.turn_index < 0 or self.turn_index >= len(self.combatants):
            self.turn_index = 0
        return self.combatants[self.turn_index]

    def perform_attack(self, attacker_id: str, defender_id: str, attack_roll_total: int, damage: int, crit: bool = False) -> Dict[str, Any]:
        attacker = next((c for c in self.combatants if c.character.char_id == attacker_id), None)
        defender = next((c for c in self.combatants if c.character.char_id == defender_id), None)
        if not attacker or not defender:
            raise ValueError("Attacker or defender not in encounter")
        hit = attack_roll_total >= defender.character.armor_class
        applied_damage = damage * (2 if crit else 1) if hit else 0
        res = {"hit": hit, "applied_damage": applied_damage, "defender_before_hp": defender.character.current_hp}
        if hit:
            dmg_res = defender.character.apply_damage(applied_damage)
            res.update(dmg_res)
            if defender.character.current_hp <= 0:
                defender.alive = False
                res["defender_alive"] = False
            else:
                res["defender_alive"] = True
        return res

    def cast_spell(self, caster_id: str, spell_name: str, slot_level: Optional[int], target_ids: List[str], caster_ability: str = "INT") -> Dict[str, Any]:
        caster = next((c for c in self.combatants if c.character.char_id == caster_id), None)
        if not caster:
            raise ValueError("Caster not in encounter")
        sp = None
        for s in caster.character.spells:
            if s.name.lower() == spell_name.lower():
                sp = s
                break
        if sp is None:
            sp = get_spell_by_name(spell_name)
        if sp is None:
            raise ValueError(f"Spell '{spell_name}' not found for caster or API/library")

        if sp.level > 0:
            lvl_to_use = slot_level if slot_level and slot_level >= sp.level else sp.level
            if not caster.character.use_spell_slot(lvl_to_use):
                return {"error": f"No spell slot level {lvl_to_use} available"}

        results: Dict[str, Any] = {"spell": sp.name, "caster": caster.character.name, "targets": {}}
        dc = 8 + caster.character.prof_bonus() + caster.character.ability_mod(caster_ability)

        for tid in target_ids:
            tgt = next((c for c in self.combatants if c.character.char_id == tid), None)
            if not tgt:
                results["targets"][tid] = {"error": "target not in encounter"}
                continue
            save_succeeded = None
            save_roll_val = None
            damage_done = 0
            detail = ""
            if sp.save:
                roll = random.randint(1, 20)
                save_roll_val = roll + tgt.character.saving_throw_modifier(sp.save)
                save_succeeded = save_roll_val >= dc
            dmg_total, dmg_detail = _roll_damage_expr(sp.damage_expr)
            if sp.save and sp.save_half and save_succeeded:
                damage_done = max(0, dmg_total // 2)
            else:
                damage_done = dmg_total if (sp.damage_expr and (not sp.save or not save_succeeded or not sp.save_half)) else 0
            applied = tgt.character.apply_damage(damage_done, con_save_roll=save_roll_val)
            if tgt.character.current_hp <= 0:
                tgt.alive = False
            results["targets"][tid] = {
                "target_name": tgt.character.name,
                "save_required": bool(sp.save),
                "save_roll_total": save_roll_val,
                "save_succeeded": save_succeeded,
                "damage_roll": dmg_detail,
                "damage_applied": damage_done,
                "after": applied
            }
        return results

    def apply_area_damage(self, damage_map: Dict[str, int], con_saves: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        results = {}
        for tid, dmg in damage_map.items():
            target = next((c for c in self.combatants if c.character.char_id == tid), None)
            if not target:
                continue
            con_roll = con_saves.get(tid) if con_saves else None
            res = target.character.apply_damage(dmg, con_save_roll=con_roll)
            if target.character.current_hp <= 0:
                target.alive = False
            results[tid] = res
        return results

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "round": self.round, "turn_index": self.turn_index, "combatants": [c.to_dict() for c in self.combatants]}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "CombatEncounter":
        e = CombatEncounter(name=data.get("name", "Encounter"))
        e.round = data.get("round", 0)
        e.turn_index = data.get("turn_index", 0)
        e.combatants = [Combatant.from_dict(c) for c in data.get("combatants", [])]
        return e

class Game:
    def __init__(self, name: str = "Campaign"):
        self.name = name
        self.characters: Dict[str, Character] = {}
        self.encounters: Dict[str, CombatEncounter] = {}
        self.party: List[str] = []

    def add_character(self, char: Character) -> str:
        self.characters[char.char_id] = char
        return char.char_id

    def remove_character(self, char_id: str) -> None:
        self.characters.pop(char_id, None)
        if char_id in self.party:
            self.party.remove(char_id)

    def create_encounter(self, encounter_name: str) -> str:
        eid = str(uuid.uuid4())
        self.encounters[eid] = CombatEncounter(encounter_name)
        return eid

    def add_to_encounter(self, encounter_id: str, char_id: str, is_npc: bool = False) -> None:
        encounter = self.encounters.get(encounter_id)
        char = self.characters.get(char_id)
        if encounter is None or char is None:
            raise ValueError("Encounter or character not found")
        encounter.add_combatant(Combatant(character=char, is_npc=is_npc))

    def save(self, path: str) -> None:
        data = {
            "name": self.name,
            "characters": {cid: ch.to_dict() for cid, ch in self.characters.items()},
            "encounters": {eid: enc.to_dict() for eid, enc in self.encounters.items()},
            "party": self.party,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def load(path: str) -> "Game":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        g = Game(name=data.get("name", "Campaign"))
        for cid, cd in data.get("characters", {}).items():
            g.characters[cid] = Character.from_dict(cd)
        for eid, ed in data.get("encounters", {}).items():
            g.encounters[eid] = CombatEncounter.from_dict(ed)
        g.party = data.get("party", [])
        return g

class DnDApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DnD Manager")
        try:
            self.attributes("-fullscreen", True)
            self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))
        except Exception:
            self.geometry("1000x650")
        self.minsize(900, 600)
        self.columnconfigure(0, weight=0)   # left
        self.columnconfigure(1, weight=1)   # middle (expand)
        self.columnconfigure(2, weight=0)   # right
        # main content will be on row=1; header is row=0
        self.rowconfigure(1, weight=1)

        style = ttk.Style(self)
        try:
            style.theme_use('clam')
        except Exception:
            pass
        bg = "#2b2b2b"
        midbg = "#333333"
        fg = "#e6e6e6"
        entry_bg = "#3a3a3a"
        style.configure('.', background=bg, foreground=fg, fieldbackground=entry_bg)
        style.configure('TLabel', background=bg, foreground=fg)
        style.configure('TButton', background=midbg, foreground=fg)
        style.map('TButton', background=[('active', '#444444')])
        self.configure(bg=bg)
        self._base_dir = os.path.dirname(__file__) if '__file__' in globals() else os.getcwd()
        self.game = Game()
        default_path = os.path.join(self._base_dir, "game_save.json")
        if os.path.exists(default_path):
            try:
                self.game = Game.load(default_path)
            except Exception:
                pass

        # mode state: "DM" or "Player"
        self.mode = "DM"

        self._style_colors = (bg, midbg, fg, entry_bg)

        # build UI (header + main panes). create_widgets will create header at row 0 and main panes at row 1
        self.create_widgets()

        # initial mode prompt
        self.prompt_initial_mode()

        # data initialization
        try:
            init_api_indexes()
        except Exception:
            pass
        try:
            load_spell_library(os.path.join(self._base_dir, "spells.json"))
        except Exception:
            pass

        self.refresh_char_list()
        self.refresh_enc_list()

    def prompt_initial_mode(self):
        try:
            resp = messagebox.askyesno("Mode", "Open in DM mode? (Yes = DM, No = Player)", parent=self)
            self.set_mode("DM" if resp else "Player")
        except Exception:
            self.set_mode("DM")

    def set_mode(self, mode: str):
        self.mode = "DM" if (mode and mode.upper().startswith("D")) else "Player"
        self.apply_mode()

    def toggle_mode(self):
        self.set_mode("Player" if self.mode == "DM" else "DM")

    def apply_mode(self):
        # update label & toggle button text
        self.mode_label.config(text=f"Mode: {self.mode}")
        self.mode_toggle_btn.config(text=f"Switch to {'Player' if self.mode == 'DM' else 'DM'}")

        if self.mode == "DM":
            # show right (encounters) and enable management buttons
            if not self.right.winfo_ismapped():
                self.right.grid(row=1, column=2, sticky="nse", padx=8, pady=8)
            self.new_char_btn.config(state="normal")
            self.edit_char_btn.config(state="normal")
            self.delete_char_btn.config(state="normal")
            self.save_btn.config(state="normal")
            self.load_btn.config(state="normal")
            self.add_enc_btn.config(state="normal")
            self.delete_enc_btn.config(state="normal")
            self.start_enc_btn.config(state="normal")
            self.open_enc_btn.config(state="normal")
        else:
            # Player mode: hide right frame, disable management actions
            if self.right.winfo_ismapped():
                self.right.grid_remove()
            self.new_char_btn.config(state="disabled")
            self.edit_char_btn.config(state="disabled")
            self.delete_char_btn.config(state="disabled")
            self.save_btn.config(state="disabled")
            self.load_btn.config(state="disabled")
            self.add_enc_btn.config(state="disabled")
            self.delete_enc_btn.config(state="disabled")
            self.start_enc_btn.config(state="disabled")
            self.open_enc_btn.config(state="disabled")

    def create_widgets(self):
        bg, midbg, fg, entry_bg = self._style_colors

        # header row (row=0)
        header = ttk.Frame(self)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", padx=4, pady=4)
        header.columnconfigure(0, weight=1)
        self.mode_label = ttk.Label(header, text="Mode: DM")
        self.mode_label.grid(row=0, column=0, sticky="w", padx=(6,0))
        self.mode_toggle_btn = ttk.Button(header, text="Switch to Player", width=14, command=self.toggle_mode)
        self.mode_toggle_btn.grid(row=0, column=1, sticky="e", padx=(0,6))

        # left pane (row=1)
        self.left = ttk.Frame(self)
        self.left.grid(row=1, column=0, sticky="nsw", padx=8, pady=8)
        self.left.columnconfigure(0, weight=1)
        ttk.Label(self.left, text="Characters").grid(row=0, column=0, sticky="w")
        self.char_list = tk.Listbox(self.left, width=30, height=25,
                                   bg=entry_bg, fg=fg, selectbackground="#5a5a5a",
                                   highlightbackground=bg, bd=0)
        self.char_list.grid(row=1, column=0, padx=4, pady=4, sticky="nsew")
        self.left.rowconfigure(1, weight=1)
        self.char_list.bind("<<ListboxSelect>>", self.on_char_select)

        # management buttons (store references for enable/disable)
        self.new_char_btn = ttk.Button(self.left, text="New Char", command=self.new_character)
        self.new_char_btn.grid(row=2, column=0, sticky="ew", pady=2)
        self.edit_char_btn = ttk.Button(self.left, text="Edit Char", command=self.edit_character)
        self.edit_char_btn.grid(row=3, column=0, sticky="ew", pady=2)
        self.delete_char_btn = ttk.Button(self.left, text="Delete Char", command=self.delete_character)
        self.delete_char_btn.grid(row=4, column=0, sticky="ew", pady=2)
        self.save_btn = ttk.Button(self.left, text="Save Game", command=self.save_game)
        self.save_btn.grid(row=5, column=0, sticky="ew", pady=2)
        self.load_btn = ttk.Button(self.left, text="Load Game", command=self.load_game)
        self.load_btn.grid(row=6, column=0, sticky="ew", pady=2)
        self.dice_btn = ttk.Button(self.left, text="Dice Roller", command=self.open_dice_roller)
        self.dice_btn.grid(row=7, column=0, sticky="ew", pady=(8,0))

        # middle pane (row=1)
        self.mid = ttk.Frame(self)
        self.mid.grid(row=1, column=1, sticky="nsew", padx=8, pady=8)
        self.mid.columnconfigure(0, weight=1)
        self.mid.rowconfigure(0, weight=1)
        self.detail_text = tk.Text(self.mid, width=60, height=20, bg="#1e1e1e", fg=fg, insertbackground=fg, bd=0)
        self.detail_text.grid(row=0, column=0, sticky="nsew")
        act_frame = ttk.Frame(self.mid)
        act_frame.grid(row=1, column=0, sticky="ew", pady=4)
        act_frame.columnconfigure((0,1,2,3), weight=1)
        ttk.Button(act_frame, text="Add to Encounter", command=self.add_to_encounter).grid(row=0, column=0, padx=4, sticky="ew")
        ttk.Button(act_frame, text="Heal/Damage", command=self.heal_damage).grid(row=0, column=1, padx=4, sticky="ew")
        ttk.Button(act_frame, text="Short Rest", command=self.short_rest_dialog).grid(row=0, column=2, padx=4, sticky="ew")
        ttk.Button(act_frame, text="Long Rest", command=self.long_rest).grid(row=0, column=3, padx=4, sticky="ew")

        # right pane (row=1) - encounters
        self.right = ttk.Frame(self)
        self.right.grid(row=1, column=2, sticky="nse", padx=8, pady=8)
        self.right.columnconfigure(0, weight=1)
        ttk.Label(self.right, text="Encounters").grid(row=0, column=0, sticky="w")
        self.enc_list = tk.Listbox(self.right, width=40, height=12,
                                   bg=entry_bg, fg=fg, selectbackground="#5a5a5a",
                                   highlightbackground=bg, bd=0)
        self.enc_list.grid(row=1, column=0, padx=4, pady=4, sticky="nsew")
        self.right.rowconfigure(1, weight=1)
        self.enc_list.bind("<<ListboxSelect>>", self.on_enc_select)
        btnf = ttk.Frame(self.right)
        btnf.grid(row=2, column=0, sticky="ew")
        btnf.columnconfigure((0,1,2,3), weight=1)
        self.add_enc_btn = ttk.Button(btnf, text="New Enc", command=self.new_encounter)
        self.add_enc_btn.grid(row=0, column=0, sticky="ew")
        self.delete_enc_btn = ttk.Button(btnf, text="Delete Enc", command=self.delete_encounter)
        self.delete_enc_btn.grid(row=0, column=1, sticky="ew")
        self.start_enc_btn = ttk.Button(btnf, text="Start Enc", command=self.start_encounter)
        self.start_enc_btn.grid(row=0, column=2, sticky="ew")
        self.open_enc_btn = ttk.Button(btnf, text="Open Enc", command=self.open_encounter_window)
        self.open_enc_btn.grid(row=0, column=3, sticky="ew")

    # --- rest of methods unchanged (they use self.char_list, self.left, etc.) ---
    def refresh_char_list(self):
        self.char_list.delete(0, tk.END)
        for cid, ch in self.game.characters.items():
            self.char_list.insert(tk.END, f"{ch.name} (HP {ch.current_hp}/{ch.max_hp}) [{cid}]")

    def on_char_select(self, _ev=None):
        sel = self.char_list.curselection()
        if not sel:
            return
        idx = sel[0]
        cid = list(self.game.characters.keys())[idx]
        self.selected_char_id = cid
        self.show_character(cid)

    def show_character(self, cid: str):
        ch = self.game.characters.get(cid)
        if not ch:
            return
        t = []
        t.append(f"Name: {ch.name}")
        t.append(f"Player: {ch.player_name}")
        t.append(f"Level: {ch.level}  XP: {ch.xp}  Prof bonus: {ch.prof_bonus()}")
        t.append(f"Class/Race: {ch.char_class} / {ch.race}")
        t.append(f"HP: {ch.current_hp}/{ch.max_hp}  Temp HP: {ch.temp_hp}")
        t.append(f"AC: {ch.armor_class}  Speed: {ch.speed}")
        t.append("Abilities:")
        for k, v in ch.abilities.items():
            t.append(f"  {k}: {v} (mod {ability_mod(v)})")
        t.append("Conditions:")
        for c, r in ch.conditions.items():
            t.append(f"  {c}: {r}")
        t.append("Inventory:")
        for it in ch.inventory:
            t.append(f"  {it.name} x{it.quantity} {'(consumable)' if it.consumable else ''}")
        t.append("Spells:")
        for sp in ch.spells:
            t.append(f"  {sp.name} (L{sp.level})")
        self.detail_text.delete(1.0, tk.END)
        self.detail_text.insert(tk.END, "\n".join(t))

    def new_character(self):
        dlg = CharacterDialog(self)
        ch = dlg.result
        if ch:
            self.game.add_character(ch)
            self.refresh_char_list()

    def edit_character(self):
        if not getattr(self, "selected_char_id", None):
            messagebox.showinfo("Info", "Select a character first")
            return
        ch = self.game.characters[self.selected_char_id] # type: ignore
        dlg = CharacterDialog(self, ch)
        updated = dlg.result
        if updated:
            self.game.characters[self.selected_char_id] = updated # type: ignore
            self.refresh_char_list()
            self.show_character(self.selected_char_id) # type: ignore

    def delete_character(self):
        if not getattr(self, "selected_char_id", None):
            messagebox.showinfo("Info", "Select a character first")
            return
        if messagebox.askyesno("Confirm", "Delete selected character?"):
            self.game.remove_character(self.selected_char_id) # type: ignore
            self.selected_char_id = None
            self.refresh_char_list()
            self.detail_text.delete(1.0, tk.END)

    def refresh_enc_list(self):
        self.enc_list.delete(0, tk.END)
        for eid, enc in self.game.encounters.items():
            self.enc_list.insert(tk.END, f"{enc.name} Round:{enc.round} Combatants:{len(enc.combatants)} [{eid}]")

    def on_enc_select(self, _ev=None):
        sel = self.enc_list.curselection()
        if not sel:
            return
        idx = sel[0]
        eid = list(self.game.encounters.keys())[idx]
        self.selected_enc_id = eid

    def new_encounter(self):
        name = simpledialog.askstring("Encounter", "Encounter name:", parent=self)
        if not name:
            return
        eid = self.game.create_encounter(name)
        self.refresh_enc_list()

    def delete_encounter(self):
        """Delete the currently selected encounter after confirmation."""
        if not getattr(self, "selected_enc_id", None):
            messagebox.showinfo("Info", "Select an encounter first", parent=self)
            return
        if self.selected_enc_id not in self.game.encounters:
            messagebox.showinfo("Info", "Selected encounter no longer exists", parent=self)
            self.selected_enc_id = None
            self.refresh_enc_list()
            return
        if messagebox.askyesno("Confirm", "Delete encounter?", parent=self):
            self.game.encounters.pop(self.selected_enc_id, None)
            self.selected_enc_id = None
            self.refresh_enc_list()

    def add_to_encounter(self):
        """Add the selected character to the selected encounter."""
        if not getattr(self, "selected_enc_id", None):
            messagebox.showinfo("Info", "Select an encounter first", parent=self)
            return
        if not getattr(self, "selected_char_id", None):
            messagebox.showinfo("Info", "Select a character first", parent=self)
            return
        enc = self.game.encounters.get(self.selected_enc_id) # type: ignore
        ch = self.game.characters.get(self.selected_char_id) # type: ignore
        if enc is None or ch is None:
            messagebox.showerror("Error", "Encounter or character not found", parent=self)
            return
        try:
            self.game.add_to_encounter(self.selected_enc_id, self.selected_char_id, is_npc=False) # type: ignore
            messagebox.showinfo("Added", f"Added {ch.name} to {enc.name}", parent=self) # type: ignore
            self.refresh_enc_list()
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self)

    def start_encounter(self):
        """Prompt for initiatives and start the selected encounter."""
        if not getattr(self, "selected_enc_id", None):
            messagebox.showinfo("Info", "Select an encounter first", parent=self)
            return
        enc = self.game.encounters.get(self.selected_enc_id) # type: ignore
        if enc is None:
            messagebox.showerror("Error", "Encounter not found", parent=self)
            self.selected_enc_id = None
            self.refresh_enc_list()
            return
        if not enc.combatants:
            messagebox.showinfo("Info", "Encounter has no combatants", parent=self)
            return

        initiatives: Dict[str, int] = {}
        for c in enc.combatants:
            default = c.character.ability_mod("DEX") + random.randint(1, 20)
            try:
                val = simpledialog.askinteger("Initiative",
                                              f"Initiative for {c.character.name} (default {default}):",
                                              parent=self,
                                              initialvalue=default)
            except Exception:
                val = default
            if val is None:
                val = default
            initiatives[c.character.char_id] = int(val)

        try:
            enc.start(initiatives)
            messagebox.showinfo("Started", f"Encounter '{enc.name}' started (Round {enc.round})", parent=self)
            self.refresh_enc_list()
            self.open_encounter_window()
        except Exception as e:
            messagebox.showerror("Error starting encounter", str(e), parent=self)

    def open_encounter_window(self):
        """Open the encounter window for the selected encounter."""
        if not getattr(self, "selected_enc_id", None):
            messagebox.showinfo("Info", "Select an encounter first", parent=self)
            return
        enc = self.game.encounters.get(self.selected_enc_id) # type: ignore
        if enc is None:
            messagebox.showerror("Error", "Encounter not found", parent=self)
            self.selected_enc_id = None
            self.refresh_enc_list()
            return
        try:
            EncWindow(self, enc)
            self.refresh_enc_list()
        except Exception as e:
            messagebox.showerror("Error opening encounter window", str(e), parent=self)

    def heal_damage(self):
        """Heal or damage the selected character. Positive heals, negative damages."""
        if not getattr(self, "selected_char_id", None):
            messagebox.showinfo("Info", "Select a character first", parent=self)
            return
        ch = self.game.characters.get(self.selected_char_id) # type: ignore
        if ch is None:
            messagebox.showerror("Error", "Character not found", parent=self)
            self.selected_char_id = None
            self.refresh_char_list()
            return
        amt = simpledialog.askinteger("Heal/Damage", "Positive to heal, negative to damage:", parent=self)
        if amt is None:
            return
        try:
            if amt >= 0:
                ch.heal(int(amt))
            else:
                ch.apply_damage(int(-amt))
            self.show_character(self.selected_char_id) # type: ignore
            self.refresh_char_list()
        except Exception as e:
            messagebox.showerror("Error applying heal/damage", str(e), parent=self)

    def short_rest_dialog(self):
        """Perform a short rest using comma-separated hit die rolls entered by the user."""
        if not getattr(self, "selected_char_id", None):
            messagebox.showinfo("Info", "Select a character first", parent=self)
            return
        ch = self.game.characters.get(self.selected_char_id) # type: ignore
        if ch is None:
            messagebox.showerror("Error", "Character not found", parent=self)
            self.selected_char_id = None
            self.refresh_char_list()
            return
        rolls = simpledialog.askstring("Short Rest", "Enter comma-separated hit die rolls (e.g. 4,5):", parent=self)
        if rolls is None or rolls.strip() == "":
            return
        try:
            rolls_list = [int(x.strip()) for x in rolls.split(",") if x.strip() != ""]
        except ValueError:
            messagebox.showerror("Error", "Invalid rolls - enter integers separated by commas", parent=self)
            return
        try:
            ch.short_rest(rolls_list)
            self.show_character(self.selected_char_id) # type: ignore
            self.refresh_char_list()
        except Exception as e:
            messagebox.showerror("Error applying short rest", str(e), parent=self)

    def long_rest(self):
        """Apply a long rest to the selected character (full heal and restore slots by default)."""
        if not getattr(self, "selected_char_id", None):
            messagebox.showinfo("Info", "Select a character first", parent=self)
            return
        ch = self.game.characters.get(self.selected_char_id) # type: ignore
        if ch is None:
            messagebox.showerror("Error", "Character not found", parent=self)
            self.selected_char_id = None
            self.refresh_char_list()
            return
        try:
            ch.long_rest()
            self.show_character(self.selected_char_id) # type: ignore
            self.refresh_char_list()
        except Exception as e:
            messagebox.showerror("Error applying long rest", str(e), parent=self)

    def save_game(self):
        fname = simpledialog.askstring("Save", "Save filename (or full path):", initialvalue="game_save.json", parent=self)
        if not fname:
            return
        path = fname
        if not os.path.isabs(path):
            path = os.path.join(self._base_dir, path)
        try:
            self.game.save(path)
            messagebox.showinfo("Saved", f"Saved to {path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def load_game(self):
        fname = simpledialog.askstring("Load", "Load filename (or full path):", initialvalue="game_save.json", parent=self)
        if not fname:
            return
        path = fname
        if not os.path.isabs(path):
            path = os.path.join(self._base_dir, path)
        if not os.path.exists(path):
            messagebox.showerror("Error", f"File not found: {path}")
            return
        try:
            g = Game.load(path)
            self.game = g
            self.selected_char_id = None
            self.selected_enc_id = None
            self.refresh_char_list()
            self.refresh_enc_list()
            messagebox.showinfo("Loaded", f"Loaded {path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def open_dice_roller(self):
        DiceRoller(self)


class CharacterDialog(simpledialog.Dialog):
    def __init__(self, parent, character: Optional[Character] = None):
        self.character = character
        self.result: Optional[Character] = None
        super().__init__(parent, title="Character" if character is None else f"Edit {character.name}")

    def body(self, master):
        tk.Label(master, text="Name:").grid(row=0, column=0)
        self.name_e = tk.Entry(master)
        self.name_e.grid(row=0, column=1)
        tk.Label(master, text="Player:").grid(row=1, column=0)
        self.player_e = tk.Entry(master)
        self.player_e.grid(row=1, column=1)
        tk.Label(master, text="Level:").grid(row=2, column=0)
        self.level_e = tk.Entry(master)
        self.level_e.grid(row=2, column=1)
        tk.Label(master, text="Max HP:").grid(row=3, column=0)
        self.maxhp_e = tk.Entry(master)
        self.maxhp_e.grid(row=3, column=1)

        tk.Label(master, text="Class:").grid(row=4, column=0)
        self.class_cb = ttk.Combobox(master, values=sorted([k.title() for k in CLASS_INDEX.keys()]) if CLASS_INDEX else [], state="readonly")
        self.class_cb.grid(row=4, column=1, sticky="ew")
        self.class_cb.bind("<<ComboboxSelected>>", self.on_class_selected)

        self.ability_vars = {}
        row = 5
        for ab in ["STR", "DEX", "CON", "INT", "WIS", "CHA"]:
            tk.Label(master, text=ab+":").grid(row=row, column=0)
            e = tk.Entry(master, width=6)
            e.grid(row=row, column=1, sticky="w")
            self.ability_vars[ab] = e
            row += 1

        if self.character:
            ch = self.character
            self.name_e.insert(0, ch.name)
            if ch.player_name:
                self.player_e.insert(0, ch.player_name)
            self.level_e.insert(0, str(ch.level))
            self.maxhp_e.insert(0, str(ch.max_hp))
            if ch.char_class:
                self.class_cb.set(ch.char_class)
            for k, ent in self.ability_vars.items():
                ent.insert(0, str(ch.abilities.get(k, 10)))
        else:
            self.level_e.insert(0, "1")
            self.maxhp_e.insert(0, "8")
            for k, ent in self.ability_vars.items():
                ent.insert(0, "10")
        return self.name_e

    def on_class_selected(self, _ev=None):
        sel = self.class_cb.get()
        if not sel:
            return
        idx = CLASS_INDEX.get(sel.lower()) or CLASS_INDEX.get(normalize_key(sel))
        if not idx:
            return
        try:
            r = api_get(f"{API_BASE}/api/classes/{idx}")
            r.raise_for_status()
            data = r.json()
            hit_die = data.get("hit_die")
            if hit_die:
                try:
                    level = int(self.level_e.get())
                except Exception:
                    level = 1
                if level == 1:
                    try:
                        con = int(self.ability_vars.get("CON").get()) # type: ignore
                    except Exception:
                        con = 10
                    con_mod = ability_mod(con)
                    suggested = hit_die + con_mod
                    self.maxhp_e.delete(0, tk.END)
                    self.maxhp_e.insert(0, str(max(1, suggested)))
        except Exception:
            pass

    def apply(self):
        name = self.name_e.get().strip() or "Unnamed"
        player = self.player_e.get().strip() or None
        try:
            level = int(self.level_e.get())
        except Exception:
            level = 1
        try:
            max_hp = int(self.maxhp_e.get())
        except Exception:
            max_hp = 8
        abilities = {}
        for k, ent in self.ability_vars.items():
            try:
                abilities[k] = int(ent.get())
            except Exception:
                abilities[k] = 10
        cls = self.class_cb.get().strip()
        ch = Character(name=name, player_name=player, level=level, max_hp=max_hp, current_hp=max_hp, abilities=abilities, char_class=cls)
        self.result = ch

class EncWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk, encounter: CombatEncounter):
        super().__init__(parent)
        self.title(f"Encounter: {encounter.name}")
        self.geometry("800x520")
        self.minsize(600, 380)
        self.enc = encounter
        self.create_widgets()
        self.refresh()

    def create_widgets(self):
        top = ttk.Frame(self)
        top.pack(fill="both", expand=True, padx=8, pady=8)
        self.lst = tk.Listbox(top)
        self.lst.pack(fill="both", expand=True, side="left")
        right = ttk.Frame(top)
        right.pack(side="right", fill="y")
        ttk.Button(right, text="Next Turn", command=self.next_turn).pack(fill="x", pady=2)
        ttk.Button(right, text="Prev Turn", command=self.prev_turn).pack(fill="x", pady=2)
        ttk.Button(right, text="Damage", command=self.apply_damage).pack(fill="x", pady=2)
        ttk.Button(right, text="Area Damage", command=self.area_damage).pack(fill="x", pady=2)
        ttk.Button(right, text="Roll Initiative", command=self.roll_initiative).pack(fill="x", pady=2)

    def refresh(self):
        self.lst.delete(0, tk.END)
        for i, c in enumerate(self.enc.combatants):
            cur = "(current)" if i == self.enc.turn_index else ""
            self.lst.insert(tk.END, f"{i+1}. {c.character.name} HP:{c.character.current_hp}/{c.character.max_hp} Init:{c.initiative} {cur}")

    def next_turn(self):
        try:
            cb = self.enc.next_turn()
            messagebox.showinfo("Turn", f"Now: {cb.character.name} (Round {self.enc.round})")
            self.refresh()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def prev_turn(self):
        try:
            cb = self.enc.previous_turn()
            messagebox.showinfo("Turn", f"Now: {cb.character.name}")
            self.refresh()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def apply_damage(self):
        sel = self.lst.curselection()
        if not sel:
            messagebox.showinfo("Info", "Select a target")
            return
        idx = sel[0]
        target = self.enc.combatants[idx]
        dmg = simpledialog.askinteger("Damage", f"Damage to {target.character.name}:", parent=self)
        if dmg is None:
            return
        res = target.character.apply_damage(dmg)
        if target.character.current_hp <= 0:
            target.alive = False
        messagebox.showinfo("Result", f"Applied {dmg}. HP now {target.character.current_hp}/{target.character.max_hp}")
        self.refresh()

    def area_damage(self):
        if not self.enc.combatants:
            return
        dmg_map = {}
        for c in self.enc.combatants:
            v = simpledialog.askstring("Area Damage", f"Damage to {c.character.name} (blank skip):", parent=self)
            if not v or v.strip() == "":
                continue
            try:
                dmg_map[c.character.char_id] = int(v.strip())
            except ValueError:
                continue
        if not dmg_map:
            return
        con_saves = {}
        if messagebox.askyesno("Con saves", "Provide CON saves for concentration checks?"):
            for c in self.enc.combatants:
                if c.character.char_id in dmg_map:
                    val = simpledialog.askinteger("Con save", f"CON save total for {c.character.name} (blank skip):", parent=self)
                    if val is not None:
                        con_saves[c.character.char_id] = val
        res = self.enc.apply_area_damage(dmg_map, con_saves if con_saves else None)
        messagebox.showinfo("Area Damage", f"Results: {res}")
        self.refresh()

    def roll_initiative(self):
        for c in self.enc.combatants:
            c.initiative = random.randint(1,20) + c.character.ability_mod("DEX")
        self.enc.combatants.sort(key=lambda c: (c.initiative if c.initiative is not None else 0, c.character.ability_mod("DEX")), reverse=True)
        messagebox.showinfo("Init", "Rolled initiative")
        self.refresh()

class DiceRoller(tk.Toplevel):
    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.title("Dice Roller")
        self.geometry("420x360")
        self.minsize(360, 300)
        self.create_widgets()

    def create_widgets(self):
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        for i in range(3):
            frame.columnconfigure(i, weight=1)

        buttons = [("d4", "1d4"), ("d6", "1d6"), ("d8", "1d8"),
                ("d10", "1d10"), ("d12", "1d12"), ("d20", "1d20"),
                ("d100", "1d100")]
        for idx, (lab, expr) in enumerate(buttons):
            r = idx // 3
            c = idx % 3
            b = ttk.Button(frame, text=lab, command=lambda e=expr: self.roll(e))
            b.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")
        label_row = (len(buttons) - 1) // 3 + 1
        ttk.Label(frame, text="Custom (NdM+K):").grid(row=label_row, column=0, columnspan=3, sticky="w", pady=(6,0))
        self.custom_e = tk.Entry(frame)
        self.custom_e.grid(row=label_row+1, column=0, columnspan=2, sticky="ew", padx=(0,4))
        ttk.Button(frame, text="Roll", command=self.roll_custom).grid(row=label_row+1, column=2, sticky="ew")
        self.out = tk.Text(frame, height=8)
        self.out.grid(row=label_row+2, column=0, columnspan=3, sticky="nsew", pady=8)
        frame.rowconfigure(label_row+2, weight=1)

    def roll(self, expr: str):
        total, detail = roll_expression(expr)
        self.out.insert(tk.END, f"{expr} -> {total} ({detail})\n")
        self.out.see(tk.END)

    def roll_custom(self):
        expr = self.custom_e.get().strip()
        if not expr:
            return
        try:
            total, detail = roll_expression(expr)
            self.out.insert(tk.END, f"{expr} -> {total} ({detail})\n")
            self.out.see(tk.END)
        except Exception as e:
            messagebox.showerror("Error", f"Invalid expression: {e}")

def roll_expression(expr: str) -> Tuple[int, str]:
    """
    Parse and roll expressions like:
      - "1d6", "2d8+3", "4", "d20", "3d6+2"
    Returns (total, detail).
    Raises ValueError on invalid syntax.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("Empty expression")

    e = expr.replace(" ", "").lower()
    plus = 0
    # support + and - modifiers (only a single trailing +/- number supported)
    # find last + or - that is not part of a dice count (e.g. 1d6-1)
    mod_idx = None
    for i in range(len(e)-1, -1, -1):
        if e[i] in "+-":
            # ensure it's not the 'd' or part of number like "-2" at start
            if i == 0:
                mod_idx = i
                break
            # if preceding char is digit or letter, accept as modifier separator
            if e[i-1].isdigit() or e[i-1] == 'd':
                mod_idx = i
                break
    if mod_idx is not None:
        try:
            mod_part = e[mod_idx:]
            e = e[:mod_idx]
            plus = int(mod_part)
        except Exception:
            raise ValueError("Invalid modifier in expression")

    if not e:
        # expression was just a modifier like "+3" or "-2"
        return plus, f"modifier only {plus:+d}"

    if 'd' in e:
        parts = e.split('d', 1)
        n_str, m_str = parts[0], parts[1]
        try:
            n = int(n_str) if n_str != "" else 1
            m = int(m_str)
        except Exception:
            raise ValueError("Invalid dice count/size")
        if n < 1 or m < 1 or n > 1000 or m > 10000:
            raise ValueError("Dice counts/sizes out of allowed range")
        rolls = [random.randint(1, m) for _ in range(n)]
        total = sum(rolls) + plus
        return total, f"rolls={rolls}{' ' + ('+' if plus>=0 else '') + str(plus) if plus!=0 else ''}"
    else:
        # constant integer
        try:
            total = int(e) + plus
            return total, f"const {int(e)} {'+' if plus>=0 else ''}{plus}" if plus != 0 else f"const {int(e)}"
        except Exception:
            raise ValueError("Invalid numeric expression")

# Initialize caches and local library once at module load; be robust about paths
try:
    init_api_indexes()
except Exception:
    # don't crash the program if API indexing fails
    pass

try:
    # try current working directory first, then script directory
    load_spell_library("spells.json")
except Exception:
    try:
        base = os.path.dirname(__file__) if '__file__' in globals() else os.getcwd()
        load_spell_library(os.path.join(base, "spells.json"))
    except Exception:
        # final fallback: empty library (already handled inside load_spell_library)
        pass

if __name__ == "__main__":
    # Ensure tkinter import name exists (some versions imported ttk/messagebox earlier)
    try:
        app = DnDApp()
        app.mainloop()
    except Exception as exc:
        # Minimal reporting to stderr; avoid crashing silently during development
        import traceback, sys
        traceback.print_exc(file=sys.stderr)
        raise