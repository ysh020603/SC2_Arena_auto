from agents.base_agent import BaseAgent
from tools.format import extract_code, json_to_markdown, construct_ordered_list
import json

strategy_prompt = """
Our final aim: destroy all enemies as soon as possible.
Our strategy:
- Resource collection: produce workers and gather minerals and gas
- Development: build attacking units and structures
- Attacking: concentrate forces to search and destroy enemies proactively
""".strip()

def construct_plan_example(race: str):
    if race == "Terran":
        return """
Following are some examples:
- Do nothing and just wait;
- Train 1/2/3/... SCV/Marine/Viking/...
- Build a supply depot;
- Upgrade to Orbital Command;
- Attack visible enemies;
- ...
""".strip()
    elif race == "Protoss":
        return """
Following are some examples:
- Do nothing and just wait;
- Train 1/2/3/... Probe/Stalker/Zealot/...
- Build a Pylon;
- Upgrade to Warp Gate;
- Attack visible enemies;
- ...
""".strip()
    elif race == "Zerg":
        return """
Following are some examples:
- Do nothing and just wait;
- Train 1/2/3/... Drone/Zergling/Hydralisk/...
- Build a Hatchery;
- Upgrade to Lair;
- Attack visible enemies;
- ...
""".strip()
    else:
        raise ValueError(f"Unknown race: {race}")

def construct_rules(race: str):
    rules = [
        "Commands should be natural language, instead of code.",
        "Produce as many units with the strongest attack power as possible.",
        "The total cost of all commands should not exceed the current resources (minerals and gas).",
        # "Commands should not build redundant structures while the existing ones are idle.",
        "Commands should not build redundant structures(e.g. 2 Refinery while one is not fully utilized).",
        "Commands should not use abilities that are not supported currently.",
        "Commands should not build a structure that is not needed now (e.g. build a Missile Turret but there is no enemy air unit).",
        "The unit production list capacity of structures is 5. If the list is full, do not add more units to it.",
    ]
    if race == "Terran":
        rules += [
            "Commands should not send SCV or MULE to gather resources because the system will do it automatically.",
            "Commands should not train too many SCVs or MULEs, whose number should not exceed the capacity of CommandCenter and Refinery.",
            "Commands can construct a new one Supply Depot only when the remaining unused supply is less than 7.",
        ]
    elif race == "Protoss":
        rules += [
            "Commands should not send Probe to gather resources because the system will do it automatically.",
            "Commands should not train too many Probes, whose number should not exceed the capacity of Nexus and Assimilator.",
            "Commands can construct a new one Pylon only when the remaining unused supply is less than 7.",
        ]
    elif race == "Zerg":
        rules += [
            "Commands should not send Drone to gather resources because the system will do it automatically.",
            "Commands should not train too many Drones, whose number should not exceed the capacity of Hatchery and Extractor.",
            "Commands can construct a new one Overlord only when the remaining unused supply is less than 7.",
            "Commands should not train another Overlord if any [Egg] unit in 'Own units' has 'Production list: Overlord'.",
        ]
    else:
        raise ValueError(f"Unknown race: {race}")
    return rules

############## Plan Role Prompt ###############
def create_plan_prompt(race: str, rules: list[str], obs_text: str):
    plan_example_prompt = construct_plan_example(race)
    rules_prompt = "Rule checklist:\n" + construct_ordered_list(rules)
    return f"""
As a top-tier StarCraft II strategist, your task is to give one or more commands based on the current game state. Only give commands which can be executed immediately, instead of waiting for certain events.

### Aim
{strategy_prompt}

### Current Game State
{obs_text}

### Rules
{rules_prompt}

### Examples
{plan_example_prompt}

Think step by step, and then give commands as a list JSON in the following format wrapped with triple backticks:
```
[
    "<command_1>",
    "<command_2>",
    ...
]
```
    """.strip()


############## Plan Critic Role Prompt ###############
def create_plan_critic_prompt(rules: list[str], obs_text: str, plans: list[str]):
    rules_text = construct_ordered_list(rules)
    plans_text = construct_ordered_list(plans)
    return """
As a top-tier StarCraft II player, your task is to check if the given commands for current game state violate any rules.

### Current Game State
%s

### Given Commands
%s

### Rules Checklist
%s

Analyze the given rules one by one, and then provide a summary for errors at the end as follows, wrapped with triple backticks::
```
{
    "errors": [
        "Error 1: ...",
        "Error 2: ...",
        ...
    ],
    "error_number": 0/1/2/...
}
```
    """.strip() % (obs_text, plans_text, rules_text)


class PlanAgent(BaseAgent):
    def __init__(self, race, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.race = race
        self.rules = construct_rules(race)
        self.plan_example = construct_plan_example(race)
        
        self.max_refine_times = 3
        self.think = []
        self.chat_history = []

    def gene_new_plan(self, obs_text: str, rules: list[str]):
        prompt = create_plan_prompt(self.race, rules, obs_text)
        response, messages = self.llm_client.call(**self.generation_config, prompt=prompt, need_json=True)
        self.think.append([response])
        self.chat_history.append(messages)
        return json.loads(extract_code(response))

    def critic_plan(self, plan: list[str], obs_text: str, rules: list[str]):
        prompt = create_plan_critic_prompt(rules, obs_text, plan)
        response, messages = self.llm_client.call(**self.generation_config, prompt=prompt, need_json=True)
        self.think[-1].append(response)
        self.chat_history.append(messages)
        return response

    def refine_plan(self, obs_text: str, plan: list[str], critic: str, rules: list[str]):
        gene_prompt = create_plan_prompt(self.race, rules, obs_text)
        history = [
            {"role": "user", "content": gene_prompt},
            {"role": "assistant", "content": json_to_markdown(plan)},
        ]
        prompt = (
            "Errors:\n"
            + critic
            + "\nRethink with the given rules and errors step by step, and then give a refined plan based on the current game state."
        )
        response, messages = self.llm_client.call(**self.generation_config, prompt=prompt, history=history, need_json=True)
        self.think.append([response])
        self.chat_history.append(messages)
        return json.loads(extract_code(response))

    def refine_plan_until_ready(self, obs_text: str, plan: list[str], rules: list[str]):
        for _ in range(self.max_refine_times):
            critic = self.critic_plan(plan, obs_text, rules)
            critic = json.loads(extract_code(critic))
            if isinstance(critic, list):
                critic = {"error_number": len(critic), "errors": critic}
            if critic.get("error_number", 0) == 0:
                return plan
            critic = construct_ordered_list(critic.get("errors", []))
            plan = self.refine_plan(obs_text, plan, critic, rules)
        return plan

    def run(self, obs_text: str, verifier=None, suggestions: list[str] = []):
        self.think = []
        self.chat_history = []
        rules = self.rules + suggestions
        plan = self.gene_new_plan(obs_text, rules)
        if verifier == "llm":
            plan = self.refine_plan_until_ready(obs_text, plan, rules)
        return plan, self.think, self.chat_history
