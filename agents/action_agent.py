from agents.base_agent import BaseAgent
from agents.common import construct_text, format_prompt
import json
from tools.format import extract_code, constrcut_openai_qa, construct_ordered_list


# Define reusable rules
rules = [
    "Do not give any action that is irrelevant to the task.",
    "Each of units can only be used in the whole response once at most.",
    "If a unit is already performing an action as given task, you should ignore it, instead of giving a repeated action for it.",
    "If one task cannot be finished, just ignore it.",
    "If resource is not enough, just complete the most important part of the task.",
]
rules_prompt = "Rule checklist:\n" + "\n".join([f"{i+1}. {rule}" for i, rule in enumerate(rules)])


# Define the prompt template
def create_action_prompt(obs_text: str, plan: list[str]):
    plan_text = construct_ordered_list(plan)
    return f"""
As a top-tier StarCraft II executor, your task is to give some actions to finish the given task as possible as you can.

### Current Game State
{obs_text}

### Given Tasks
{plan_text}

### Rules
{rules_prompt}

Give an action JSON in the following format wrapped with triple backticks:
{format_prompt}
    """.strip()


class ActionAgent(BaseAgent):
    def __init__(self, race: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_retry_attempts = 3
        self.think = []
        self.chat_history = []

    def run(self, obs_text: str, plan: list[str], verifier=None):
        self.think = []
        self.chat_history = []
        prompt = create_action_prompt(obs_text, plan)
        response, messages = self.llm_client.call(prompt=prompt, **self.generation_config, need_json=True)
        self.think.append([response])
        self.chat_history.append(messages)
        
        if verifier:
            history = constrcut_openai_qa(prompt, response)
            for try_time in range(self.max_retry_attempts):
                ok, verification_message = verifier(response)
                if not ok:
                    self.think[-1].append(verification_message)
                    
                    verification_message + "\nAnalyze step by step and then give a refined action JSON to finish the task as possible as you can."
                    response, messages = self.llm_client.call(prompt=verification_message, history=history, **self.generation_config, need_json=True)
                    self.think.append([response])
                    self.chat_history.append(messages)
                    
                    history.extend(constrcut_openai_qa(verification_message, response))
                else:
                    break
            ok, verification_message = verifier(response)
            self.think[-1].append(verification_message)

        try:
            actions = extract_code(response)
            actions = json.loads(actions)
            assert isinstance(actions, list)
            return actions, self.think, self.chat_history
        except Exception as e:
            return [], self.think, self.chat_history
