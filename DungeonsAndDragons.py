import json
import math
import uuid
import random
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.stacklayout import StackLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.checkbox import CheckBox
from kivy.uix.spinner import Spinner
from kivy.uix.popup import Popup
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.modalview import ModalView
from kivy.uix.carousel import Carousel
from kivy.core.window import Window
from kivy.properties import StringProperty, NumericProperty, BooleanProperty, ListProperty, DictProperty, ObjectProperty
from kivy.clock import Clock
from kivy.config import Config

# API and core functionality (unchanged from original)
API_BASE = "https://www.dnd5eapi.co"
API_TIMEOUT = 5  # seconds

HAVE_REQUESTS = False
try:
    import requests
    from requests.exceptions import RequestException
    HAVE_REQUESTS = True
except Exception:
    requests = None
    RequestException = Exception

import urllib.request
import urllib.error

SESSION = requests.Session() if HAVE_REQUESTS else None

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

SPELL_INDEX: Dict[str, str] = {}
CLASS_INDEX: Dict[str, str] = {}
SPELL_LIBRARY: Dict[str, "Spell"] = {}

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
    damage_expr: Optional[str] = None
    damage_type: Optional[str] = None
    save: Optional[str] = None
    save_half: bool = False

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
        damage_expr = None
        damage_type = None
        Expandedg = data.get("damage")
        if isinstance(Expandedg, dict):
            dt = Expandedg.get("damage_type")
            if isinstance(dt, dict):
                damage_type = dt.get("name")
            dal = Expandedg.get("damage_at_slot_level") or Expandedg.get("damage_at_character_level")
            if isinstance(dal, dict):
                key = str(level) if str(level) in dal else next(iter(dal.keys()), None)
                if key:
                    damage_expr = dal.get(key)
        save = None
        dc = data.get("dc")
        if isinstance(dc, dict):
            dc_type = dc.get("dc_type")
            if isinstance(dc_type, dict):
                name = dc_type.get("name", "")
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

    def apply_damage(self, amount: int, damage_round: Optional[int] = None, 
                     con_save_roll: Optional[int] = None) -> Dict[str, Any]:
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
        return {"current_hp": self.current_hp, "temp_hp": self.temp_hp, 
                "concentration_broken": concentration_broken}

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

    def cast_spell(self, spell_name: str, encounter: "CombatEncounter", targets: List[str], 
                   slot_level: Optional[int] = None, caster_ability: str = "INT") -> Dict[str, Any]:
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
        return {"character": self.character.to_dict(), "initiative": self.initiative, 
                "alive": self.alive, "is_npc": self.is_npc, "token_id": self.token_id}

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Combatant":
        return Combatant(character=Character.from_dict(data["character"]), 
                         initiative=data.get("initiative"), alive=data.get("alive", True), 
                         is_npc=data.get("is_npc", False), token_id=data.get("token_id"))

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
        self.combatants.sort(key=lambda c: (c.initiative if c.initiative is not None else -999, 
                                            c.character.ability_mod("DEX")), reverse=True)
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

    def perform_attack(self, attacker_id: str, defender_id: str, 
                       attack_roll_total: int, damage: int, crit: bool = False) -> Dict[str, Any]:
        attacker = next((c for c in self.combatants if c.character.char_id == attacker_id), None)
        defender = next((c for c in self.combatants if c.character.char_id == defender_id), None)
        if not attacker or not defender:
            raise ValueError("Attacker or defender not in encounter")
        hit = attack_roll_total >= defender.character.armor_class
        applied_damage = damage * (2 if crit else 1) if hit else 0
        res = {"hit": hit, "applied_damage": applied_damage, "defender_before_hp": defender.character.current_hp}
        if hit:
            Expandedg_res = defender.character.apply_damage(applied_damage)
            res.update(Expandedg_res)
            if defender.character.current_hp <= 0:
                defender.alive = False
                res["defender_alive"] = False
            else:
                res["defender_alive"] = True
        return res

    def cast_spell(self, caster_id: str, spell_name: str, slot_level: Optional[int], 
                   target_ids: List[str], caster_ability: str = "INT") -> Dict[str, Any]:
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
            Expandedg_total, Expandedg_detail = _roll_damage_expr(sp.damage_expr)
            if sp.save and sp.save_half and save_succeeded:
                damage_done = max(0, Expandedg_total // 2)
            else:
                damage_done = Expandedg_total if (sp.damage_expr and (not sp.save or not save_succeeded or not sp.save_half)) else 0
            applied = tgt.character.apply_damage(damage_done, con_save_roll=save_roll_val)
            if tgt.character.current_hp <= 0:
                tgt.alive = False
            results["targets"][tid] = {
                "target_name": tgt.character.name,
                "save_required": bool(sp.save),
                "save_roll_total": save_roll_val,
                "save_succeeded": save_succeeded,
                "damage_roll": Expandedg_detail,
                "damage_applied": damage_done,
                "after": applied
            }
        return results

    def apply_area_damage(self, damage_map: Dict[str, int], 
                          con_saves: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        results = {}
        for tid, Expandedg in damage_map.items():
            target = next((c for c in self.combatants if c.character.char_id == tid), None)
            if not target:
                continue
            con_roll = con_saves.get(tid) if con_saves else None
            res = target.character.apply_damage(Expandedg, con_save_roll=con_roll)
            if target.character.current_hp <= 0:
                target.alive = False
            results[tid] = res
        return results

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "round": self.round, "turn_index": self.turn_index, 
                "combatants": [c.to_dict() for c in self.combatants]}

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

def roll_expression(expr: str) -> Tuple[int, str]:
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("Empty expression")

    e = expr.replace(" ", "").lower()
    plus = 0
    mod_idx = None
    for i in range(len(e)-1, -1, -1):
        if e[i] in "+-":
            if i == 0:
                mod_idx = i
                break
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
        try:
            total = int(e) + plus
            return total, f"const {int(e)} {'+' if plus>=0 else ''}{plus}" if plus != 0 else f"const {int(e)}"
        except Exception:
            raise ValueError("Invalid numeric expression")

# Kivy UI Components
class MessagePopup(Popup):
    def __init__(self, title, message, **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self.size_hint = (0.8, 0.4)
        layout = BoxLayout(orientation='vertical')
        layout.add_widget(Label(text=message))
        btn = Button(text='OK', size_hint_y=0.3)
        btn.bind(on_press=self.dismiss)
        layout.add_widget(btn)
        self.add_widget(layout)

class InputPopup(Popup):
    def __init__(self, title, prompt, callback, **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self.size_hint = (0.8, 0.4)
        self.callback = callback
        layout = BoxLayout(orientation='vertical')
        layout.add_widget(Label(text=prompt))
        self.input = TextInput(multiline=False)
        layout.add_widget(self.input)
        btn_layout = BoxLayout(size_hint_y=0.3)
        ok_btn = Button(text='OK')
        ok_btn.bind(on_press=self.on_ok)
        cancel_btn = Button(text='Cancel')
        cancel_btn.bind(on_press=self.dismiss)
        btn_layout.add_widget(ok_btn)
        btn_layout.add_widget(cancel_btn)
        layout.add_widget(btn_layout)
        self.add_widget(layout)

    def on_ok(self, instance):
        self.callback(self.input.text)
        self.dismiss()

class NumericInputPopup(Popup):
    def __init__(self, title, prompt, callback, **kwargs):
        super().__init__(**kwargs)
        self.title = title
        self.size_hint = (0.8, 0.4)
        self.callback = callback
        layout = BoxLayout(orientation='vertical')
        layout.add_widget(Label(text=prompt))
        self.input = TextInput(multiline=False, input_filter='int')
        layout.add_widget(self.input)
        btn_layout = BoxLayout(size_hint_y=0.3)
        ok_btn = Button(text='OK')
        ok_btn.bind(on_press=self.on_ok)
        cancel_btn = Button(text='Cancel')
        cancel_btn.bind(on_press=self.dismiss)
        btn_layout.add_widget(ok_btn)
        btn_layout.add_widget(cancel_btn)
        layout.add_widget(btn_layout)
        self.add_widget(layout)

    def on_ok(self, instance):
        try:
            value = int(self.input.text)
            self.callback(value)
            self.dismiss()
        except ValueError:
            MessagePopup("Error", "Please enter a valid number").open()

class CharacterDialog(ModalView):
    def __init__(self, app, character=None, **kwargs):
        super().__init__(**kwargs)
        self.app = app
        self.character = character
        self.size_hint = (0.9, 0.9)
        self.title = "Edit Character" if character else "New Character"
        
        layout = BoxLayout(orientation='vertical')
        
        # Name and basic info
        form_layout = GridLayout(cols=2, size_hint_y=None, height=200)
        form_layout.add_widget(Label(text="Name:"))
        self.name_input = TextInput(text=character.name if character else "", multiline=False)
        form_layout.add_widget(self.name_input)
        
        form_layout.add_widget(Label(text="Player:"))
        self.player_input = TextInput(text=character.player_name if character else "", multiline=False)
        form_layout.add_widget(self.player_input)
        
        form_layout.add_widget(Label(text="Level:"))
        self.level_input = TextInput(text=str(character.level) if character else "1", multiline=False, input_filter='int')
        form_layout.add_widget(self.level_input)
        
        form_layout.add_widget(Label(text="Max HP:"))
        self.hp_input = TextInput(text=str(character.max_hp) if character else "8", multiline=False, input_filter='int')
        form_layout.add_widget(self.hp_input)
        
        form_layout.add_widget(Label(text="Class:"))
        class_values = sorted([k.title() for k in CLASS_INDEX.keys()]) if CLASS_INDEX else []
        self.class_spinner = Spinner(text=character.char_class if character else "", values=class_values)
        form_layout.add_widget(self.class_spinner)
        
        layout.add_widget(form_layout)
        
        # Abilities
        abilities_layout = GridLayout(cols=7, size_hint_y=None, height=100)
        self.ability_inputs = {}
        abilities = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
        for ab in abilities:
            abilities_layout.add_widget(Label(text=ab))
            input_field = TextInput(
                text=str(character.abilities.get(ab, 10)) if character else "10", 
                multiline=False, 
                size_hint_x=None,
                width=60,
                input_filter='int'
            )
            self.ability_inputs[ab] = input_field
            abilities_layout.add_widget(input_field)
        layout.add_widget(abilities_layout)
        
        # Buttons
        btn_layout = BoxLayout(size_hint_y=0.1)
        save_btn = Button(text="Save")
        save_btn.bind(on_press=self.save_character)
        cancel_btn = Button(text="Cancel")
        cancel_btn.bind(on_press=self.dismiss)
        btn_layout.add_widget(save_btn)
        btn_layout.add_widget(cancel_btn)
        layout.add_widget(btn_layout)
        
        self.add_widget(layout)

    def save_character(self, instance):
        try:
            name = self.name_input.text.strip() or "Unnamed"
            player = self.player_input.text.strip() or None
            level = int(self.level_input.text) if self.level_input.text else 1
            max_hp = int(self.hp_input.text) if self.hp_input.text else 8
            char_class = self.class_spinner.text
            
            abilities = {}
            for ab, widget in self.ability_inputs.items():
                abilities[ab] = int(widget.text) if widget.text else 10
            
            if self.character:
                # Update existing character
                self.character.name = name
                self.character.player_name = player
                self.character.level = level
                self.character.max_hp = max_hp
                self.character.current_hp = max_hp
                self.character.char_class = char_class
                self.character.abilities = abilities
                result = self.character
            else:
                # Create new character
                result = Character(
                    name=name,
                    player_name=player,
                    level=level,
                    max_hp=max_hp,
                    current_hp=max_hp,
                    char_class=char_class,
                    abilities=abilities
                )
            
            self.app.on_character_dialog_result(result)
            self.dismiss()
        except Exception as e:
            MessagePopup("Error", f"Error saving character: {str(e)}").open()

class CharacterTab(TabbedPanelItem):
    def __init__(self, character, app, **kwargs):
        super().__init__(**kwargs)
        self.character = character
        self.app = app
        self.text = character.name
        
        layout = ScrollView()
        content = BoxLayout(orientation='vertical', size_hint_y=None)
        content.bind(minimum_height=content.setter('height'))
        
        # Header
        header = BoxLayout(size_hint_y=None, height=80)
        header.add_widget(Label(text=f"{character.name}\nPlayer: {character.player_name or 'N/A'}"))
        content.add_widget(header)
        
        # Conditions
        conditions_label = Label(text="Conditions:", size_hint_y=None, height=30)
        content.add_widget(conditions_label)
        
        conditions_layout = GridLayout(cols=3, size_hint_y=None)
        conditions_layout.bind(minimum_height=conditions_layout.setter('height'))
        
        for condition in sorted(CONDITIONS):
            condition_box = BoxLayout(orientation='horizontal', size_hint_y=None, height=40)
            checkbox = CheckBox(active=condition in character.conditions)
            checkbox.bind(active=lambda instance, value, cond=condition: self.toggle_condition(cond, value))
            condition_box.add_widget(checkbox)
            condition_box.add_widget(Label(text=condition.title()))
            conditions_layout.add_widget(condition_box)
        
        content.add_widget(conditions_layout)
        
        # Spell Slots
        if character.spell_slots:
            slots_label = Label(text="Spell Slots:", size_hint_y=None, height=30)
            content.add_widget(slots_label)
            
            for level in sorted(character.spell_slots.keys()):
                slot = character.spell_slots[level]
                slot_layout = BoxLayout(size_hint_y=None, height=50)
                slot_layout.add_widget(Label(text=f"Level {level}: {slot.get('current', 0)}/{slot.get('max', 0)}"))
                
                btn_layout = BoxLayout(size_hint_x=0.4)
                dec_btn = Button(text="-")
                dec_btn.bind(on_press=lambda x, lvl=level: self.change_slot(lvl, -1))
                inc_btn = Button(text="+")
                inc_btn.bind(on_press=lambda x, lvl=level: self.change_slot(lvl, 1))
                btn_layout.add_widget(dec_btn)
                btn_layout.add_widget(inc_btn)
                slot_layout.add_widget(btn_layout)
                
                content.add_widget(slot_layout)
        
        layout.add_widget(content)
        self.add_widget(layout)
    
    def toggle_condition(self, condition, active):
        if active:
            self.character.conditions[condition] = -1
        else:
            self.character.conditions.pop(condition, None)
        self.app.refresh_character_tabs()
    
    def change_slot(self, level, delta):
        slot = self.character.spell_slots.get(level)
        if slot:
            current = slot.get("current", 0)
            max_slots = slot.get("max", 0)
            new_current = max(0, min(max_slots, current + delta))
            slot["current"] = new_current
            self.app.refresh_character_tabs()

class DiceRollerPopup(Popup):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title = "Dice Roller"
        self.size_hint = (0.8, 0.8)
        
        layout = BoxLayout(orientation='vertical')
        
        # Standard dice buttons
        dice_layout = GridLayout(cols=3, size_hint_y=0.6)
        dice_buttons = [
            ("d4", "1d4"), ("d6", "1d6"), ("d8", "1d8"),
            ("d10", "1d10"), ("d12", "1d12"), ("d20", "1d20"),
            ("d100", "1d100")
        ]
        
        for label, expr in dice_buttons:
            btn = Button(text=label)
            btn.bind(on_press=lambda instance, e=expr: self.roll_dice(e))
            dice_layout.add_widget(btn)
        
        layout.add_widget(dice_layout)
        
        # Custom roll
        custom_layout = BoxLayout(size_hint_y=0.2)
        self.custom_input = TextInput(text="", hint_text="e.g., 2d6+3", multiline=False)
        custom_layout.add_widget(self.custom_input)
        custom_btn = Button(text="Roll", size_hint_x=0.3)
        custom_btn.bind(on_press=self.roll_custom)
        custom_layout.add_widget(custom_btn)
        layout.add_widget(custom_layout)
        
        # Results
        self.results_output = TextInput(text="", readonly=True, size_hint_y=0.2)
        layout.add_widget(self.results_output)
        
        self.add_widget(layout)
    
    def roll_dice(self, expression):
        try:
            total, detail = roll_expression(expression)
            self.results_output.text += f"{expression}: {total} ({detail})\n"
        except Exception as e:
            self.results_output.text += f"Error: {str(e)}\n"
    
    def roll_custom(self, instance):
        expression = self.custom_input.text.strip()
        if expression:
            self.roll_dice(expression)

class EncounterWindow(Popup):
    def __init__(self, encounter, app, **kwargs):
        super().__init__(**kwargs)
        self.encounter = encounter
        self.app = app
        self.title = f"Encounter: {encounter.name}"
        self.size_hint = (0.9, 0.9)
        
        layout = BoxLayout(orientation='vertical')
        
        # Combatant list
        self.combatant_list = ScrollView()
        self.list_layout = BoxLayout(orientation='vertical', size_hint_y=None)
        self.list_layout.bind(minimum_height=self.list_layout.setter('height'))
        self.combatant_list.add_widget(self.list_layout)
        layout.add_widget(self.combatant_list)
        
        # Controls
        controls = BoxLayout(size_hint_y=0.1)
        controls.add_widget(Button(text="Next Turn", on_press=self.next_turn))
        controls.add_widget(Button(text="Prev Turn", on_press=self.prev_turn))
        controls.add_widget(Button(text="Damage", on_press=self.apply_damage))
        controls.add_widget(Button(text="Area Damage", on_press=self.area_damage))
        controls.add_widget(Button(text="Roll Initiative", on_press=self.roll_initiative))
        layout.add_widget(controls)
        
        self.refresh_list()
        self.add_widget(layout)
    
    def refresh_list(self):
        self.list_layout.clear_widgets()
        for i, combatant in enumerate(self.encounter.combatants):
            current_indicator = " (CURRENT)" if i == self.encounter.turn_index else ""
            btn = Button(
                text=f"{i+1}. {combatant.character.name} HP: {combatant.character.current_hp}/{combatant.character.max_hp} Init: {combatant.initiative}{current_indicator}",
                size_hint_y=None,
                height=60
            )
            btn.combatant_index = i
            btn.bind(on_press=self.on_combatant_select)
            self.list_layout.add_widget(btn)
    
    def on_combatant_select(self, instance):
        self.selected_combatant_index = instance.combatant_index
    
    def next_turn(self, instance):
        try:
            combatant = self.encounter.next_turn()
            MessagePopup("Turn", f"Now: {combatant.character.name} (Round {self.encounter.round})").open()
            self.refresh_list()
        except Exception as e:
            MessagePopup("Error", str(e)).open()
    
    def prev_turn(self, instance):
        try:
            combatant = self.encounter.previous_turn()
            MessagePopup("Turn", f"Now: {combatant.character.name}").open()
            self.refresh_list()
        except Exception as e:
            MessagePopup("Error", str(e)).open()
    
    def apply_damage(self, instance):
        if not hasattr(self, 'selected_combatant_index'):
            MessagePopup("Info", "Select a combatant first").open()
            return
        
        def apply_damage_callback(amount):
            try:
                amount = int(amount)
                combatant = self.encounter.combatants[self.selected_combatant_index]
                result = combatant.character.apply_damage(amount)
                if combatant.character.current_hp <= 0:
                    combatant.alive = False
                MessagePopup("Result", f"Applied {amount} damage. HP now: {combatant.character.current_hp}/{combatant.character.max_hp}").open()
                self.refresh_list()
            except ValueError:
                MessagePopup("Error", "Invalid damage amount").open()
        
        NumericInputPopup("Damage", "Enter damage amount:", apply_damage_callback).open()
    
    def area_damage(self, instance):
        # Simplified area damage - apply same damage to all
        def apply_area_damage_callback(amount):
            try:
                amount = int(amount)
                damage_map = {}
                for combatant in self.encounter.combatants:
                    damage_map[combatant.character.char_id] = amount
                
                result = self.encounter.apply_area_damage(damage_map)
                MessagePopup("Area Damage", f"Applied {amount} damage to all combatants").open()
                self.refresh_list()
            except ValueError:
                MessagePopup("Error", "Invalid damage amount").open()
        
        NumericInputPopup("Area Damage", "Enter damage amount for all:", apply_area_damage_callback).open()
    
    def roll_initiative(self, instance):
        for combatant in self.encounter.combatants:
            combatant.initiative = random.randint(1, 20) + combatant.character.ability_mod("DEX")
        self.encounter.combatants.sort(
            key=lambda c: (c.initiative if c.initiative is not None else 0, c.character.ability_mod("DEX")), 
            reverse=True
        )
        MessagePopup("Initiative", "Rolled initiative for all combatants").open()
        self.refresh_list()

class DnDManagerApp(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.game = Game()
        self.selected_char_id = None
        self.selected_enc_id = None
        self.mode = "Expanded"  # or "Player"
        
        # Initialize data
        self._base_dir = os.getcwd()
        default_path = os.path.join(self._base_dir, "game_save.json")
        if os.path.exists(default_path):
            try:
                self.game = Game.load(default_path)
            except Exception:
                pass
        
        try:
            init_api_indexes()
        except Exception:
            pass
        
        try:
            load_spell_library(os.path.join(self._base_dir, "spells.json"))
        except Exception:
            pass

    def build(self):
        Window.clearcolor = (0.1, 0.1, 0.1, 1)
        return self.create_main_layout()

    def create_main_layout(self):
        # Main layout
        main_layout = BoxLayout(orientation='vertical')
        
        # Header
        header = BoxLayout(size_hint_y=0.1)
        self.mode_label = Label(text=f"Mode: {self.mode}")
        mode_btn = Button(text="Switch Mode", on_press=self.toggle_mode)
        exit_btn = Button(text="Exit", on_press=self.exit_app, size_hint_x=0.2)
        header.add_widget(self.mode_label)
        header.add_widget(mode_btn)
        header.add_widget(exit_btn)
        main_layout.add_widget(header)
        
        # Content area
        content = BoxLayout()
        
        # Left panel - Characters
        left_panel = BoxLayout(orientation='vertical', size_hint_x=0.3)
        left_panel.add_widget(Label(text="Characters", size_hint_y=0.05))
        
        # Character list
        char_scroll = ScrollView()
        self.char_list_layout = BoxLayout(orientation='vertical', size_hint_y=None)
        self.char_list_layout.bind(minimum_height=self.char_list_layout.setter('height'))
        char_scroll.add_widget(self.char_list_layout)
        left_panel.add_widget(char_scroll)
        
        # Character buttons
        char_buttons = BoxLayout(size_hint_y=0.2)
        self.new_char_btn = Button(text="New Char", on_press=self.new_character)
        self.edit_char_btn = Button(text="Edit Char", on_press=self.edit_character)
        self.delete_char_btn = Button(text="Delete Char", on_press=self.delete_character)
        char_buttons.add_widget(self.new_char_btn)
        char_buttons.add_widget(self.edit_char_btn)
        char_buttons.add_widget(self.delete_char_btn)
        left_panel.add_widget(char_buttons)
        
        # Character tabs
        self.char_tabs = TabbedPanel(size_hint_y=0.5)
        left_panel.add_widget(self.char_tabs)
        
        # Game management buttons
        game_buttons = BoxLayout(size_hint_y=0.1)
        game_buttons.add_widget(Button(text="Save Game", on_press=self.save_game))
        game_buttons.add_widget(Button(text="Load Game", on_press=self.load_game))
        game_buttons.add_widget(Button(text="Dice Roller", on_press=self.open_dice_roller))
        left_panel.add_widget(game_buttons)
        
        content.add_widget(left_panel)
        
        # Middle panel - Character details
        middle_panel = BoxLayout(orientation='vertical', size_hint_x=0.4)
        middle_panel.add_widget(Label(text="Character Details", size_hint_y=0.05))
        self.detail_text = TextInput(text="", readonly=True)
        middle_panel.add_widget(self.detail_text)
        
        # Action buttons
        action_buttons = BoxLayout(size_hint_y=0.15)
        action_buttons.add_widget(Button(text="Add to Encounter", on_press=self.add_to_encounter))
        action_buttons.add_widget(Button(text="Heal/Damage", on_press=self.heal_damage))
        action_buttons.add_widget(Button(text="Short Rest", on_press=self.short_rest_dialog))
        action_buttons.add_widget(Button(text="Long Rest", on_press=self.long_rest))
        middle_panel.add_widget(action_buttons)
        
        content.add_widget(middle_panel)
        
        # Right panel - Encounters (only in Expanded mode)
        self.right_panel = BoxLayout(orientation='vertical', size_hint_x=0.3)
        self.right_panel.add_widget(Label(text="Encounters", size_hint_y=0.05))
        
        # Encounter list
        enc_scroll = ScrollView()
        self.enc_list_layout = BoxLayout(orientation='vertical', size_hint_y=None)
        self.enc_list_layout.bind(minimum_height=self.enc_list_layout.setter('height'))
        enc_scroll.add_widget(self.enc_list_layout)
        self.right_panel.add_widget(enc_scroll)
        
        # Encounter buttons
        enc_buttons = BoxLayout(size_hint_y=0.15)
        self.new_enc_btn = Button(text="New Enc", on_press=self.new_encounter)
        self.delete_enc_btn = Button(text="Delete Enc", on_press=self.delete_encounter)
        self.start_enc_btn = Button(text="Start Enc", on_press=self.start_encounter)
        self.open_enc_btn = Button(text="Open Enc", on_press=self.open_encounter_window)
        enc_buttons.add_widget(self.new_enc_btn)
        enc_buttons.add_widget(self.delete_enc_btn)
        enc_buttons.add_widget(self.start_enc_btn)
        enc_buttons.add_widget(self.open_enc_btn)
        self.right_panel.add_widget(enc_buttons)
        
        content.add_widget(self.right_panel)
        main_layout.add_widget(content)
        
        self.refresh_ui()
        return main_layout

    def refresh_ui(self):
        self.refresh_char_list()
        self.refresh_enc_list()
        self.refresh_character_tabs()
        self.apply_mode()

    def refresh_char_list(self):
        self.char_list_layout.clear_widgets()
        for char_id, character in self.game.characters.items():
            btn = Button(
                text=f"{character.name} (HP {character.current_hp}/{character.max_hp})",
                size_hint_y=None,
                height=60
            )
            btn.char_id = char_id
            btn.bind(on_press=self.on_char_select)
            self.char_list_layout.add_widget(btn)

    def refresh_enc_list(self):
        self.enc_list_layout.clear_widgets()
        for enc_id, encounter in self.game.encounters.items():
            btn = Button(
                text=f"{encounter.name} Round:{encounter.round} Combatants:{len(encounter.combatants)}",
                size_hint_y=None,
                height=60
            )
            btn.enc_id = enc_id
            btn.bind(on_press=self.on_enc_select)
            self.enc_list_layout.add_widget(btn)

    def refresh_character_tabs(self):
        self.char_tabs.clear_tabs()
        for char_id, character in self.game.characters.items():
            tab = CharacterTab(character, self)
            self.char_tabs.add_widget(tab)

    def on_char_select(self, instance):
        self.selected_char_id = instance.char_id
        self.show_character(self.selected_char_id)

    def on_enc_select(self, instance):
        self.selected_enc_id = instance.enc_id

    def show_character(self, char_id):
        character = self.game.characters.get(char_id)
        if not character:
            return
        
        details = []
        details.append(f"Name: {character.name}")
        details.append(f"Player: {character.player_name or 'N/A'}")
        details.append(f"Level: {character.level}  XP: {character.xp}  Prof bonus: {character.prof_bonus()}")
        details.append(f"Class/Race: {character.char_class} / {character.race}")
        details.append(f"HP: {character.current_hp}/{character.max_hp}  Temp HP: {character.temp_hp}")
        details.append(f"AC: {character.armor_class}  Speed: {character.speed}")
        details.append("Abilities:")
        for ability, score in character.abilities.items():
            details.append(f"  {ability}: {score} (mod {ability_mod(score)})")
        details.append("Conditions:")
        if character.conditions:
            for condition, rounds in character.conditions.items():
                details.append(f"  {condition}: {rounds}")
        else:
            details.append("  None")
        details.append("Inventory:")
        if character.inventory:
            for item in character.inventory:
                details.append(f"  {item.name} x{item.quantity} {'(consumable)' if item.consumable else ''}")
        else:
            details.append("  None")
        details.append("Spells:")
        if character.spells:
            for spell in character.spells:
                details.append(f"  {spell.name} (L{spell.level})")
        else:
            details.append("  None")
        details.append("Spell Slots:")
        if character.spell_slots:
            for level, slot in sorted(character.spell_slots.items()):
                details.append(f"  Level {level}: {slot.get('current', 0)}/{slot.get('max', 0)}")
        else:
            details.append("  None")
        
        self.detail_text.text = "\n".join(details)

    def toggle_mode(self, instance):
        self.mode = "Player" if self.mode == "Expanded" else "Expanded"
        self.apply_mode()

    def apply_mode(self):
        self.mode_label.text = f"Mode: {self.mode}"
        
        if self.mode == "Expanded":
            self.right_panel.opacity = 1
            self.right_panel.disabled = False
            # Enable management buttons
            for btn in [self.new_char_btn, self.edit_char_btn, self.delete_char_btn, 
                       self.new_enc_btn, self.delete_enc_btn, self.start_enc_btn, self.open_enc_btn]:
                btn.disabled = False
        else:
            self.right_panel.opacity = 0.3
            self.right_panel.disabled = True
            # Disable management buttons
            for btn in [self.new_char_btn, self.edit_char_btn, self.delete_char_btn,
                       self.new_enc_btn, self.delete_enc_btn, self.start_enc_btn, self.open_enc_btn]:
                btn.disabled = True

    def new_character(self, instance):
        CharacterDialog(self).open()

    def edit_character(self, instance):
        if not self.selected_char_id:
            MessagePopup("Info", "Select a character first").open()
            return
        character = self.game.characters[self.selected_char_id]
        CharacterDialog(self, character).open()

    def on_character_dialog_result(self, character):
        if character.char_id in self.game.characters:
            # Update existing
            self.game.characters[character.char_id] = character
        else:
            # Add new
            self.game.add_character(character)
        self.refresh_ui()

    def delete_character(self, instance):
        if not self.selected_char_id:
            MessagePopup("Info", "Select a character first").open()
            return
        
        def confirm_delete(instance):
            self.game.remove_character(self.selected_char_id)
            self.selected_char_id = None
            self.detail_text.text = ""
            self.refresh_ui()
        
        popup = Popup(title="Confirm Delete", size_hint=(0.6, 0.3))
        content = BoxLayout(orientation='vertical')
        content.add_widget(Label(text="Delete this character?"))
        btn_layout = BoxLayout(size_hint_y=0.4)
        yes_btn = Button(text="Yes")
        yes_btn.bind(on_press=confirm_delete)
        no_btn = Button(text="No")
        no_btn.bind(on_press=popup.dismiss)
        btn_layout.add_widget(yes_btn)
        btn_layout.add_widget(no_btn)
        content.add_widget(btn_layout)
        popup.add_widget(content)
        popup.open()

    def new_encounter(self, instance):
        def create_encounter(name):
            if name:
                self.game.create_encounter(name)
                self.refresh_enc_list()
        
        InputPopup("New Encounter", "Enter encounter name:", create_encounter).open()

    def delete_encounter(self, instance):
        if not self.selected_enc_id:
            MessagePopup("Info", "Select an encounter first").open()
            return
        
        def confirm_delete(instance):
            self.game.encounters.pop(self.selected_enc_id, None)
            self.selected_enc_id = None
            self.refresh_enc_list()
        
        popup = Popup(title="Confirm Delete", size_hint=(0.6, 0.3))
        content = BoxLayout(orientation='vertical')
        content.add_widget(Label(text="Delete this encounter?"))
        btn_layout = BoxLayout(size_hint_y=0.4)
        yes_btn = Button(text="Yes")
        yes_btn.bind(on_press=confirm_delete)
        no_btn = Button(text="No")
        no_btn.bind(on_press=popup.dismiss)
        btn_layout.add_widget(yes_btn)
        btn_layout.add_widget(no_btn)
        content.add_widget(btn_layout)
        popup.add_widget(content)
        popup.open()

    def add_to_encounter(self, instance):
        if not self.selected_enc_id:
            MessagePopup("Info", "Select an encounter first").open()
            return
        if not self.selected_char_id:
            MessagePopup("Info", "Select a character first").open()
            return
        
        try:
            self.game.add_to_encounter(self.selected_enc_id, self.selected_char_id)
            MessagePopup("Success", "Character added to encounter").open()
            self.refresh_enc_list()
        except Exception as e:
            MessagePopup("Error", str(e)).open()

    def start_encounter(self, instance):
        if not self.selected_enc_id:
            MessagePopup("Info", "Select an encounter first").open()
            return
        
        encounter = self.game.encounters.get(self.selected_enc_id)
        if not encounter:
            MessagePopup("Error", "Encounter not found").open()
            return
        
        if not encounter.combatants:
            MessagePopup("Info", "No combatants in encounter").open()
            return
        
        # Roll initiatives
        initiatives = {}
        for combatant in encounter.combatants:
            default = combatant.character.ability_mod("DEX") + random.randint(1, 20)
            initiatives[combatant.character.char_id] = default
        
        encounter.start(initiatives)
        MessagePopup("Started", f"Encounter '{encounter.name}' started (Round {encounter.round})").open()
        self.refresh_enc_list()
        self.open_encounter_window(None)

    def open_encounter_window(self, instance):
        if not self.selected_enc_id:
            MessagePopup("Info", "Select an encounter first").open()
            return
        
        encounter = self.game.encounters.get(self.selected_enc_id)
        if not encounter:
            MessagePopup("Error", "Encounter not found").open()
            return
        
        EncounterWindow(encounter, self).open()

    def heal_damage(self, instance):
        if not self.selected_char_id:
            MessagePopup("Info", "Select a character first").open()
            return
        
        character = self.game.characters.get(self.selected_char_id)
        if not character:
            MessagePopup("Error", "Character not found").open()
            return
        
        def apply_heal_damage(amount):
            try:
                amount = int(amount)
                if amount >= 0:
                    character.heal(amount)
                else:
                    character.apply_damage(-amount)
                self.show_character(self.selected_char_id)
                self.refresh_char_list()
                self.refresh_character_tabs()
            except ValueError:
                MessagePopup("Error", "Invalid amount").open()
        
        NumericInputPopup("Heal/Damage", "Positive to heal, negative to damage:", apply_heal_damage).open()

    def short_rest_dialog(self, instance):
        if not self.selected_char_id:
            MessagePopup("Info", "Select a character first").open()
            return
        
        character = self.game.characters.get(self.selected_char_id)
        if not character:
            MessagePopup("Error", "Character not found").open()
            return
        
        def apply_short_rest(rolls_text):
            try:
                rolls = [int(x.strip()) for x in rolls_text.split(",") if x.strip()]
                character.short_rest(rolls)
                self.show_character(self.selected_char_id)
                self.refresh_char_list()
                self.refresh_character_tabs()
            except ValueError:
                MessagePopup("Error", "Invalid rolls format").open()
        
        InputPopup("Short Rest", "Enter comma-separated hit die rolls (e.g., 4,5):", apply_short_rest).open()

    def long_rest(self, instance):
        if not self.selected_char_id:
            MessagePopup("Info", "Select a character first").open()
            return
        
        character = self.game.characters.get(self.selected_char_id)
        if not character:
            MessagePopup("Error", "Character not found").open()
            return
        
        character.long_rest()
        self.show_character(self.selected_char_id)
        self.refresh_char_list()
        self.refresh_character_tabs()
        MessagePopup("Long Rest", "Character has completed a long rest").open()

    def save_game(self, instance):
        def save_callback(filename):
            if filename:
                path = filename if os.path.isabs(filename) else os.path.join(self._base_dir, filename)
                try:
                    self.game.save(path)
                    MessagePopup("Saved", f"Game saved to {path}").open()
                except Exception as e:
                    MessagePopup("Error", f"Save failed: {str(e)}").open()
        
        InputPopup("Save Game", "Enter filename:", save_callback, text="game_save.json").open()

    def load_game(self, instance):
        def load_callback(filename):
            if filename:
                path = filename if os.path.isabs(filename) else os.path.join(self._base_dir, filename)
                if not os.path.exists(path):
                    MessagePopup("Error", f"File not found: {path}").open()
                    return
                
                try:
                    self.game = Game.load(path)
                    self.selected_char_id = None
                    self.selected_enc_id = None
                    self.detail_text.text = ""
                    self.refresh_ui()
                    MessagePopup("Loaded", f"Game loaded from {path}").open()
                except Exception as e:
                    MessagePopup("Error", f"Load failed: {str(e)}").open()
        
        InputPopup("Load Game", "Enter filename:", load_callback, text="game_save.json").open()

    def open_dice_roller(self, instance):
        DiceRollerPopup().open()

    def exit_app(self, instance):
        self.stop()

if __name__ == "__main__":
    DnDManagerApp().run()