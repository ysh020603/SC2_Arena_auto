import json
import os
import re
from agents.base_agent import BaseAgent
from tools.format import extract_code
from tools.logger import setup_logger

# 获取 logger 实例
logger = setup_logger("AdjestAgent", log_dir="./logs")

# --- 1. 第一阶段: 基础分类 (Plan -> Task Type) ---

def build_plan_prompt(plan: str) -> str:
    """
    构建用于 LLM 分类的英文提示（针对单个 plan，要求 JSON 列表输出）。
    (此函数基于您的需求描述)
    """
    
    # 英文提示，要求 LLM 严格返回 *包含单个元素* 的 JSON 列表
    prompt = f"""
Here is an instruction from StarCraft:
"{plan}"

Please classify this instruction into one of the following categories: ['Attack Task', 'Empty Task', 'Other Task'].

- 'Attack Task': Assigning our units to attack an enemy unit or location.
- 'Empty Task': No action is required; do nothing (e.g., "Do nothing and just wait").
- 'Other Task': Any task that is not an 'Attack Task' or 'Empty Task' (e.g., training units, building, researching, using non-attack abilities).

Please output *only* a valid JSON list containing the *single* corresponding task category.

Example Input 1:
"Train one SCV from the Command Center (4657)."

Example Output 1:
```json
[
    "Other Task"
]
```

Example Input 2:
"Use the Ghost at unit ID 4945 to snipe the enemy Ghost at unit ID 4961."

Example Output 2:
```json
[
    "Attack Task"
]
```

Example Input 3:
"Do nothing and just wait"

Example Output 3:
```json
[
    "Empty Task"
]
```
"""
    return prompt

def extract_json_list(text: str) -> list | None:
    """
    从 LLM 的原始文本输出中提取 JSON 列表。
    优先使用 extract_code (来自 tools.format)，然后回退到 regex。
    """
    try:
        code_block = extract_code(text)
        if code_block and code_block.strip().startswith('['):
            parsed_json = json.loads(code_block)
        else:
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if not match:
                logger.warning(f"No JSON list (e.g., [ ... ]) found in LLM output: '{text}'")
                return None
            json_string = match.group(0)
            parsed_json = json.loads(json_string)
        
        if isinstance(parsed_json, list):
            return parsed_json
        else:
            logger.warning(f"Parsed JSON is not a list. Type: {type(parsed_json)}")
            return None
            
    except json.JSONDecodeError as e:
        logger.error(f"Could not decode extracted JSON list: {e}. Text: '{text}'")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during JSON list extraction: {e}. Text: '{text}'")
        return None

# --- 2. 第二阶段: 攻击分类 (Attack Plan -> Standard/Special) ---

def build_attack_prompt(command: str) -> str:
    """
    构建用于第二阶段攻击分类的 Prompt。
    (此函数基于您的需求描述)
    """
    return f"""Here are the definitions:
1.  **Standard Attack Command**: A basic instruction for a unit to attack a target using its default weapon. This command does NOT involve activating any special named abilities (like 'Stimpack') or changing its mode (like 'Siege Mode').
2.  **Special Ability/Mode Switch Command**: Any other command that involves activating a specific, named ability or changing the unit's operational mode.

Your task is to analyze the command provided below.

-   If the command is a 'Special Ability/Mode Switch Command', you MUST respond with the exact JSON string: `[]`
-   If the command is a 'Standard Attack Command', you MUST respond with a JSON object with the following English keys and specific format:
    -   `"unit_count"`: (Type: `int`) The number of units being commanded. For singular nouns like 'the Marine' or 'the Cyclone', this is always `1`.
    -   `"target_position"`: (Type: `list`) A list of two integers `[x, y]` representing the **target's** coordinates.
    -   `"target_unit"`: (Type: `str`) A string containing **only the name** of the target unit (e.g., 'Barracks', 'Oracle'). Do not include descriptors like 'the enemy' or 'under construction'.

Here are some examples:

**Example 1 (Special Ability):**
Command: "Use Stimpack on Marine unit 4833 to increase damage and movement speed."
Your Output:
[]

**Example 2 (Standard Attack):**
Command: "Launch OFFENSE with 3 Marine targeting Enemy Unit at (117, 43) and Enemy Unit at (35, 33)"
Your Output:
{{"unit_count": 3, "target_position": [[117, 43],[35,33]], "target_unit": ""}}

**Example 3 (Standard Attack):**
Command: "Launch OFFENSE with 3 Marauder, 5 Marine, 1 SCV targeting SupplyDepotLowered"
Your Output:
{{"unit_count": 9, "target_position": [], "target_unit": "SupplyDepotLowered"}}

---

Now, analyze this command:

Command: "{command}"
Your Output:
"""

def extract_attack_json_obj(text: str) -> dict | list | None:
    """
    从 LLM 的原始文本输出中提取 JSON 对象或列表。
    这是为第二阶段（攻击分类）定制的。
    """
    try:
        code_block = extract_code(text)
        if not code_block:
             # 如果没有 `...` 块，LLM 可能直接输出了 [] 或 {...}
             text = text.strip()
             if text.startswith('[') and text.endswith(']'):
                 code_block = text
             elif text.startswith('{') and text.endswith('}'):
                 code_block = text
             else:
                 logger.warning(f"No valid JSON object or list found in output: '{text}'")
                 return None
        
        parsed_json = json.loads(code_block)
        
        if isinstance(parsed_json, (dict, list)):
            return parsed_json
        else:
            logger.warning(f"Parsed JSON is not a dict or list. Type: {type(parsed_json)}")
            return None

    except json.JSONDecodeError as e:
        logger.error(f"Could not decode extracted JSON object/list: {e}. Text: '{text}'")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during JSON object/list extraction: {e}. Text: '{text}'")
        return None

# --- 3. JSON 日志保存 ---

def save_json(data: any, file_path: str):
    """
    将数据以 JSON 格式保存到文件，并确保目录存在。
    """
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving to {file_path}: {e}")

# --- 4. AdjestAgent 类 ---

class AdjestAgent(BaseAgent):
    def __init__(self, log_dir: str = "./logs/classification_logs", *args, **kwargs):
        """
        初始化 AdjestAgent。
        """
        super().__init__(*args, **kwargs)
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        
        # [修改] 实例属性，用于累积所有分类
        self.total_attack_tasks_raw = []
        self.total_standard_attack_commands = []
        self.total_empty_tasks = []
        self.total_other_tasks = []
        
        logger.info(f"AdjestAgent initialized. Classification logs will be saved to {self.log_dir}")

    def save_json_logs(self):
        """
        [修改] 将 [所有累积] 的分类结果保存到 JSON 文件中。
        (不再需要参数)
        """
        try:
            # 保存第一阶段识别出的所有 Attack Task 字符串
            save_json(self.total_attack_tasks_raw, os.path.join(self.log_dir, "attack_tasks_raw.json"))
            # 保存第二阶段解析出的 Standard Attack (JSON 对象)
            save_json(self.total_standard_attack_commands, os.path.join(self.log_dir, "standard_attack_commands.json"))
            # 保存 Empty Task
            save_json(self.total_empty_tasks, os.path.join(self.log_dir, "empty_tasks.json"))
            # 保存 Other Task (现在也包括了特殊技能)
            save_json(self.total_other_tasks, os.path.join(self.log_dir, "other_tasks.json"))
            
            logger.info(f"Cumulative classification logs saved to {self.log_dir}")
        except Exception as e:
            logger.error(f"Failed to save cumulative classification logs: {e}")

    def classify_plan(self, plan: str):
        """
        [第一阶段] 调用 LLM 对单个 plan 进行基础分类。
        """
        prompt = build_plan_prompt(plan)
        
        try:
            response, messages = self.llm_client.call(
                **self.generation_config,
                prompt=prompt,
                need_json=True 
            )
            
            categories_list = extract_json_list(response)
            
            category = None
            
            if categories_list and isinstance(categories_list, list) and len(categories_list) == 1:
                category = str(categories_list[0]).strip()
            else:
                logger.error(f"Expected JSON list with 1 element, but got: {categories_list}. Raw: '{response}'")

            if category == "Attack Task":
                return "Attack Task"
            elif category == "Empty Task":
                return "Empty Task"
            elif category == "Other Task":
                return "Other Task"
            else:
                if category is not None:
                    logger.warning(f"Unknown category '{category}' for plan: '{plan}'. Defaulting to 'Other Task'.")
                else:
                    logger.warning(f"Parse failed for plan: '{plan}'. Defaulting to 'Other Task'.")
                return "Other Task"

        except Exception as e:
            logger.error(f"Error during LLM call or classification for plan '{plan}': {e}")
            return "Other Task"

    def classify_attack_detail(self, plan: str):
        """
        [第二阶段] 调用 LLM 对 'Attack Task' plan 进行详细分类。
        返回解析后的 JSON 对象 (dict) 或列表 (list)。
        """
        prompt = build_attack_prompt(plan)
        
        try:
            response, messages = self.llm_client.call(
                **self.generation_config,
                prompt=prompt,
                need_json=True
            )
            
            # 使用为第二阶段定制的解析器
            parsed_output = extract_attack_json_obj(response)
            
            return parsed_output # 可能是 dict, list, 或 None

        except Exception as e:
            logger.error(f"Error during LLM call for attack detail classification '{plan}': {e}")
            return None # 失败时返回 None

    def run(self, plans: list[str]):
        """
        [修改] 执行两阶段分类任务。
        
        Args:
            plans (list[str]): PlanAgent 生成的自然语言规划列表。
            
        Returns:
            dict: [本轮] 分类后规划的字典。
                  e.g., {"standard_attack_commands": [{}], "empty_tasks": [], "other_tasks": []}
        """
        logger.info(f"Starting 2-stage classification for {len(plans)} plans...")
        
        # 1. 为 [本轮] 初始化临时列表
        # 阶段1：基础分类
        current_attack_tasks_raw = []
        current_empty_tasks = []
        current_other_tasks = []
        
        # 阶段2：详细分类
        current_standard_attack_commands = [] # (新变量，存储 JSON 对象)

        for plan in plans:
            if not isinstance(plan, str) or not plan.strip():
                logger.warning(f"Skipping empty or invalid plan: {plan}")
                continue
            
            # --- 阶段 1 ---
            category = self.classify_plan(plan)
            
            # 2. 将结果附加到 [本轮] 列表
            if category == "Empty Task":
                current_empty_tasks.append(plan)
            elif category == "Other Task":
                current_other_tasks.append(plan)
            elif category == "Attack Task":
                # 标记为攻击任务，准备进入阶段 2
                current_attack_tasks_raw.append(plan)
                
                # --- 阶段 2 ---
                attack_detail = self.classify_attack_detail(plan)
                
                # 检查返回的是否是包含数据的 dict
                if isinstance(attack_detail, dict) and attack_detail:
                    # 这是 'Standard Attack Command'
                    current_standard_attack_commands.append(attack_detail)
                else:
                    # 这是 'Special Ability' (返回 '[]') 或解析失败 (返回 None)
                    # 按要求归类到 'Other Task'
                    current_other_tasks.append(plan)

        # 3. 将 [本轮] 结果累积到 [总] 实例属性中
        self.total_attack_tasks_raw.extend(current_attack_tasks_raw)
        self.total_standard_attack_commands.extend(current_standard_attack_commands)
        self.total_empty_tasks.extend(current_empty_tasks)
        self.total_other_tasks.extend(current_other_tasks)

        # 4. 保存 [总] 日志
        self.save_json_logs() # (不需要参数)

        # 5. 返回 [本轮] 结果
        result = {
            "standard_attack_commands": current_standard_attack_commands,
            "empty_tasks": current_empty_tasks,
            "other_tasks": current_other_tasks
        }
        
        logger.info(f"Classification complete [Current Run]. Standard Attacks: {len(current_standard_attack_commands)}, Empty: {len(current_empty_tasks)}, Other/Special: {len(current_other_tasks)}.")
        logger.info(f"Classification complete [Total]. Standard Attacks: {len(self.total_standard_attack_commands)}, Empty: {len(self.total_empty_tasks)}, Other/Special: {len(self.total_other_tasks)}.")
        
        return result