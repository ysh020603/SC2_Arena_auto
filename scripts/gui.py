import os
import time
import json
import uuid
import subprocess
import streamlit as st
from dataclasses import dataclass
from typing import Dict, List, Optional
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Game configuration options
MAP_LIST = ["Flat32", "Flat48", "Flat64", "Flat96", "Flat128", "Simple64", "Simple96", "Simple128"]
DIFFICULTY_LIST = [
    "VeryEasy",
    "Easy",
    "Medium",
    "MediumHard",
    "Hard",
    "Harder",
    "VeryHard",
    "CheatVision",
    "CheatMoney",
    "CheatInsane",
]
MODEL_LIST = [
    "Qwen2.5-32B-Instruct",
    "DeepSeek-R1-Distill-Qwen-32B",
    "QwQ-32B",
]
AI_BUILD_LIST = ["RandomBuild", "Rush", "Timing", "Power", "Macro", "Air"]


# Dataclass to track game instances
@dataclass
class GameInstance:
    id: str
    player_name: str
    map_name: str
    difficulty: str
    ai_build: str
    model_name: str
    enable_rag: bool
    enable_plan: bool
    enable_plan_verifier: bool
    enable_action_verifier: bool
    process: Optional[subprocess.Popen] = None
    start_time: Optional[float] = None
    status: str = "Configured"
    log_path: Optional[str] = None


# Initialize session state
if "game_instances" not in st.session_state:
    st.session_state.game_instances = {}


def start_game(game_config: GameInstance):
    """Start a game with the given configuration"""
    log_path = f"logs/{game_config.player_name}/{game_config.map_name}/{game_config.difficulty}/{game_config.ai_build}"
    os.makedirs(log_path, exist_ok=True)
    game_config.log_path = log_path

    # Command to run the game
    cmd = [
        "python",
        "main.py",
        "--map_name",
        game_config.map_name,
        "--difficulty",
        game_config.difficulty,
        "--ai_build",
        game_config.ai_build,
        "--model_name",
        game_config.model_name,
        "--player_name",
        game_config.player_name,
    ]

    # Add optional flags
    if game_config.enable_rag:
        cmd.append("--enable_rag")
    if game_config.enable_plan:
        cmd.append("--enable_plan")
    if game_config.enable_plan_verifier:
        cmd.append("--enable_plan_verifier")
    if game_config.enable_action_verifier:
        cmd.append("--enable_action_verifier")

    # Start the process
    process = subprocess.Popen(cmd)
    game_config.process = process
    game_config.start_time = time.time()
    game_config.status = "Running"

    return game_config


def terminate_game(game_id: str):
    """Terminate a running game"""
    game = st.session_state.game_instances.get(game_id)
    if game and game.process:
        game.process.terminate()
        game.status = "Terminated"
        st.session_state.game_instances[game_id] = game


# Streamlit UI
st.title("星际争霸2博弈决策平台")

# Sidebar for configuration
with st.sidebar:
    st.header("游戏配置")

    # Basic settings
    player_name = st.text_input("智能体名称", value="player")
    map_name = st.selectbox("地图", MAP_LIST, index=0)  # Default to Simple64
    difficulty = st.selectbox("难度", DIFFICULTY_LIST, index=0)  # Default to Medium
    ai_build = st.selectbox("对手风格", AI_BUILD_LIST, index=0)  # Default to RandomBuild
    model_name = st.selectbox("模型", MODEL_LIST, index=0)

    # Enable options
    st.subheader("智能体功能")
    enable_rag = st.checkbox("启用认知检索增强", value=False)
    enable_plan = st.checkbox("启用规划器（无验证器）", value=False)
    enable_plan_verifier = st.checkbox("启用规划器的验证器", value=False)
    enable_action_verifier = st.checkbox("启用执行器的验证器", value=False)

    # Add game button
    if st.button("添加游戏到队列"):
        game_id = str(uuid.uuid4())
        st.session_state.game_instances[game_id] = GameInstance(
            id=game_id,
            player_name=player_name,
            map_name=map_name,
            difficulty=difficulty,
            ai_build=ai_build,
            model_name=model_name,
            enable_rag=enable_rag,
            enable_plan=enable_plan,
            enable_plan_verifier=enable_plan_verifier,
            enable_action_verifier=enable_action_verifier,
        )
        st.success("Game added to queue!")

# Main panel for game management
st.header("Game Instances")

# Convert game instances to DataFrame for display
if st.session_state.game_instances:
    games_data = []
    for game_id, game in st.session_state.game_instances.items():
        duration = "-"
        if game.start_time:
            duration = f"{int(time.time() - game.start_time)}s"

        games_data.append(
            {
                "ID": game_id[:8],
                "Player": game.player_name,
                "Map": game.map_name,
                "Difficulty": game.difficulty,
                "AI Build": game.ai_build,
                "Model": game.model_name,
                "Status": game.status,
                "Duration": duration,
                "Full ID": game_id,  # Hidden column for reference
            }
        )

    df = pd.DataFrame(games_data)

    # Display game instances
    st.dataframe(df.drop(columns=["Full ID"]), hide_index=True)

    # Action buttons
    col1, col2 = st.columns(2)

    with col1:
        selected_games = st.multiselect(
            "Select games to start:",
            options=df[df["Status"] == "Configured"]["Full ID"].tolist(),
            format_func=lambda x: f"{df[df['Full ID'] == x]['Player'].iloc[0]} - {df[df['Full ID'] == x]['Map'].iloc[0]}",
        )

        if st.button("Start Selected Games") and selected_games:
            for game_id in selected_games:
                game_config = st.session_state.game_instances[game_id]
                st.session_state.game_instances[game_id] = start_game(game_config)
            st.success(f"Started {len(selected_games)} game(s)!")
            st.rerun()

    with col2:
        running_games = st.multiselect(
            "Select games to terminate:",
            options=df[df["Status"] == "Running"]["Full ID"].tolist(),
            format_func=lambda x: f"{df[df['Full ID'] == x]['Player'].iloc[0]} - {df[df['Full ID'] == x]['Map'].iloc[0]}",
        )

        if st.button("Terminate Selected Games") and running_games:
            for game_id in running_games:
                terminate_game(game_id)
            st.success(f"Terminated {len(running_games)} game(s)!")
            st.rerun()

    # Game details
    if st.session_state.game_instances:
        st.header("Game Details")
        selected_game = st.selectbox(
            "Select game to view details:",
            options=df["Full ID"].tolist(),
            format_func=lambda x: f"{df[df['Full ID'] == x]['Player'].iloc[0]} - {df[df['Full ID'] == x]['Map'].iloc[0]} ({df[df['Full ID'] == x]['Status'].iloc[0]})",
        )

        if selected_game:
            game = st.session_state.game_instances[selected_game]
            st.json(
                {
                    "Player Name": game.player_name,
                    "Map": game.map_name,
                    "Difficulty": game.difficulty,
                    "AI Build": game.ai_build,
                    "Model": game.model_name,
                    "Status": game.status,
                    "Features": {
                        "RAG": game.enable_rag,
                        "Plan": game.enable_plan,
                        "Plan Verifier": game.enable_plan_verifier,
                        "Action Verifier": game.enable_action_verifier,
                    },
                    "Log Path": game.log_path or "Not started",
                }
            )

            if game.status == "Running" and game.log_path:
                if st.button("Open Logs Folder"):
                    os.startfile(game.log_path)
else:
    st.info("No game instances added. Configure a game using the sidebar.")
