format_prompt = """
```
[
    {
        "action": "<action_name>",
        "units": [<unit_id>, <unit_id>, ...], # units you want to command
        "target_unit" (optional): <unit_id>, # some existing unit
        "target_position" (optional): [x, y]
    },
    // more actions ...
]
```

Example:
```
[
    {
        "action": "ATTACK_ATTACK",
        "units": [1, 2, 3],
        "target_unit": 9
    },
    {
        "action": "MOVE_MOVE",
        "units": [4, 5],
        "target_position": [50, 60]
    },
    {
        "action": "COMMANDCENTERTRAIN_SCV",
        "units": [6]
    }
]
```
""".strip()


def construct_text(info: dict):
    return "\n\n".join([f"###{key}###\n{value}" for key, value in info.items()])

TERRANN_TECH_TREE = f"""
Core & Economy
    Command Center: unlock SCV, Orbital Command, Planetary Fortress, and Engineering Bay
    Orbital Command: an upgrade to the Command Center; unlock MULEs for increased mining
    Planetary Fortress: a defensive upgrade for the Command Center with a powerful ground attack
    Supply Depot: increases supply cap and unlock the Barracks
    Refinery: allows SCVs to harvest Vespene Gas

Infantry & Defenses
    Barracks: produce infantry units (Marine, Reaper, Marauder, Ghost); unlock Bunker, Factory and Ghost Academy
    Engineering Bay: research infantry and building upgrades; unlock Missile Turret, Sensor Tower and Planetary Fortress
    Bunker: a defensive structure that infantry units can garrison inside for protection
    Missile Turret: attack air units
    Sensor Tower: detect invisible units on the minimap
    Ghost Academy: unlock the Ghost unit and researches its upgrades, including Personal Cloaking and the ability to arm nukes

Mechanical & Air
    Factory: produce mechanical ground units (Hellion, Widow Mine, Siege Tank, Hellbat, Thor) and unlock the Starport and Armory
    Armory: research weapon/armor upgrades; unlock the Hellbat, Thor and higher-level infantry upgrades
    Starport: produce air units (Viking, Medivac, Banshee, Raven, Battlecruiser)
    Fusion Core: unlock Battlecruiser

Add-ons
    Tech Lab of Barracks: unlock Marauder and Ghost
    Tech Lab of Factory: unlock Siege Tank, Hellbat and Thor
    Tech Lab of Starport: unlock Banshee, Raven and Battlecruiser
    Reactor: allow structures to produce two units simultaneously
""".strip()

PROTOSS_TECH_TREE = f"""
Core & Economy
    Nexus: produce Probes and the Mothership Core; unlock the Gateway and Forge
    Pylon: increases supply cap and provides a power field that is required to power nearby structures
    Assimilator: allows Probes to harvest Vespene Gas
Gateway & Ground Forces
    Gateway: produce ground units (Zealot, Sentry, Stalker, High Templar, Dark Templar) and unlock the Cybernetics Core
    Cybernetics Core: researches Warp Gate technology and air weapon/armor upgrades; unlock the Stalker, Sentry, Stargate, Mothership Core, Twilight Council, and Robotics Facility
    Forge: researches ground weapon, ground armor, and shield upgrades; unlock the Photon Cannon
    Photon Cannon: a static defense structure that can attack both ground and air units
    Twilight Council: unlock advanced unit abilities like Blink and Charge, the Templar Archives, the Dark Shrine, and higher-level ground upgrades
    Templar Archives: unlock the High Templar unit and researches its Psionic Storm ability. High Templar can merge to form an Archon
    Dark Shrine: unlock the permanently cloaked Dark Templar unit
Robotics & Air Units
    Stargate: produce air units (Phoenix, Oracle, Void Ray, Tempest, Carrier) and unlock the Fleet Beacon
    Robotics Facility: produce mechanical ground units (Observer, Warp Prism, Immortal, Colossus) and unlock the Robotics Bay
    Robotics Bay: unlock the Colossus unit and researches upgrades for robotic units
    Fleet Beacon: unlock the Carrier and Tempest units, researches their unique upgrades, and enables higher-level air upgrades
Key Technology
    Warp Gate: an upgrade to the Gateway that allows units to be warped-in instantly to any location within a Pylon's power field
""".strip()

ZERG_TECH_TREE = f"""
Core & Evolution
    Hatchery: The core Zerg structure. produce Drones for economy and Overlords for supply. unlock the Spawning Pool, Evolution Chamber, and Spore Crawler. Can be upgraded to a Lair.
    Lair: An upgrade to the Hatchery that unlock tier-two units and structures. produce Overseers (by morphing an Overlord) and unlock the Hydralisk Den, Spire, Infestation Pit, and Nydus Network. Can be upgraded to a Hive.
    Hive: The final upgrade for the Hatchery/Lair, unlocking the highest tier of Zerg technology. unlock the Viper, Ultralisk Cavern, and Greater Spire.
    Extractor: Allows Drones to harvest Vespene Gas.
Ground Units & Defenses
    Spawning Pool: The first military structure. produce Zerglings and unlock the Spine Crawler, Roach Warren, and Baneling Nest. Researches Zergling upgrades like Metabolic Boost.
    Spine Crawler: A static defensive structure that attacks ground units.
    Spore Crawler: A static defensive structure that attacks air units and can detect cloaked units.
    Roach Warren: unlock the Roach unit and researches its upgrades, including Glial Reconstitution and Tunneling Claws.
    Baneling Nest: unlock the ability to morph Zerglings into Banelings and researches the Centrifugal Hooks upgrade.
    Hydralisk Den: unlock the Hydralisk unit and researches its upgrades, Grooved Spines and Muscular Augments.
    Ultralisk Cavern: unlock the powerful Ultralisk unit and researches its Chitinous Plating upgrade.
Advanced & Air Units
    Spire: produce air units (Mutalisk, Corruptor) and researches their attack and armor upgrades. Can be upgraded to a Greater Spire.
    Greater Spire: An upgrade to the Spire that unlock the ability to morph Corruptors into Brood Lords.
    Infestation Pit: unlock caster units (Swarm Host, Infestor) and is a prerequisite for upgrading a Lair to a Hive. Researches upgrades like Neural Parasite.
    Nydus Network: A utility structure that allows you to create Nydus Worms, enabling rapid transport of your army across the map.
Upgrades & Evolution
    Evolution Chamber: Researches upgrades for all ground units, including Melee Attacks, Missile Attacks, and Ground Carapace.
""".strip()

TechTree = {
    "Terran": TERRANN_TECH_TREE,
    "Protoss": PROTOSS_TECH_TREE,
    "Zerg": ZERG_TECH_TREE,
}