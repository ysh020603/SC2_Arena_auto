from sc2 import maps
from sc2.player import Bot, Computer
from sc2.main import run_game
from sc2.data import Race, Difficulty, AIBuild
from dotenv import load_dotenv
from argparse import ArgumentParser
import json
import os

from players import LLMPlayer
from tools import constants
from tools.llm import LLMClient

load_dotenv()

os.environ['SC2PATH'] = '/data2/SC2/StarCraftII'
# os.environ['SC2PATH'] = 'C:/Program Files (x86)/StarCraft II'
# os.environ['SC2PATH'] = 'D:/SC2/StarCraft II'

def parse_args():
    parser = ArgumentParser()
    # For competitive game settings
    parser.add_argument(
        "--map_name",
        choices=constants.map_choices,
        help="Map name",
        default='Flat128',
        required=False,
    )
    parser.add_argument(
        "--difficulty",
        choices=constants.difficulty_choices,
        help="Bot difficulty",
        default="Easy",
        required=False,
    )
    parser.add_argument(
        "--model_name", 
        type=str, 
        required=False,
        # default="glm-4.5-flash",
        # default="glm-4-flash",
        # default="105_30B",
        default="73_7B",
        # default="162_30B",
        # default='Qwen2.5-72B-Instruct',
        # default='Qwen2.5-7B-Instruct/checkpoint-7000/',
        # default='deepseek-ai/DeepSeek-R1-Distill-Llama-70B',
        # default='DeepSeek-R1-Distill-Qwen-32B',
        # default='Qwen2.5-72B-Instruct',
        # default='deepseek/deepseek-r1-distill-llama-70b:free',
        # default='deepseek-ai/DeepSeek-R1-Distill-Llama-70B',
        help="Model name"
    )
    parser.add_argument(
        "--ai_build",
        choices=constants.ai_build_choices,
        help="AI build",
        default="RandomBuild",
    )
    # For LLM agent settings
    parser.add_argument(
        "--player_name", type=str, help="Player name", default="default_player"
    )
    parser.add_argument("--enable_rag", default=False, help="Enable RAG agent")
    parser.add_argument("--enable_plan", default=True, help="Enable Plan agent")
    parser.add_argument(
        "--enable_plan_verifier", default=True, help="Enable Plan verifier agent"
    )
    parser.add_argument(
        "--enable_action_verifier",
        default=True,
        help="Enable Action verifier agent",
    )
    # For LLM API service
    parser.add_argument(
        "--base_url",
        type=str,
        # default=os.getenv("BASE_URL", ""),
        # default="https://open.bigmodel.cn/api/paas/v4/",
        default="http://172.18.30.73:8000/v1",
        # default="http://172.18.30.162:8000/v1",
        # default="http://172.18.132.21:30000/v1",
        # default="http://172.18.132.20:8815/v1",
        # default="http://172.18.30.165:8815/v1",
        # default="http://localhost:8815/v1",
        # default="http://172.18.30.165:12001/v1",
        # default="https://openrouter.ai/api/v1",
        help="Base URL for the LLM API service",
    )
    parser.add_argument(
        "--api_key",
        type=str,
        # default=os.getenv("API_KEY", ""),
        default="d3a8265453a945a8a8fce2032325be72.3mQNClIStiQ5Z8pd",
        # default="sk-11223344",
        # default="no-key-required",
        help="API key for the LLM API service",
    )
    # For Race selection
    parser.add_argument(
        "--own_race",
        choices=constants.race_choices,
        default="Terran",
    )
    parser.add_argument(
        "--enemy_race",
        choices=constants.race_choices,
        default="Terran",
    )
    # For data collection and benchmarking
    parser.add_argument(
        "--enable_random_decision_interval",
        action="store_true",
        help="Enable this to improve the data quality while collecting data. Disable this to benchmark the agent.",
    )

    args = parser.parse_args()

    if args.enable_rag:
        raise ValueError(
            "RAG agent is not supported in this version. Please disable it with --enable_rag."
        )
    if args.enable_plan_verifier and not args.enable_plan:
        raise ValueError(
            "Plan verifier requires Plan agent to be enabled. Please enable Plan agent with --enable_plan."
        )
    if not args.base_url or not args.api_key:
        raise ValueError(
            "Base URL and API key must be provided. Please set them using --base_url and --api_key."
        )

    return args


args = parse_args()
log_path = f"logs/{args.player_name}/{args.map_name}_{args.difficulty}_{args.ai_build}_d5"

map_name = args.map_name
difficulty = args.difficulty
model_name = args.model_name
ai_build = args.ai_build
player_name = args.player_name

# Initialize LLM service
llm_config = {
    "model_name": model_name,
    "generation_config": {
        "model_name": model_name,
        "n": 1,
        "max_tokens": 9000,
        "temperature": 0.1,
        "top_p": 0.8,
        "top_k": 20,
        "repetition_penalty": 1.1,
        "presence_penalty": 0.0,
    },
    "llm_client": LLMClient(
        base_url=args.base_url,
        api_key=args.api_key,
    ),
}

# Initialize players
join_player = Computer(
    race=getattr(Race, args.enemy_race),
    difficulty=getattr(Difficulty, difficulty),
    ai_build=getattr(AIBuild, ai_build),
)
ai_player = LLMPlayer(
    config=args,
    player_name=player_name,
    log_path=log_path,
    **llm_config,
)
host_player = Bot(getattr(Race, args.own_race), ai_player)

with open(ai_player.log_path + "/config.json", "w", encoding="utf-8") as f:
    json.dump(vars(args), f, indent=4)

import random

# 生成一个随机种子
# 2**32 - 1 是 4294967295，这是一个常用的种子范围
random_seed_value = random.randint(0, 100)

# 将生成的种子放入您的代码中
res = run_game(
    maps.get(map_name),
    [host_player, join_player],
    realtime=False,
    rgb_render_config=None,
    save_replay_as=ai_player.log_path + "/replay.SC2Replay",
    random_seed=random_seed_value
)

# print(f"本次游戏使用的随机种子是: {random_seed_value}")